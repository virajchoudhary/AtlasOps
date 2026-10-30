import json
from copy import deepcopy

import pytest
from kubernetes.client import ApiClient, Configuration
from kubernetes.client.exceptions import ApiException
from kubernetes.client.rest import RESTResponse
from urllib3 import HTTPResponse

from agents import action_execution as actions
from agents import verifier
from agents.tools import chaos


@pytest.fixture(params=["current", "legacy"])
def transport(monkeypatch, request):
    configuration = Configuration(host="https://fixture-kubernetes.invalid")
    configuration.api_key["authorization"] = "Bearer fixture-bearer"
    client = ApiClient(configuration)
    responses = []
    requests = []

    def send(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        status, document = responses.pop(0)
        response = RESTResponse(HTTPResponse(
            body=json.dumps(document).encode(),
            status=status,
            headers={"Content-Type": "application/json"},
        ))
        if status >= 400:
            raise ApiException(http_resp=response)
        return response

    monkeypatch.setattr(client, "request", send)
    if request.param == "legacy":
        current_call = client.call_api

        def legacy_call(
            path, method, *, header_params, body=None, auth_settings, response_type,
            _return_http_data_only, _request_timeout,
        ):
            return current_call(
                path, method, header_params=header_params, body=body,
                auth_settings=auth_settings,
                response_types_map={200: response_type, 201: response_type, 202: response_type},
                _return_http_data_only=_return_http_data_only, _request_timeout=_request_timeout,
            )

        monkeypatch.setattr(client, "call_api", legacy_call)
    monkeypatch.setattr(actions, "_new_chaos_api_client", lambda _context: (None, client))
    monkeypatch.setattr(chaos, "_new_chaos_api_client", lambda _context: (None, client))
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "fixture-kind")
    try:
        yield client, responses, requests
    finally:
        client.close()


@pytest.mark.parametrize(("method", "status"), [("GET", 200), ("PATCH", 200), ("DELETE", 202)])
def test_atomic_adapter_decodes_real_client_responses_without_retries(transport, method, status):
    client, responses, requests = transport
    document = {"kind": "Status", "metadata": {"resourceVersion": "43"}}
    body = [{"op": "test", "path": "/metadata/uid", "value": "owned-uid"}]
    responses.append((status, document))

    assert actions._request(client, "/fixture-resource", method, body=body) == document
    assert len(requests) == 1
    assert requests[0]["method"] == method
    assert requests[0]["url"] == "https://fixture-kubernetes.invalid/fixture-resource"
    assert requests[0]["body"] == body
    assert requests[0]["headers"]["authorization"] == "Bearer fixture-bearer"
    assert requests[0]["_request_timeout"] == 30
    if method == "PATCH":
        assert requests[0]["headers"]["Content-Type"] == "application/json-patch+json"
    elif method == "DELETE":
        assert requests[0]["headers"]["Content-Type"] == "application/json"


def test_bound_verifier_reads_the_pinned_deployment_with_the_real_client(transport):
    _client, responses, requests = transport
    resource = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "namespace": "atlasops-boutique", "name": "frontend", "uid": "frontend-uid",
            "resourceVersion": "43",
        },
    }
    responses.append((200, resource))
    target = verifier._OperationalTarget(
        deployment="frontend", namespace="atlasops-boutique", uid="frontend-uid",
        server="https://fixture-kubernetes.invalid", context="fixture-kind",
    )

    assert verifier._query_bound_deployment(target) == {
        "success": True,
        "parsed": resource,
        "cluster_identity": {
            "server": "https://fixture-kubernetes.invalid", "context": "fixture-kind",
        },
    }
    assert len(requests) == 1
    assert requests[0]["method"] == "GET"
    assert requests[0]["url"].endswith(
        "/apis/apps/v1/namespaces/atlasops-boutique/deployments/frontend"
    )
    assert requests[0]["headers"]["authorization"] == "Bearer fixture-bearer"


def test_real_client_prepares_and_acknowledges_the_exact_argo_action(transport, monkeypatch):
    client, responses, requests = transport
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    source = {
        "repoURL": "https://github.com/virajchoudhary/AtlasOps.git",
        "path": "infra/local/boutique", "targetRevision": "a" * 40,
    }
    application = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {
            "name": "atlasops-boutique", "namespace": "argocd", "uid": "app-uid",
            "resourceVersion": "19",
        },
        "spec": {
            "project": "atlasops-boutique", "source": source,
            "destination": {
                "server": "https://kubernetes.default.svc", "namespace": "atlasops-boutique",
            },
        },
        "status": {"history": [{"id": 0, "revision": "a" * 40, "source": source}]},
    }
    workload = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {
            "name": "frontend", "namespace": "atlasops-boutique", "uid": "frontend-uid",
            "resourceVersion": "42",
        },
    }
    labels = {
        "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
        "namespace": "atlasops-boutique", "deployment": "frontend",
    }
    responses.extend([(200, application), (200, workload)])

    action = actions.prepare_action(
        "argocd_rollback", {"app": "atlasops-boutique", "revision": "0"},
        alert={"commonLabels": labels, "alerts": [{"labels": labels}]},
    )
    assert [request["method"] for request in requests] == ["GET", "GET"]
    assert client.configuration.retries == 0
    assert action.target.uid == "app-uid"
    assert action.verification_target.uid == "frontend-uid"
    patch = action.approval_action["patch"]
    assert patch[:2] == [
        {"op": "test", "path": "/metadata/uid", "value": "app-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "19"},
    ]
    assert patch[-1]["value"]["sync"] == {"revision": "a" * 40, "prune": False}
    assert patch[-1]["value"]["info"][0]["name"] == "atlasops-action-id"
    acknowledged = deepcopy(application)
    acknowledged["metadata"]["resourceVersion"] = "20"
    acknowledged["operation"] = patch[-1]["value"]
    responses.append((200, acknowledged))

    result = actions.execute_prepared_action(action)
    assert result["success"] is True
    assert result["action_digest"] == action.digest
    assert result["mutation_receipt"]["target"]["resource_version"] == "20"
    assert [request["method"] for request in requests] == ["GET", "GET", "PATCH"]
    assert requests[-1]["body"] == patch


def test_real_client_conflict_never_retries_the_mutation(transport):
    client, responses, requests = transport
    responses.append((409, {"kind": "Status"}))

    with pytest.raises(ApiException) as error:
        actions._request(client, "/fixture-resource", "PATCH", body=[])
    assert error.value.status == 409
    assert len(requests) == 1

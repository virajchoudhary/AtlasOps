#!/usr/bin/env python3
"""Render and verify the bounded local AI-SRE platform configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_LOCAL = REPO_ROOT / "infra" / "local"
APPLICATION_NAME = "atlasops-boutique"
APPLICATION_NAMESPACE = "argocd"
APPLICATION_PROJECT = "atlasops-boutique"
APPLICATION_REPOSITORY = "https://github.com/virajchoudhary/AtlasOps.git"
APPLICATION_PATH = "infra/local/boutique"
APPLICATION_DESTINATION = "https://kubernetes.default.svc"
BOUTIQUE_NAMESPACE = "atlasops-boutique"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
OPERATOR_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.@+-]{1,128}$")


def load_documents(path: Path) -> list[dict[str, Any]]:
    return [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]


def find_document(
    documents: list[dict[str, Any]], kind: str, name: str
) -> dict[str, Any]:
    for document in documents:
        metadata = document.get("metadata", {})
        if document.get("kind") == kind and metadata.get("name") == name:
            return document
    raise ValueError(f"missing {kind}/{name}")


def validate_source_sha(source_sha: str) -> None:
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("source revision must be a 40-character commit SHA")


def _replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value
    return value


def _write_documents(path: Path, documents: list[dict[str, Any]]) -> None:
    rendered = yaml.safe_dump_all(
        documents,
        explicit_start=True,
        sort_keys=False,
        default_flow_style=False,
    )
    path.write_bytes(rendered.encode("utf-8"))


def render_prometheus_rules(output_dir: Path) -> Path:
    source = REPO_ROOT / "infra" / "kubernetes" / "atlasops-prometheus-rules.yaml"
    documents = load_documents(source)
    rule_document = find_document(
        documents, "PrometheusRule", "atlasops-online-boutique"
    )
    replaced_selectors = 0
    for group in rule_document.get("spec", {}).get("groups", []):
        for rule in group.get("rules", []):
            expression = rule.get("expr")
            if not isinstance(expression, str):
                continue
            selector = 'namespace="default"'
            replaced_selectors += expression.count(selector)
            rule["expr"] = expression.replace(
                selector, f'namespace="{BOUTIQUE_NAMESPACE}"'
            )
    if replaced_selectors != 2:
        raise ValueError(
            "the Online Boutique availability rule must select both replica "
            "metrics in the local workload namespace"
        )

    path = output_dir / "atlasops-prometheus-rules.yaml"
    _write_documents(path, documents)
    return path


def render_platform(args: argparse.Namespace) -> int:
    validate_source_sha(args.source_sha)
    parsed_url = urlsplit(args.vllm_base)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("VLLM_BASE must be an explicit HTTP or HTTPS URL")
    if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        raise ValueError("VLLM_BASE must not embed credentials, a query, or a fragment")
    if not args.agent_model.strip() or any(char.isspace() for char in args.agent_model):
        raise ValueError("AGENT_MODEL must be a non-empty model identifier without spaces")
    if not OPERATOR_IDENTITY_PATTERN.fullmatch(args.operator_identity):
        raise ValueError("operator identity must use letters, digits, dot, underscore, plus, or hyphen")

    replacements = {
        "__ATLASOPS_SOURCE_SHA__": args.source_sha,
        "__ATLASOPS_VLLM_BASE__": args.vllm_base.rstrip("/"),
        "__ATLASOPS_AGENT_MODEL__": args.agent_model,
        "__ATLASOPS_OPERATOR_ID__": args.operator_identity,
    }
    coordinator = _replace_placeholders(
        load_documents(INFRA_LOCAL / "coordinator-local.yaml"), replacements
    )
    application = _replace_placeholders(
        load_documents(INFRA_LOCAL / "argocd-application.yaml"), replacements
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    coordinator_path = args.output_dir / "coordinator-local.yaml"
    application_path = args.output_dir / "argocd-application.yaml"
    _write_documents(coordinator_path, coordinator)
    _write_documents(application_path, application)
    render_prometheus_rules(args.output_dir)
    print(
        "Rendered coordinator, Argo CD Application, and namespace-scoped "
        f"Prometheus rules for source {args.source_sha}."
    )
    return 0


def _application_mismatches(
    application: dict[str, Any], source_sha: str | None
) -> list[str]:
    if source_sha:
        validate_source_sha(source_sha)
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    source = spec.get("source", {})
    destination = spec.get("destination", {})
    sync_policy = spec.get("syncPolicy", {})
    automated = sync_policy.get("automated", {})
    expected = {
        "metadata.name": (metadata.get("name"), APPLICATION_NAME),
        "metadata.namespace": (metadata.get("namespace"), APPLICATION_NAMESPACE),
        "spec.project": (spec.get("project"), APPLICATION_PROJECT),
        "spec.source.repoURL": (source.get("repoURL"), APPLICATION_REPOSITORY),
        "spec.source.path": (source.get("path"), APPLICATION_PATH),
        "spec.destination.server": (
            destination.get("server"),
            APPLICATION_DESTINATION,
        ),
        "spec.destination.namespace": (
            destination.get("namespace"),
            APPLICATION_NAME,
        ),
        "spec.syncPolicy.automated.enabled": (automated.get("enabled"), True),
        "spec.syncPolicy.automated.prune": (automated.get("prune"), False),
        "spec.syncPolicy.automated.selfHeal": (automated.get("selfHeal"), False),
        "spec.syncPolicy.automated.allowEmpty": (automated.get("allowEmpty"), False),
        "spec.syncPolicy.syncOptions": (sync_policy.get("syncOptions", []), []),
    }
    mismatches = [
        field
        for field, (actual, expected_value) in expected.items()
        if actual != expected_value
    ]
    revision = source.get("targetRevision")
    if (
        source_sha is not None
        and revision != source_sha
        or source_sha is None
        and (
            not isinstance(revision, str)
            or not SOURCE_SHA_PATTERN.fullmatch(revision)
        )
    ):
        mismatches.append("spec.source.targetRevision")
    return mismatches


def check_application(args: argparse.Namespace) -> int:
    try:
        application = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ValueError("kubectl did not return a valid Application JSON object") from exc
    if not isinstance(application, dict):
        raise TypeError("kubectl did not return an Application JSON object")
    mismatches = _application_mismatches(application, args.source_sha)
    if mismatches:
        raise ValueError(
            "refusing to adopt incompatible Argo CD Application "
            f"{APPLICATION_NAMESPACE}/{APPLICATION_NAME}; mismatched fields: "
            + ", ".join(mismatches)
        )
    print(f"Existing Application {APPLICATION_NAMESPACE}/{APPLICATION_NAME} matches its owner contract.")
    return 0


def _http_json(
    url: str, *, headers: dict[str, str] | None = None, timeout: float
) -> tuple[int, dict[str, Any] | None]:
    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"request failed for {urlsplit(url).path}: {exc}") from exc


def _prometheus_result(base_url: str, query: str, timeout: float) -> list[dict[str, Any]]:
    query_url = base_url.rstrip("/") + "/api/v1/query?" + urlencode({"query": query})
    status, response = _http_json(query_url, timeout=timeout)
    if status != 200 or not response or response.get("status") != "success":
        raise RuntimeError("Prometheus did not return a successful query response")
    result = response.get("data", {}).get("result")
    if not isinstance(result, list):
        raise TypeError("Prometheus query response did not contain a result list")
    return result


def verify_live(args: argparse.Namespace) -> int:
    validate_source_sha(args.source_sha)
    api_base = args.api_url.rstrip("/")
    unauthenticated_status, _ = _http_json(
        api_base + "/api/operations", timeout=args.request_timeout
    )
    if unauthenticated_status not in {401, 403}:
        raise RuntimeError(
            "unauthenticated GET /api/operations must fail closed with 401 or 403"
        )

    operator_key = args.operator_key_file.read_text(encoding="utf-8").strip()
    if not operator_key:
        raise RuntimeError("generated CI operator key file is empty")
    authenticated_status, _ = _http_json(
        api_base + "/api/operations",
        headers={"X-AtlasOps-Key": operator_key, "Accept": "application/json"},
        timeout=args.request_timeout,
    )
    if authenticated_status != 200:
        raise RuntimeError(
            f"authenticated GET /api/operations returned HTTP {authenticated_status}"
        )
    print("Authenticated GET /api/operations passed; no decision was submitted.")

    deadline = time.monotonic() + args.signal_wait_seconds
    calls_query = (
        'atlasops_boutique_calls_total{service_name="frontend",'
        'span_kind="SPAN_KIND_SERVER"}'
    )
    duration_query = (
        'atlasops_boutique_duration_milliseconds_bucket{service_name="frontend",'
        'span_kind="SPAN_KIND_SERVER"}'
    )
    calls: list[dict[str, Any]] = []
    durations: list[dict[str, Any]] = []
    traces: dict[str, Any] | None = None
    jaeger_services: list[str] = []
    last_error = "no frontend spans have reached Prometheus or Jaeger yet"
    while time.monotonic() < deadline:
        try:
            calls = _prometheus_result(args.prometheus_url, calls_query, args.request_timeout)
            durations = _prometheus_result(
                args.prometheus_url, duration_query, args.request_timeout
            )
            status, services_response = _http_json(
                args.jaeger_url.rstrip("/") + "/api/services",
                timeout=args.request_timeout,
            )
            if status != 200 or not services_response:
                raise RuntimeError("Jaeger service query failed")
            jaeger_services = services_response.get("data", [])
            status, trace_response = _http_json(
                args.jaeger_url.rstrip("/")
                + "/api/traces?"
                + urlencode({"service": "frontend", "limit": "1"}),
                timeout=args.request_timeout,
            )
            if status != 200 or not trace_response:
                raise RuntimeError("Jaeger trace query failed")
            traces = trace_response
            trace_rows = traces.get("data", [])
            if calls and durations and "frontend" in jaeger_services and trace_rows:
                break
            last_error = "waiting for frontend span metrics and a Jaeger trace"
        except (RuntimeError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(args.poll_seconds)
    else:
        raise RuntimeError(f"timed out verifying frontend spans: {last_error}")

    trace = traces["data"][0]
    spans = trace.get("spans", [])
    if not isinstance(trace.get("traceID"), str) or not trace["traceID"] or not spans:
        raise RuntimeError("Jaeger returned a trace without a trace ID or spans")
    first_span = spans[0]
    if not isinstance(first_span.get("startTime"), int):
        raise TypeError("Jaeger returned a trace without a span start time")

    evidence = {
        "classification": "NON_EMPIRICAL_SOFTWARE_FIXTURE",
        "source_sha": args.source_sha,
        "observations": {
            "operator_route": {
                "unauthenticated_status": unauthenticated_status,
                "authenticated_status": authenticated_status,
            },
            "span_metrics": {
                "calls_metric": "atlasops_boutique_calls_total",
                "duration_histogram": "atlasops_boutique_duration_milliseconds_bucket",
                "service_name": "frontend",
                "span_kind": "SPAN_KIND_SERVER",
                "calls_series": len(calls),
                "duration_series": len(durations),
            },
            "jaeger": {
                "frontend_service_present": "frontend" in jaeger_services,
                "frontend_trace_count": len(traces["data"]),
                "trace_id": trace["traceID"],
                "first_span_start_time_unix_microseconds": first_span["startTime"],
            },
        },
    }
    if args.evidence_file:
        args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_file.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        "Observed frontend span-derived metrics "
        "atlasops_boutique_calls_total and "
        "atlasops_boutique_duration_milliseconds_bucket, plus a Jaeger frontend trace."
    )
    print("Evidence classification: NON_EMPIRICAL_SOFTWARE_FIXTURE.")
    return 0


def validate_static() -> int:
    required_files = [
        INFRA_LOCAL / "coordinator-local.yaml",
        INFRA_LOCAL / "argocd-platform.yaml",
        INFRA_LOCAL / "argocd-application.yaml",
        INFRA_LOCAL / "otel-collector.yaml",
        INFRA_LOCAL / "grafana-dashboard.yaml",
        INFRA_LOCAL / "boutique" / "kustomization.yaml",
        REPO_ROOT / "infra" / "kubernetes" / "atlasops-prometheus-rules.yaml",
    ]
    for path in required_files:
        if not path.is_file():
            raise ValueError(f"missing platform manifest: {path.relative_to(REPO_ROOT)}")
    for path in required_files:
        if path.suffix in {".yaml", ".yml"} and path.name != "kustomization.yaml":
            load_documents(path)
    yaml.safe_load((INFRA_LOCAL / "boutique" / "kustomization.yaml").read_text(encoding="utf-8"))
    print("Local platform YAML parsed. No cluster or model was contacted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render", help="render source-bound local manifests")
    render.add_argument("--source-sha", required=True)
    render.add_argument("--vllm-base", required=True)
    render.add_argument("--agent-model", required=True)
    render.add_argument("--operator-identity", required=True)
    render.add_argument("--output-dir", type=Path, required=True)
    render.set_defaults(function=render_platform)

    check = commands.add_parser(
        "check-application",
        help="check an existing Application from kubectl JSON without changing it",
    )
    check.add_argument(
        "--source-sha",
        help="require this source revision; omit to validate an existing owned revision",
    )
    check.set_defaults(function=check_application)

    live = commands.add_parser(
        "verify-live",
        help="verify the bounded authenticated route and trace-derived CI signals",
    )
    live.add_argument("--source-sha", required=True)
    live.add_argument("--api-url", required=True)
    live.add_argument("--operator-key-file", type=Path, required=True)
    live.add_argument("--prometheus-url", required=True)
    live.add_argument("--jaeger-url", required=True)
    live.add_argument("--request-timeout", type=float, default=5.0)
    live.add_argument("--signal-wait-seconds", type=int, default=300)
    live.add_argument("--poll-seconds", type=int, default=5)
    live.add_argument("--evidence-file", type=Path)
    live.set_defaults(function=verify_live)

    static = commands.add_parser(
        "validate-static", help="parse local manifests without contacting a cluster"
    )
    static.set_defaults(function=lambda _args: validate_static())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.function(args)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

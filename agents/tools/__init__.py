"""AtlasOps SRE tool registry.

The registry contains callable wrappers; agent exposure is controlled separately
by :mod:`agents.tool_policy`.
"""

from agents.tools.alertmanager import alertmanager_list_alerts, alertmanager_silence
from agents.tools.argocd import (
    argocd_app_get,
    argocd_app_history,
    argocd_list_apps,
    argocd_rollback,
)
from agents.tools.chaos import chaos_list_experiments, chaos_stop_experiment
from agents.tools.comms import postmortem_draft, slack_post_update
from agents.tools.gcloud_logging import gcloud_logs_read
from agents.tools.jaeger import jaeger_get_trace, jaeger_search
from agents.tools.kubectl import (
    kubectl_describe,
    kubectl_exec,
    kubectl_get,
    kubectl_logs,
    kubectl_rollout,
    kubectl_scale,
    kubectl_top_nodes,
    kubectl_top_pods,
)
from agents.tools.prometheus import promql_query, promql_query_range

# Keep tool imports resilient in local/dev test environments where optional
# cloud SDK extras may be unavailable. This avoids blocking unrelated tools.
try:
    from agents.tools.cloud_monitoring import cloud_monitoring_query
except Exception as exc:  # pragma: no cover - exercised only when dependency missing
    _cloud_monitoring_import_error = str(exc)

    def cloud_monitoring_query(*args, **kwargs):  # type: ignore[no-redef]
        return {
            "success": False,
            "error": f"cloud_monitoring_unavailable: {_cloud_monitoring_import_error}",
        }


TOOL_REGISTRY = {
    "kubectl_get": kubectl_get,
    "kubectl_describe": kubectl_describe,
    "kubectl_logs": kubectl_logs,
    "kubectl_top_pods": kubectl_top_pods,
    "kubectl_top_nodes": kubectl_top_nodes,
    "kubectl_rollout": kubectl_rollout,
    "kubectl_scale": kubectl_scale,
    "kubectl_exec": kubectl_exec,
    "promql_query": promql_query,
    "promql_query_range": promql_query_range,
    "jaeger_search": jaeger_search,
    "jaeger_get_trace": jaeger_get_trace,
    "argocd_list_apps": argocd_list_apps,
    "argocd_app_history": argocd_app_history,
    "argocd_rollback": argocd_rollback,
    "argocd_app_get": argocd_app_get,
    "gcloud_logs_read": gcloud_logs_read,
    "cloud_monitoring_query": cloud_monitoring_query,
    "alertmanager_silence": alertmanager_silence,
    "alertmanager_list_alerts": alertmanager_list_alerts,
    "chaos_list_experiments": chaos_list_experiments,
    "chaos_stop_experiment": chaos_stop_experiment,
    "slack_post_update": slack_post_update,
    "postmortem_draft": postmortem_draft,
}

REGISTERED_TOOLS = frozenset(TOOL_REGISTRY)

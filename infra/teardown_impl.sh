#!/usr/bin/env bash
# Implementation for infra/teardown.sh. Do not invoke this file directly.
set -euo pipefail

readonly DEFAULT_REGION="us-central1"
readonly DEFAULT_CLUSTER="atlasops"
readonly DELETE_ACK_VALUE="DELETE_ATLASOPS_DEVELOPMENT_RESOURCES"
readonly PUBSUB_TOPICS=("AtlasOps-checkout-events" "AtlasOps-alerts")
readonly PUBSUB_SUBSCRIPTIONS=("AtlasOps-checkout-sub" "AtlasOps-alerts-sub")

ATLASOPS_ENABLE_CLOUD_SQL="${ATLASOPS_ENABLE_CLOUD_SQL:-false}"
ATLASOPS_ENABLE_PUBSUB="${ATLASOPS_ENABLE_PUBSUB:-false}"
ATLASOPS_ENABLE_ARTIFACT_REGISTRY="${ATLASOPS_ENABLE_ARTIFACT_REGISTRY:-false}"
ATLASOPS_ENABLE_CLOUD_BUILD="${ATLASOPS_ENABLE_CLOUD_BUILD:-false}"

usage() {
  cat <<'EOF'
Usage:
  bash infra/teardown.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --check
  bash infra/teardown.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --apply

Modes:
  --check  Read-only inventory/dry run. Deletes nothing.
  --apply  Delete only exact, AtlasOps-labeled targets after acknowledgement.

Optional:
  ATLASOPS_GKE_ZONE=<zone in REGION>       default: <REGION>-a
  ATLASOPS_ENABLE_CLOUD_SQL=false          deferred; never deleted here
  ATLASOPS_ENABLE_PUBSUB=false             delete exact opt-in resources
  ATLASOPS_ENABLE_ARTIFACT_REGISTRY=false  deferred; never deleted here
  ATLASOPS_ENABLE_CLOUD_BUILD=false        deferred; never deleted here

Apply-only acknowledgement:
  ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_DEVELOPMENT_RESOURCES
EOF
}

fail() { echo "ERROR: $*" >&2; exit 1; }

parse_bool() {
  local name="$1" value
  value="${!name:-false}"
  case "$value" in
    true|false) printf -v "$name" '%s' "$value" ;;
    *) fail "$name must be exactly 'true' or 'false' (received '$value')." ;;
  esac
}

parse_arguments() {
  MODE=""; POSITIONAL=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --check|--apply)
        [[ -z "$MODE" ]] || fail "Specify exactly one mode."
        MODE="$arg"
        ;;
      -h|--help) usage; exit 0 ;;
      --*) fail "Unknown option: $arg" ;;
      *) POSITIONAL+=("$arg") ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage >&2; fail "An explicit --check or --apply mode is required."; }
  ((${#POSITIONAL[@]} >= 1 && ${#POSITIONAL[@]} <= 3)) || { usage >&2; fail "Expected PROJECT_ID and at most REGION and CLUSTER_NAME."; }
  PROJECT="${POSITIONAL[0]}"
  REGION="${POSITIONAL[1]:-$DEFAULT_REGION}"
  CLUSTER="${POSITIONAL[2]:-$DEFAULT_CLUSTER}"
  ZONE="${ATLASOPS_GKE_ZONE:-${REGION}-a}"
}

validate_inputs() {
  command -v gcloud >/dev/null 2>&1 || fail "Required command 'gcloud' is not available."
  [[ "$PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || fail "Invalid GCP project ID: '$PROJECT'."
  [[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || fail "Invalid GCP region: '$REGION'."
  [[ "$ZONE" =~ ^${REGION}-[a-z]$ ]] || fail "Zone '$ZONE' is not in region '$REGION'."
  [[ "$CLUSTER" =~ ^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$ ]] || fail "Invalid cluster name: '$CLUSTER'."
  parse_bool ATLASOPS_ENABLE_CLOUD_SQL
  parse_bool ATLASOPS_ENABLE_PUBSUB
  parse_bool ATLASOPS_ENABLE_ARTIFACT_REGISTRY
  parse_bool ATLASOPS_ENABLE_CLOUD_BUILD
  [[ "$ATLASOPS_ENABLE_CLOUD_SQL" == false ]] || fail "Cloud SQL is deferred and is not owned by this lifecycle."
  [[ "$ATLASOPS_ENABLE_ARTIFACT_REGISTRY" == false ]] || fail "Artifact Registry is deferred and is not owned by this lifecycle."
  [[ "$ATLASOPS_ENABLE_CLOUD_BUILD" == false ]] || fail "Cloud Build is deferred and is not owned by this lifecycle."
  if [[ "$MODE" == --apply ]]; then
    [[ "${ATLASOPS_TEARDOWN_ACK:-}" == "$DELETE_ACK_VALUE" ]] || fail "--apply requires ATLASOPS_TEARDOWN_ACK=$DELETE_ACK_VALUE."
  fi
}

owned_labels() { [[ "$1" == *"managed-by=atlasops"* && "$1" == *"environment=development"* ]]; }

preflight() {
  local account project_id
  account="$(gcloud auth list --filter='status:ACTIVE' --limit=1 --format='value(account)')"
  [[ -n "$account" ]] || fail "No active gcloud account is available."
  echo "PREFLIGHT: active account found (identity value not printed)."
  project_id="$(gcloud projects describe "$PROJECT" --project="$PROJECT" --format='value(projectId)')" || fail "Project is inaccessible."
  [[ "$project_id" == "$PROJECT" ]] || fail "Project verification returned '$project_id'."
  gcloud compute regions describe "$REGION" --project="$PROJECT" --format='value(name)' >/dev/null || fail "Region is uninspectable."
  gcloud compute zones describe "$ZONE" --project="$PROJECT" --format='value(name,region)' >/dev/null || fail "Zone is uninspectable."
}

inspect_cluster() {
  local rows name location labels
  CLUSTER_PRESENT=false
  rows="$(gcloud container clusters list --project="$PROJECT" --filter="name=$CLUSTER" --format='csv[no-heading](name,location)')" || fail "Clusters are uninspectable."
  while IFS=',' read -r name location; do
    [[ -n "$name" ]] || continue
    [[ "$name" == "$CLUSTER" ]] || continue
    [[ "$location" == "$ZONE" ]] || fail "Cluster '$CLUSTER' exists in '$location', not '$ZONE'; refusing deletion."
    CLUSTER_PRESENT=true
  done <<< "$rows"
  if [[ "$CLUSTER_PRESENT" == true ]]; then
    labels="$(gcloud container clusters describe "$CLUSTER" --zone="$ZONE" --project="$PROJECT" --format='value(resourceLabels)')"
    owned_labels "$labels" || fail "Cluster '$CLUSTER' lacks the exact AtlasOps development ownership labels; refusing deletion."
  fi
}

inspect_pubsub() {
  local resource labels actual_topic topic subscription index expected_topic
  for topic in "${PUBSUB_TOPICS[@]}"; do
    resource="$(gcloud pubsub topics list --project="$PROJECT" --filter="name:projects/$PROJECT/topics/$topic" --format='value(name)')" || fail "Topics are uninspectable."
    if grep -Fxq "projects/$PROJECT/topics/$topic" <<< "$resource"; then
      labels="$(gcloud pubsub topics describe "$topic" --project="$PROJECT" --format='value(labels)')"
      owned_labels "$labels" || fail "Topic '$topic' is not AtlasOps-owned; refusing deletion."
    fi
  done
  for index in "${!PUBSUB_SUBSCRIPTIONS[@]}"; do
    subscription="${PUBSUB_SUBSCRIPTIONS[$index]}"
    expected_topic="${PUBSUB_TOPICS[$index]}"
    resource="$(gcloud pubsub subscriptions list --project="$PROJECT" --filter="name:projects/$PROJECT/subscriptions/$subscription" --format='value(name)')" || fail "Subscriptions are uninspectable."
    if grep -Fxq "projects/$PROJECT/subscriptions/$subscription" <<< "$resource"; then
      labels="$(gcloud pubsub subscriptions describe "$subscription" --project="$PROJECT" --format='value(labels)')"
      owned_labels "$labels" || fail "Subscription '$subscription' is not AtlasOps-owned; refusing deletion."
      actual_topic="$(gcloud pubsub subscriptions describe "$subscription" --project="$PROJECT" --format='value(topic)')"
      [[ "$actual_topic" == "projects/$PROJECT/topics/$expected_topic" ]] || \
        fail "Subscription '$subscription' does not target '$expected_topic'; refusing deletion."
    fi
  done
}

print_summary() {
  cat <<EOF

=== AtlasOps teardown target summary ===
Mode:              $MODE
Project:           $PROJECT
Region:            $REGION
Zone:              $ZONE
Cluster:           $CLUSTER
Cluster present:   $CLUSTER_PRESENT
Cloud SQL:         SKIPPED / NOT OWNED
Pub/Sub:           $([[ "$ATLASOPS_ENABLE_PUBSUB" == true ]] && echo EXACT OPT-IN TARGETS || echo SKIPPED)
Pub/Sub topics:    $([[ "$ATLASOPS_ENABLE_PUBSUB" == true ]] && echo "${PUBSUB_TOPICS[*]}" || echo NONE)
Pub/Sub subs:      $([[ "$ATLASOPS_ENABLE_PUBSUB" == true ]] && echo "${PUBSUB_SUBSCRIPTIONS[*]}" || echo NONE)
Linkerd:           SKIPPED / NOT OWNED
Artifact Registry: SKIPPED / NOT OWNED
Cloud Build:       SKIPPED / NOT OWNED
========================================
EOF
}

delete_pubsub_if_present() {
  local kind="$1" name="$2" resource
  if [[ "$kind" == subscription ]]; then
    resource="$(gcloud pubsub subscriptions list --project="$PROJECT" --filter="name:projects/$PROJECT/subscriptions/$name" --format='value(name)')"
    if grep -Fxq "projects/$PROJECT/subscriptions/$name" <<< "$resource"; then
      gcloud pubsub subscriptions delete "$name" --project="$PROJECT" --quiet
      echo "PUBSUB: subscription '$name' deleted."
    else
      echo "PUBSUB: subscription '$name' absent; SKIPPED."
    fi
  else
    resource="$(gcloud pubsub topics list --project="$PROJECT" --filter="name:projects/$PROJECT/topics/$name" --format='value(name)')"
    if grep -Fxq "projects/$PROJECT/topics/$name" <<< "$resource"; then
      gcloud pubsub topics delete "$name" --project="$PROJECT" --quiet
      echo "PUBSUB: topic '$name' deleted."
    else
      echo "PUBSUB: topic '$name' absent; SKIPPED."
    fi
  fi
}

main() {
  parse_arguments "$@"; validate_inputs; preflight; inspect_cluster
  [[ "$ATLASOPS_ENABLE_PUBSUB" == false ]] || inspect_pubsub
  print_summary
  if [[ "$MODE" == --check ]]; then
    echo "CHECK COMPLETE: deletion inventory validated; no resources were deleted."
    return
  fi
  echo "APPLY: explicit destructive acknowledgement accepted."
  if [[ "$ATLASOPS_ENABLE_PUBSUB" == true ]]; then
    delete_pubsub_if_present subscription "${PUBSUB_SUBSCRIPTIONS[0]}"
    delete_pubsub_if_present subscription "${PUBSUB_SUBSCRIPTIONS[1]}"
    delete_pubsub_if_present topic "${PUBSUB_TOPICS[0]}"
    delete_pubsub_if_present topic "${PUBSUB_TOPICS[1]}"
  else
    echo "PUBSUB: SKIPPED."
  fi
  if [[ "$CLUSTER_PRESENT" == true ]]; then
    gcloud container clusters delete "$CLUSTER" --zone="$ZONE" --project="$PROJECT" --quiet
    echo "CLUSTER: '$CLUSTER' deleted from '$ZONE'."
  else
    echo "CLUSTER: exact target absent; SKIPPED."
  fi
  cat <<EOF

TEARDOWN COMPLETE for the exact selected AtlasOps targets.
This does not prove billing has stopped. Inspect project resources and Cloud
Billing reports explicitly after teardown, for example:
  gcloud asset search-all-resources --scope=projects/$PROJECT --project=$PROJECT
  gcloud billing projects describe $PROJECT --project=$PROJECT
EOF
}

main "$@"

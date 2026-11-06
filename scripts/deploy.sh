#!/usr/bin/env bash
# Deploys the evaluator Function in the right order: core, private network, core with VNet, code.
#
#   1. infra/main.bicep            -> Function, plan, private storage, identity, role assignments
#   2. infra/private-network.bicep -> VNet, private endpoint and private DNS for the storage account
#   3. infra/main.bicep (again)    -> Function VNet integration, using the subnet from step 2
#   4. scripts/package.sh          -> outputs/function.zip
#   5. az functionapp deployment source config-zip --build-remote true
#
# Reuses an existing resource group and Foundry account. Never creates keys: the evaluator API key
# comes from $EVALUATOR_API_KEY and travels to Azure in a temporary parameters file (mode 600) that
# is deleted at the end. Nothing is written to Git.
#
# Requires: bash, Azure CLI (signed in) and Python 3 (JSON handling and the zip).
#
# Usage:
#   export EVALUATOR_API_KEY="$(openssl rand -base64 48)"
#   scripts/deploy.sh --check-only      # no Azure call: compile templates, validate, build package
#   scripts/deploy.sh --what-if         # preview step 1, create nothing
#   scripts/deploy.sh                   # deploy
#
# Options:
#   --parameters FILE          default infra/main.parameters.json
#   --network-parameters FILE  default infra/private-network.parameters.json
#   --rubrics FILE             default examples/rubrics.json (sent as EVALUATORS_CONFIG_JSON)
#   --check-only               compile, validate parameters and build the package; no Azure call
#   --what-if                  what-if for step 1 only
#   --skip-publish             deploy the infrastructure but not the code
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
parameters="infra/main.parameters.json"
network_parameters="infra/private-network.parameters.json"
rubrics="examples/rubrics.json"
check_only=0
what_if=0
skip_publish=0

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --parameters) parameters="${2:?--parameters needs a file}"; shift ;;
    --network-parameters) network_parameters="${2:?--network-parameters needs a file}"; shift ;;
    --rubrics) rubrics="${2:?--rubrics needs a file}"; shift ;;
    --check-only) check_only=1 ;;
    --what-if) what_if=1 ;;
    --skip-publish) skip_publish=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

die() { echo "Error: $*" >&2; exit 1; }

py=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
    py="$candidate"
    break
  fi
done
[[ -n "$py" ]] || die "Python 3 is required."
command -v az >/dev/null 2>&1 || die "Azure CLI (az) is required."

native() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
in_repo() { case "$1" in /*|[A-Za-z]:*) printf '%s' "$1" ;; *) printf '%s/%s' "$root" "$1" ;; esac; }

main_bicep="$root/infra/main.bicep"
network_bicep="$root/infra/private-network.bicep"
main_params="$(in_repo "$parameters")"
net_params="$(in_repo "$network_parameters")"
rubrics_file="$(in_repo "$rubrics")"

tmpfiles=()
cleanup() { if [[ ${#tmpfiles[@]} -gt 0 ]]; then rm -f "${tmpfiles[@]}"; fi; }
trap cleanup EXIT

az_() { az "$@" --only-show-errors; }

for file in "$main_params" "$net_params" "$rubrics_file"; do
  [[ -f "$file" ]] || die "Missing file: $file. Copy the matching *.example.json and fill it in (it stays out of Git)."
done

read_param() {
  "$py" -c 'import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8-sig"))["parameters"]
print(data[sys.argv[2]]["value"])' "$(native "$main_params")" "$1"
}

placeholders="$("$py" -c 'import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8-sig"))["parameters"]
print(", ".join(k for k, v in data.items() if "YOUR-" in str(v.get("value")) or "YOURNEW" in str(v.get("value"))))' "$(native "$main_params")")"
[[ -z "$placeholders" ]] || die "Replace the placeholders in $parameters: $placeholders"

rg="$(read_param resourceGroupName)"
location="$(read_param location)"
function_name="$(read_param functionName)"

SECRET_FILE=""
write_secret_parameters() {
  local subnet="${1:-}"
  SECRET_FILE="$(mktemp "${TMPDIR:-/tmp}/decision-eval-XXXXXX.json")"
  chmod 600 "$SECRET_FILE"
  tmpfiles+=("$SECRET_FILE")
  SUBNET_ID="$subnet" RUBRICS_FILE="$(native "$rubrics_file")" "$py" - "$(native "$SECRET_FILE")" <<'PY'
import json
import os
import sys

parameters = {
    "evaluatorApiKey": {"value": os.environ["EVALUATOR_API_KEY"]},
    "evaluatorConfigJson": {"value": open(os.environ["RUBRICS_FILE"], encoding="utf-8-sig").read()},
}
if os.environ["SUBNET_ID"]:
    parameters["integrationSubnetId"] = {"value": os.environ["SUBNET_ID"]}
document = {
    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
    "contentVersion": "1.0.0.0",
    "parameters": parameters,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(document, handle)
PY
}

echo "== Compiling Bicep templates"
az_ bicep build --file "$(native "$main_bicep")" --stdout >/dev/null
az_ bicep build --file "$(native "$network_bicep")" --stdout >/dev/null

if [[ $check_only -eq 1 ]]; then
  echo "== Building the package"
  "$root/scripts/package.sh" >/dev/null
  echo "Check passed: templates compile, parameters filled, package built. No Azure call was made."
  exit 0
fi

[[ -n "${EVALUATOR_API_KEY:-}" && ${#EVALUATOR_API_KEY} -ge 32 ]] \
  || die 'Set $EVALUATOR_API_KEY to a random value of at least 32 characters (it is never written to Git), for example: export EVALUATOR_API_KEY="$(openssl rand -base64 48)"'
export EVALUATOR_API_KEY

echo "== Azure context: $(az_ account show --query '{name:name, user:user.name}' -o tsv)"

if [[ $what_if -eq 1 ]]; then
  echo "== What-if: core deployment (nothing is created)"
  write_secret_parameters ""
  az_ deployment sub what-if --name decision-eval-core --location "$location" \
    --template-file "$(native "$main_bicep")" \
    --parameters "@$(native "$main_params")" "@$(native "$SECRET_FILE")"
  echo "The network what-if needs the storage account created by step 1, so it is skipped."
  exit 0
fi

echo "== 1/5 Core: Function, plan, private storage, identity, roles"
write_secret_parameters ""
az_ deployment sub create --name decision-eval-core --location "$location" \
  --template-file "$(native "$main_bicep")" \
  --parameters "@$(native "$main_params")" "@$(native "$SECRET_FILE")" \
  --query properties.outputs.endpoint.value -o tsv >/dev/null

echo "== 2/5 Private network for the storage account"
subnet_id="$(az_ deployment group create --resource-group "$rg" --name decision-eval-network \
  --template-file "$(native "$network_bicep")" \
  --parameters "@$(native "$net_params")" \
  --query properties.outputs.integrationSubnetId.value -o tsv | head -n1 | tr -d '\r')"
[[ -n "$subnet_id" ]] || die "The network deployment returned no integration subnet."

echo "== 3/5 Core again: Function VNet integration"
write_secret_parameters "$subnet_id"
endpoint="$(az_ deployment sub create --name decision-eval-core --location "$location" \
  --template-file "$(native "$main_bicep")" \
  --parameters "@$(native "$main_params")" "@$(native "$SECRET_FILE")" \
  --query properties.outputs.endpoint.value -o tsv | head -n1 | tr -d '\r')"

if [[ $skip_publish -eq 1 ]]; then
  echo "Infrastructure ready. Endpoint: $endpoint. Code not published (--skip-publish)."
  exit 0
fi

echo "== 4/5 Package"
zip_path="$("$root/scripts/package.sh" | tail -n1)"

echo "== 5/5 Publish with remote dependency installation"
az_ functionapp deployment source config-zip --resource-group "$rg" --name "$function_name" \
  --src "$(native "$zip_path")" --build-remote true >/dev/null

echo
echo "Deployed. Endpoint: $endpoint"
echo "Check:     curl $endpoint/health"
echo 'Next:      register the evaluators (dry-run first), see "Use in Foundry" in the README.'
echo 'Note:      sending the package is not proof that the Function is live; check /health and run one evaluation.'

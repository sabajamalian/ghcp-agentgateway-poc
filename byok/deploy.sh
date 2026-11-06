#!/usr/bin/env bash
# Publish a bearer-authenticated model API with Azure-managed HTTPS ingress.
set -euo pipefail
umask 077
SUBSCRIPTION="${SUBSCRIPTION:?Set SUBSCRIPTION to your Azure subscription ID}"
AZURE_RESOURCE="${AZURE_RESOURCE:?Set AZURE_RESOURCE to your model resource name}"
AZURE_MODEL="${AZURE_MODEL:?Set AZURE_MODEL to your deployment/model identifier}"
FOUNDRY_API_KEY="${FOUNDRY_API_KEY:?Set FOUNDRY_API_KEY to the upstream provider key}"
export FOUNDRY_API_KEY
RESOURCE_TYPE="${RESOURCE_TYPE:-openAI}"
MODEL_ALIAS="${MODEL_ALIAS:-foundry-team-model}"
RG="${RG:-rg-agentgateway-byok}"
LOCATION="${LOCATION:-centralus}"
NAME="${NAME:-agw-byok}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE="$ROOT/../.secrets/byok"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
mkdir -p "$STATE"
chmod 700 "$STATE"
if [[ ! -s "$STATE/gateway-api-key" ]]; then
  openssl rand -hex 32 >"$STATE/gateway-api-key"
fi
ARGS=(--resource "$AZURE_RESOURCE" --model "$AZURE_MODEL"
  --resource-type "$RESOURCE_TYPE" --alias "$MODEL_ALIAS"
  --gateway-key-file "$STATE/gateway-api-key" --output "$STATE/config.json")
if [[ -n "${AZURE_PROJECT:-}" ]]; then
  ARGS+=(--project "$AZURE_PROJECT")
fi
python3 "$ROOT/configure.py" "${ARGS[@]}"
PARAMETERS="$(mktemp "$STATE/parameters.XXXXXX")"
trap 'rm -f "$PARAMETERS"' EXIT
python3 - "$STATE/config.json" "$PARAMETERS" "$NAME" <<'PY'
import json
import os
from pathlib import Path
import sys

values = {
    "name": sys.argv[3],
    "configJson": Path(sys.argv[1]).read_text(),
    "foundryApiKey": os.environ["FOUNDRY_API_KEY"],
}
Path(sys.argv[2]).write_text(json.dumps({
    "parameters": {key: {"value": value} for key, value in values.items()},
}))
PY
az provider register --subscription "$SUBSCRIPTION" -n Microsoft.App --wait -o none
az group create --subscription "$SUBSCRIPTION" -n "$RG" -l "$LOCATION" -o none
az deployment group create --subscription "$SUBSCRIPTION" -g "$RG" -n agentgateway-byok \
  -f "$ROOT/main.bicep" -p "@$PARAMETERS" --query properties.outputs -o json \
  >"$STATE/outputs.json"
BASE_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["baseUrl"]["value"])' "$STATE/outputs.json")"
printf 'header = "Authorization: Bearer %s"\n' "$(cat "$STATE/gateway-api-key")" |
  curl --config - --fail --silent --show-error --noproxy '*' \
    --retry 30 --retry-delay 5 --retry-all-errors --retry-max-time 180 \
    --connect-timeout 5 --max-time 15 --output /dev/null "$BASE_URL/models"
echo "BYOK base URL: $BASE_URL"
echo "Client model ID: $MODEL_ALIAS"
echo "Gateway key (keep private): $STATE/gateway-api-key"

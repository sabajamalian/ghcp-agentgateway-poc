#!/usr/bin/env bash
# Deploy agentgateway as a GitHub Copilot egress proxy to Azure Container Instances.
# Backs up live UI edits before redeployment. --apply-config uses the local YAML.
set -euo pipefail
umask 077

APPLY_CONFIG=false
case "${1:-}" in
  "") ;;
  --apply-config) APPLY_CONFIG=true ;;
  *) echo "Usage: $0 [--apply-config]" >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [--apply-config]" >&2
  exit 2
fi

SUBSCRIPTION="${SUBSCRIPTION:?Set SUBSCRIPTION to your Azure subscription ID}"
RG="${RG:-rg-agentgateway-copilot}"
LOCATION="${LOCATION:-centralus}"
NAME="${NAME:-agw-copilot}"
PROXY_USER="${PROXY_USER:-copilot}"
UI_USER="${UI_USER:-admin}"

# Scope client proxy settings to Copilot, not Azure management commands.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

ROOT="$(cd "$(dirname "$0")" && pwd)"
SECRETS="$ROOT/.secrets"
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"

# Proxy credentials: generated once, hashed with apr1 for agentgateway.
if [[ ! -f "$SECRETS/htpasswd" ]]; then
  PASS="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
  printf '%s:%s\n' "$PROXY_USER" "$(openssl passwd -apr1 "$PASS")" > "$SECRETS/htpasswd"
  printf 'PROXY_USER=%s\nPROXY_PASS=%s\n' "$PROXY_USER" "$PASS" > "$SECRETS/proxy-credentials.env"
  chmod 600 "$SECRETS"/*
fi

if [[ ! -s "$SECRETS/ui-credentials.env" ]]; then
  if [[ ! "$UI_USER" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "UI_USER must contain only letters, numbers, dots, underscores, or hyphens." >&2
    exit 2
  fi
  UI_PASS="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
  printf 'UI_USER=%s\nUI_PASS=%s\n' "$UI_USER" "$UI_PASS" > "$SECRETS/ui-credentials.env"
fi
source "$SECRETS/ui-credentials.env"
command -v htpasswd >/dev/null || { echo "htpasswd is required to hash the UI password." >&2; exit 1; }
if [[ ! -s "$SECRETS/ui-htpasswd" ]]; then
  printf '%s\n' "$UI_PASS" | htpasswd -niBC 10 "$UI_USER" > "$SECRETS/ui-htpasswd"
fi
# Stable, globally unique DNS label per subscription + RG.
DNS_LABEL="${DNS_LABEL:-$NAME-$(printf '%s' "$SUBSCRIPTION$RG" | shasum | cut -c1-6)}"
ORIGIN_HOST="$DNS_LABEL.$LOCATION.azurecontainer.io"

RENEW_ORIGIN=false
if [[ ! -s "$SECRETS/ui-origin-ca.crt" ]] || \
   ! openssl x509 -checkend 8380800 -noout -in "$SECRETS/ui-origin-ca.crt" >/dev/null; then
  echo "Creating the UI origin CA."
  openssl req -x509 -newkey rsa:3072 -nodes -days 365 -sha256 \
    -subj '/CN=agentgateway UI origin CA' \
    -config <(printf '[req]\ndistinguished_name=dn\n[dn]\n[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n') \
    -extensions v3_ca \
    -keyout "$SECRETS/ui-origin-ca.key" -out "$SECRETS/ui-origin-ca.crt" 2>/dev/null
  RENEW_ORIGIN=true
fi
if [[ "$RENEW_ORIGIN" == true || ! -s "$SECRETS/ui-origin.crt" ]] || \
   ! openssl x509 -checkend 604800 -noout -in "$SECRETS/ui-origin.crt" >/dev/null; then
  echo "Issuing the UI origin certificate."
  openssl req -new -newkey rsa:2048 -nodes \
    -subj "/CN=$ORIGIN_HOST" \
    -keyout "$SECRETS/ui-origin.key" -out "$SECRETS/ui-origin.csr" 2>/dev/null
  openssl x509 -req -in "$SECRETS/ui-origin.csr" \
    -CA "$SECRETS/ui-origin-ca.crt" -CAkey "$SECRETS/ui-origin-ca.key" -CAcreateserial \
    -days 90 -sha256 -out "$SECRETS/ui-origin.crt" \
    -extfile <(printf 'basicConstraints=CA:FALSE\nsubjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$ORIGIN_HOST")
fi
openssl verify -CAfile "$SECRETS/ui-origin-ca.crt" "$SECRETS/ui-origin.crt"

az provider register --subscription "$SUBSCRIPTION" -n Microsoft.ContainerInstance --wait -o none
az provider register --subscription "$SUBSCRIPTION" -n Microsoft.App --wait -o none
az group create --subscription "$SUBSCRIPTION" -n "$RG" -l "$LOCATION" -o none

CONFIG_SOURCE="${CONFIG_FILE:-$ROOT/config.yaml}"
EXISTING="$(az container list --subscription "$SUBSCRIPTION" -g "$RG" --query "[?name=='$NAME-aci'].name" -o tsv)"
if [[ "$APPLY_CONFIG" == false && -n "$EXISTING" ]]; then
  CONFIG_SOURCE="$(bash "$ROOT/export-config.sh")"
  printf 'Backed up live UI configuration: %s\n' "$CONFIG_SOURCE"
fi

OUTPUT_TMP="$(mktemp "$SECRETS/deployment-outputs.XXXXXX")"
PARAMETER_FILE="$(mktemp "$SECRETS/deployment-parameters.XXXXXX")"
trap 'rm -f "$OUTPUT_TMP" "$PARAMETER_FILE"' EXIT
python3 - "$SECRETS" "$CONFIG_SOURCE" "$NAME" "$DNS_LABEL" "$UI_USER" "$APPLY_CONFIG" "$PARAMETER_FILE" <<'PY'
import json
import pathlib
import sys

secrets = pathlib.Path(sys.argv[1])
values = {
    "name": sys.argv[3],
    "dnsLabel": sys.argv[4],
    "uiUser": sys.argv[5],
    "replaceConfig": sys.argv[6] == "true",
    "configYaml": pathlib.Path(sys.argv[2]).read_text(),
    "htpasswd": (secrets / "htpasswd").read_text(),
    "uiPasswordHash": (secrets / "ui-htpasswd").read_text().strip().split(":", 1)[1],
    "uiOriginCert": (secrets / "ui-origin.crt").read_text(),
    "uiOriginKey": (secrets / "ui-origin.key").read_text(),
    "uiOriginCaCert": (secrets / "ui-origin-ca.crt").read_text(),
}
with open(sys.argv[7], "w") as stream:
    json.dump({"parameters": {k: {"value": v} for k, v in values.items()}}, stream)
PY
az deployment group create --subscription "$SUBSCRIPTION" \
  -g "$RG" -n agentgateway-copilot \
  -f "$ROOT/main.bicep" \
  -p "@$PARAMETER_FILE" \
  --query properties.outputs -o json | tee "$OUTPUT_TMP"
mv "$OUTPUT_TMP" "$SECRETS/deployment-outputs.json"
rm -f "$PARAMETER_FILE"
trap - EXIT

UI_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["uiUrl"]["value"])' "$SECRETS/deployment-outputs.json")"
curl --fail --silent --show-error --noproxy '*' \
  --retry 60 --retry-delay 5 --retry-all-errors --retry-max-time 300 \
  --connect-timeout 5 --max-time 15 --user "$UI_USER:$UI_PASS" \
  --output /dev/null "$UI_URL"
printf 'UI ready: %s\n' "$UI_URL"
echo "Use --apply-config to replace runtime rules with config.yaml."

#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
STATE="$ROOT/../.secrets/inspection-poc"
SUBSCRIPTION="${SUBSCRIPTION:?Set SUBSCRIPTION to your Azure subscription ID}"
RG="${RG:-rg-agentgateway-copilot-inspection-poc}"
LOCATION="${LOCATION:-centralus}"
VM_SIZE="${VM_SIZE:-Standard_D2as_v7}"
SSH_PORT="${SSH_PORT:-443}"
if [[ "$RG" == "${MANAGED_PROXY_RG:-rg-agentgateway-copilot}" ]]; then
  echo 'Refusing to target the existing non-inspecting deployment.' >&2
  exit 2
fi
mkdir -p "$STATE"
chmod 700 "$STATE"
az_clean() {
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy az "$@" --subscription "$SUBSCRIPTION"
}
if [[ ! -s "$STATE/id_ed25519" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C 'agentgateway-inspection-poc' -f "$STATE/id_ed25519"
fi
OPERATOR_IP="${OPERATOR_IP:-$(curl --noproxy '*' -fsS --max-time 20 https://api.ipify.org)}"
python3 - "$OPERATOR_IP" <<'PY'
import ipaddress
import sys
if not isinstance(ipaddress.ip_address(sys.argv[1]), ipaddress.IPv4Address):
    raise SystemExit("Set OPERATOR_IP to the operator's public IPv4 address.")
PY
az_clean group create -n "$RG" -l "$LOCATION" \
  --tags purpose=copilot-model-inspection-poc -o none
az_clean deployment group create -g "$RG" -n agentgateway-inspection \
  -f "$ROOT/main.bicep" -p operatorCidr="$OPERATOR_IP/32" \
  sshPublicKey="$(cat "$STATE/id_ed25519.pub")" vmSize="$VM_SIZE" sshPort="$SSH_PORT" \
  --query properties.outputs -o json >"$STATE/outputs.json"
IP="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sshIp"]["value"])' "$STATE/outputs.json")"
VM="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["vmName"]["value"])' "$STATE/outputs.json")"
# Retrieve the host key over authenticated Azure management, not ssh-keyscan.
az_clean vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
  --scripts "set -e
printf 'Port $SSH_PORT\n' >/etc/ssh/sshd_config.d/50-pilot-port.conf
systemctl disable --now ssh.socket
systemctl enable ssh
systemctl restart ssh
cat /etc/ssh/ssh_host_ed25519_key.pub" \
  --query 'value[0].message' -o tsv >"$STATE/host-key-response"
python3 - "$STATE" "$IP" "$SSH_PORT" <<'PY'
from pathlib import Path
import sys
root, ip, port = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
keys = [line.strip() for line in (root / "host-key-response").read_text().splitlines()
        if line.startswith("ssh-ed25519 ")]
if len(keys) != 1:
    raise SystemExit(
        f"VM SSH setup or host-key verification failed; inspect {root / 'host-key-response'}."
    )
(root / "known_hosts").write_text(f"[{ip}]:{port} " + keys[0] + "\n")
(root / "ssh-port").write_text(port + "\n")
PY
SSH=(-i "$STATE/id_ed25519" -o BatchMode=yes -o StrictHostKeyChecking=yes
  -o UserKnownHostsFile="$STATE/known_hosts" -o ConnectTimeout=15)
ssh "${SSH[@]}" -p "$SSH_PORT" "pilot@$IP" 'mkdir -p /home/pilot/inspection-poc && chmod 700 /home/pilot/inspection-poc'
scp "${SSH[@]}" -P "$SSH_PORT" "$ROOT/bootstrap.sh" "$ROOT/configure.py" "$ROOT/project_logs.py" "$ROOT/retention.py" \
  "pilot@$IP:/home/pilot/inspection-poc/"
ssh "${SSH[@]}" -p "$SSH_PORT" "pilot@$IP" 'sudo bash /home/pilot/inspection-poc/bootstrap.sh'
ssh "${SSH[@]}" -p "$SSH_PORT" "pilot@$IP" 'sudo cat /etc/inspection-poc/ca.crt' >"$STATE/pilot-ca.crt"
ssh "${SSH[@]}" -p "$SSH_PORT" "pilot@$IP" 'sudo cat /etc/inspection-poc/image-digest' >"$STATE/image-digest"
printf 'SUBSCRIPTION=%s\nRG=%s\n' "$SUBSCRIPTION" "$RG" >"$STATE/deployment.env"
echo "Pilot deployed in $RG. Start the isolated local forward with inspection-poc/tunnel.sh."

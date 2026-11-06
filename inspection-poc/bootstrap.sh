#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io python3 openssl
systemctl enable --now docker
install -d -m 700 /etc/inspection-poc
install -d -m 755 /opt/inspection-poc
install -d -m 2750 -g syslog /var/log/inspection-poc
find /var/log/inspection-poc -maxdepth 1 -type f -name 'metadata-*.jsonl' -exec chgrp syslog {} +
install -m 644 "$ROOT/configure.py" "$ROOT/project_logs.py" "$ROOT/retention.py" /opt/inspection-poc/
if [[ ! -s /etc/inspection-poc/ca.key ]]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 14 -sha256 \
    -subj '/CN=Copilot inspection PoC CA' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -keyout /etc/inspection-poc/ca.key -out /etc/inspection-poc/ca.crt 2>/dev/null
fi
python3 /opt/inspection-poc/configure.py --output /etc/inspection-poc/config.json
chgrp 65532 /etc/inspection-poc /etc/inspection-poc/ca.key /etc/inspection-poc/ca.crt /etc/inspection-poc/config.json
chmod 750 /etc/inspection-poc
chmod 640 /etc/inspection-poc/ca.key /etc/inspection-poc/ca.crt /etc/inspection-poc/config.json
IMAGE='cr.agentgateway.dev/agentgateway:v1.7.0'
docker pull "$IMAGE"
DIGEST="$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}')"
printf '%s\n' "$DIGEST" >/etc/inspection-poc/image-digest
docker run --rm --log-driver none --user 65532:65532 --entrypoint /app/agentgateway \
  -v /etc/inspection-poc:/etc/inspection-poc:ro "$DIGEST" \
  -f /etc/inspection-poc/config.json --validate-only
cat >/etc/systemd/system/inspection-poc.service <<EOF
[Unit]
Description=Metadata-only Copilot inspection pilot
After=docker.service network-online.target
Requires=docker.service

[Service]
ExecStartPre=-/usr/bin/docker rm -f inspection-poc
ExecStart=/usr/bin/python3 /opt/inspection-poc/project_logs.py /usr/bin/docker run --rm --name inspection-poc --log-driver none --user 65532:65532 --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m -p 127.0.0.1:3128:3128 -v /etc/inspection-poc:/etc/inspection-poc:ro $DIGEST -f /etc/inspection-poc/config.json
ExecStop=/usr/bin/docker stop -t 10 inspection-poc
Restart=on-failure
RestartSec=5
UMask=0027

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/inspection-poc-retention.service <<'EOF'
[Unit]
Description=Expire pilot metadata older than seven days
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/inspection-poc/retention.py --vm
EOF
cat >/etc/systemd/system/inspection-poc-retention.timer <<'EOF'
[Unit]
Description=Daily inspection pilot metadata retention
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat >/etc/ssh/sshd_config.d/60-inspection-poc.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
AllowTcpForwarding local
GatewayPorts no
PermitOpen 127.0.0.1:3128
EOF
sshd -t
systemctl reload ssh
systemctl daemon-reload
systemctl enable --now inspection-poc-retention.timer
systemctl enable inspection-poc
systemctl restart inspection-poc
for _ in $(seq 1 60); do
  if python3 -c 'import socket; socket.create_connection(("127.0.0.1",3128),timeout=1).close()' 2>/dev/null; then
    echo 'Pilot listener ready on VM loopback only.'
    exit 0
  fi
  sleep 2
done
echo 'Pilot listener failed to become ready.' >&2
exit 1

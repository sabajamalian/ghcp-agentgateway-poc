#!/usr/bin/env python3
"""Run synthetic CLI tests and persist only metadata, never captured output."""

import argparse
import time
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Model selectable under the current enterprise policy")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--direct", action="store_true", help="Functional baseline without the pilot proxy")
    args = parser.parse_args()
    if not 1 <= args.count <= 10:
        raise SystemExit("--count must be between 1 and 10")
    root = Path(__file__).resolve().parent
    state = root.parent / ".secrets" / "inspection-poc"
    os.umask(0o077)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    version = subprocess.check_output(["copilot", "--version"], env=env, text=True).splitlines()[0]
    servers = json.loads(subprocess.check_output(
        ["copilot", "mcp", "list", "--json"], env=env, text=True, stderr=subprocess.DEVNULL,
    ))["mcpServers"]
    if not isinstance(servers, dict):
        raise SystemExit("Unexpected MCP list format; refusing to run without MCP isolation.")
    outputs = json.loads((state / "outputs.json").read_text())
    ssh = [
        "ssh", "-p", (state / "ssh-port").read_text().strip(),
        "-i", str(state / "id_ed25519"), "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o",
        f"UserKnownHostsFile={state / 'known_hosts'}",
        "-o", "ConnectTimeout=10", f"pilot@{outputs['sshIp']['value']}",
    ]
    if not args.direct:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 23128), timeout=3):
                pass
        except OSError as error:
            raise SystemExit("Start inspection-poc/tunnel.sh first; the pilot tunnel is not reachable.") from error
        env.update({
            "HTTPS_PROXY": "http://127.0.0.1:23128",
            "HTTP_PROXY": "http://127.0.0.1:23128",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "NODE_EXTRA_CA_CERTS": str(state / "pilot-ca.crt"),
        })
    evidence = state / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    for _ in range(args.count):
        run_id = str(uuid.uuid4())
        if not args.direct:
            context = {"run_id": run_id, "client": "cli", "client_version": version}
            # Global Secure Access relays SSH through rotating egress IPs, so some
            # connections reset before key exchange. Retry those instead of failing.
            for attempt in range(8):
                pushed = subprocess.run(
                    [*ssh, "sudo tee /etc/inspection-poc/run.json >/dev/null"],
                    input=json.dumps(context), text=True, timeout=25, capture_output=True,
                )
                if pushed.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise SystemExit("could not set run context on the pilot VM over SSH")
        command = [
            "copilot", "-p", "Do not use tools. Reply with exactly: INSPECTION_POC_OK",
            "--model", args.model, "--trust-folder", "--no-custom-instructions", "--no-ask-user",
            "--disable-builtin-mcps", "--disable-client-telemetry",
            "--deny-tool=shell", "--deny-tool=write", "--deny-tool=read",
            "--log-level", "none", "--sync-session=off", "--no-color",
            "--stream", "on", "--silent",
        ]
        for server in servers:
            command.extend(["--disable-mcp-server", server])
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            with tempfile.TemporaryDirectory(prefix="copilot-inspection-") as directory:
                result = subprocess.run(
                    command, cwd=directory, env=env, text=True, capture_output=True, timeout=180,
                )
            text = result.stdout + result.stderr
            row = {
                "run_id": run_id, "timestamp": started, "client": "cli",
                "client_version": version, "expected_selection": args.model,
                "mode": "direct" if args.direct else "inspection",
                "exit_code": result.returncode,
                "synthetic_reply_seen": result.returncode == 0 and result.stdout.strip() == "INSPECTION_POC_OK",
                "administrator_override_notice": "administrator-managed setting" in text,
                "failure_category": (
                    "none" if result.returncode == 0 else
                    "workspace_trust" if "trust" in text.lower() and "folder" in text.lower() else
                    "argument_error" if "unexpected argument" in text or "unknown option" in text else
                    "authentication" if any(word in text.lower() for word in ("not logged in", "unauthorized", "authentication")) else
                    "network" if any(word in text.lower() for word in ("connection", "connect", "fetch failed", "network")) else
                    "unclassified_cli_error"
                ),
                "timeout": False,
            }
        except subprocess.TimeoutExpired:
            row = {
                "run_id": run_id, "timestamp": started, "client": "cli",
                "client_version": version, "expected_selection": args.model,
                "mode": "direct" if args.direct else "inspection", "timeout": True,
                "synthetic_reply_seen": False,
            }
        (evidence / f"cli-{run_id}.json").write_text(json.dumps(row, indent=2) + "\n")
        print(json.dumps(row))
        if not row["synthetic_reply_seen"]:
            raise SystemExit("Synthetic invocation failed; stop and diagnose before repeating.")


if __name__ == "__main__":
    main()

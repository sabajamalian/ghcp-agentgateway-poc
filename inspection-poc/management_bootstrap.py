#!/usr/bin/env python3
"""Bootstrap public pilot code through Azure management when SSH is unavailable."""

import argparse
import json
import os
from pathlib import Path
import subprocess


def azure(arguments):
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    return subprocess.check_output(["az", *arguments], env=env, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", default=os.environ.get("SUBSCRIPTION"))
    parser.add_argument("--resource-group", default=os.environ.get("RG", "rg-agentgateway-copilot-inspection-poc"))
    args = parser.parse_args()
    if not args.subscription:
        parser.error("Set SUBSCRIPTION or pass --subscription.")
    root = Path(__file__).resolve().parent
    state = root.parent / ".secrets" / "inspection-poc"
    outputs = json.loads((state / "outputs.json").read_text())
    vm = outputs["vmName"]["value"]
    files = {name: (root / name).read_text()
             for name in ("configure.py", "project_logs.py", "bootstrap.sh", "retention.py")}
    script = (
        "set -eu\npython3 - <<'PILOT_PUBLIC_CODE_TRANSFER'\n"
        "import json\nfrom pathlib import Path\n"
        f"files = json.loads({json.dumps(json.dumps(files))})\n"
        "root = Path('/opt/inspection-poc-upload')\n"
        "root.mkdir(mode=0o700, exist_ok=True)\n"
        "for name, content in files.items():\n"
        "    (root / name).write_text(content)\n"
        "PILOT_PUBLIC_CODE_TRANSFER\n"
        "bash /opt/inspection-poc-upload/bootstrap.sh\n"
    )
    arguments = [
        "vm", "run-command", "invoke", "--subscription", args.subscription,
        "-g", args.resource_group, "-n", vm, "--command-id", "RunShellScript",
        "--query", "value[0].message", "-o", "tsv",
    ]
    result = azure([*arguments, "--scripts", script])
    print(result)
    if "Pilot listener ready on VM loopback only." not in result:
        raise SystemExit("Bootstrap did not report readiness. Check the VM service before testing.")
    result = azure([
        *arguments, "--scripts",
        "cat /etc/inspection-poc/ca.crt; cat /etc/inspection-poc/image-digest",
    ])
    begin = result.find("-----BEGIN CERTIFICATE-----")
    end = result.find("-----END CERTIFICATE-----")
    if begin < 0 or end < begin:
        raise SystemExit("Unable to retrieve the public pilot CA certificate.")
    (state / "pilot-ca.crt").write_text(result[begin:end + len("-----END CERTIFICATE-----")] + "\n")
    digest = [line for line in result.splitlines()
              if line.startswith("cr.agentgateway.dev/agentgateway@sha256:")]
    if len(digest) != 1:
        raise SystemExit("Unable to retrieve the pinned image digest.")
    (state / "image-digest").write_text(digest[0] + "\n")
    print("Management bootstrap ready. Client routing still requires a working SSH tunnel.")


if __name__ == "__main__":
    main()

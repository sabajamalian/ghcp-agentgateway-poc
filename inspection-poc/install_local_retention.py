#!/usr/bin/env python3
"""Install a per-user daily cleanup job for this pilot's metadata evidence only."""

import os
from pathlib import Path
import plistlib
import subprocess


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    directory = Path.home() / "Library" / "LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "com.github.agentgateway.inspection-retention.plist"
    if target.exists():
        existing = plistlib.loads(target.read_bytes())
        if existing.get("ProgramArguments") != ["/usr/bin/python3", str(root / "retention.py")]:
            raise SystemExit("A retention job for another checkout already exists; refusing to replace it.")
    else:
        target.write_bytes(plistlib.dumps({
            "Label": "com.github.agentgateway.inspection-retention",
            "ProgramArguments": ["/usr/bin/python3", str(root / "retention.py")],
            "StartInterval": 86400,
            "RunAtLoad": True,
            "ProcessType": "Background",
        }))
        target.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    label = domain + "/com.github.agentgateway.inspection-retention"
    result = subprocess.run(["launchctl", "print", label], capture_output=True)
    if result.returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["/usr/bin/python3", str(root / "retention.py")], check=True)
    print("Daily seven-day cleanup installed for this pilot's local CLI evidence.")

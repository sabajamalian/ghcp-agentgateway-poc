import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DeploymentTests(unittest.TestCase):
    def run_deploy(self, fail_enable=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "inspection-poc"
            scripts.mkdir()
            shutil.copy2(Path(__file__).with_name("deploy.sh"), scripts / "deploy.sh")

            state = root / ".secrets" / "inspection-poc"
            state.mkdir(parents=True)
            (state / "id_ed25519").write_text("test private key")
            (state / "id_ed25519.pub").write_text("ssh-ed25519 test-public-key\n")
            (root / "host.pub").write_text("ssh-ed25519 test-host-key\n")

            tools = root / "bin"
            tools.mkdir()

            def executable(name, contents):
                path = tools / name
                path.write_text(contents)
                path.chmod(0o755)

            executable("az", r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

args = sys.argv[1:]
root = Path(os.environ["TEST_DEPLOY_ROOT"])
if args[:2] == ["group", "create"]:
    pass
elif args[:3] == ["deployment", "group", "create"]:
    print(json.dumps({"sshIp": {"value": "192.0.2.1"}, "vmName": {"value": "test-vm"}}))
elif args[:3] == ["vm", "run-command", "invoke"]:
    script = args[args.index("--scripts") + 1]
    script = script.replace("/etc/ssh/sshd_config.d/50-pilot-port.conf", shlex.quote(str(root / "port.conf")))
    script = script.replace("/etc/ssh/ssh_host_ed25519_key.pub", shlex.quote(str(root / "host.pub")))
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    print("Enable succeeded: \n[stdout]")
    print(result.stdout, end="")
    print("\n[stderr]")
    print(result.stderr, end="")
else:
    raise SystemExit("Unexpected az command")
""")
            executable("systemctl", r"""#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with (Path(os.environ["TEST_DEPLOY_ROOT"]) / "systemctl.log").open("a") as log:
    print(" ".join(sys.argv[1:]), file=log)
if sys.argv[1:] == ["enable", "ssh"] and os.environ.get("TEST_FAIL_ENABLE") == "1":
    print("simulated enable failure", file=sys.stderr)
    raise SystemExit(1)
""")
            for name in ("ssh", "scp"):
                executable(name, "#!/bin/sh\nexit 0\n")

            env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                       OPERATOR_IP="192.0.2.2", RG="test-inspection-poc",
                       SUBSCRIPTION="test-subscription", SSH_PORT="443",
                       TEST_DEPLOY_ROOT=str(root))
            if fail_enable:
                env["TEST_FAIL_ENABLE"] = "1"
            else:
                env.pop("TEST_FAIL_ENABLE", None)
            result = subprocess.run(["bash", str(scripts / "deploy.sh")],
                                    env=env, capture_output=True, text=True)
            commands = (root / "systemctl.log").read_text().splitlines()
            known_hosts = state / "known_hosts"
            host_key = known_hosts.read_text() if known_hosts.exists() else None
            response = (state / "host-key-response").read_text()
            return result, commands, host_key, response

    def test_enables_ssh_and_pins_the_host_key(self):
        result, commands, host_key, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, ["disable --now ssh.socket", "enable ssh", "restart ssh"])
        self.assertEqual(host_key, "[192.0.2.1]:443 ssh-ed25519 test-host-key\n")

    def test_enable_failure_stops_deployment(self):
        result, commands, host_key, response = self.run_deploy(fail_enable=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, ["disable --now ssh.socket", "enable ssh"])
        self.assertIsNone(host_key)
        self.assertIn("VM SSH setup or host-key verification failed", result.stderr)
        self.assertIn("simulated enable failure", response)


if __name__ == "__main__":
    unittest.main()

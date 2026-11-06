# Model metadata through selective inspection

This separate Azure VM deployment captures model identifiers while Copilot continues to use each developer's GitHub authentication and GitHub-managed models. It changes the TLS trust boundary; read [architecture](../docs/architecture.md) and [operations](../docs/operations.md) first.

## Deploy

You need Azure CLI, Python 3, OpenSSL, SSH/SCP, `curl`, and a Copilot client. Load your subscription/region settings from the root environment example.

```sh
bash inspection-poc/deploy.sh
```

Default resources live in `rg-agentgateway-copilot-inspection-poc`. `VM_SIZE`, `LOCATION`, `RG`, and `SSH_PORT` can be overridden. Select a VM size available in your region.

The script generates a dedicated SSH key in `.secrets/inspection-poc/`, provisions the VM/monitoring resources, obtains its SSH host key through Azure management, and pins that key for later connections. No password or CA signing key is included in the repository.

Only SSH is exposed on the configured port. The gateway binds to VM loopback, and the NSG restricts SSH to the operator's public IPv4 address.

If a corporate egress service changes the apparent source IP, set `OPERATOR_IP` to the address that reaches Azure. Do not open public proxy ingress to work around an SSH failure. The source-IP restriction supplements SSH key authentication.

The default managed-proxy group is protected from accidental reuse. If you renamed that group, also supply `MANAGED_PROXY_RG` when deploying inspection.

## Start the tunnel

In a separate terminal:

```sh
bash inspection-poc/tunnel.sh
```

It exposes `http://127.0.0.1:23128` and verifies the pinned SSH host key. Keep it open.

If the connection exits, rerun it. If SSH is refused after VM deallocation/restart, check that the VM is running and SSH is enabled/active. If it resets, inspect the NSG/source IP before changing access.

If initial SSH bootstrap is unavailable, public bootstrap code can be transferred through Azure management:

```sh
python3 inspection-poc/management_bootstrap.py
```

This installs the gateway but does not replace the need for a working client tunnel.

## Copilot CLI

Use an enterprise-permitted model:

```sh
python3 inspection-poc/run_cli.py --model YOUR_ALLOWED_MODEL --count 1
```

The harness uses a harmless reply-only prompt, a temporary working directory, process-scoped proxy/CA settings, disabled MCP integrations, and no exported prompt/response content. Read its CLI flags against your installed client version if that version changes.

The helper records `client`, `client_version`, and `run_id` for correlation. They are labels supplied by the harness, not authenticated identity.

To launch your own CLI invocation, with the tunnel already running:

```sh
HTTPS_PROXY=http://127.0.0.1:23128 \
HTTP_PROXY=http://127.0.0.1:23128 \
NO_PROXY=localhost,127.0.0.1,::1 \
NODE_EXTRA_CA_CERTS="$PWD/.secrets/inspection-poc/pilot-ca.crt" \
  copilot -p 'Reply with exactly: PROXY_OK'
```

Copilot may keep ordinary local session history. The gateway's metadata-only output does not change the client's own storage behavior.

## Desktop local sessions

Do this from a terminal outside the app. Quitting the app ends its running sessions.

1. Start the tunnel in a separate terminal.
2. Fully quit GitHub Copilot.
3. From the repository root, run:

   ```sh
   bash inspection-poc/desktop_run.sh
   ```

The macOS launcher checks the tunnel, supplies process-scoped proxy and CA trust, and prints a run ID. Use local sessions and select models your policy permits.

It does not install an OS-wide CA or change global proxy settings. Quit and relaunch normally to leave the pilot. Remote/cloud sessions need their own host routing.

## Read the logs

Open the new group's Log Analytics workspace and run the query in [docs/logs.md](../docs/logs.md). [queries.kql](queries.kql) adds per-run and deduplicated inference views.

The model comes from the decoded request, not the harness's selected-model value. The native-format AI pipeline handles compressed inference bodies; passthrough routes preserve GitHub authentication/support traffic.

The projection persists metadata only. `requested_model_raw` and `response_model_raw` remain separate, and capture status is separate from HTTP success.

## Source checks

From the repository root:

```sh
python3 -m unittest discover -s inspection-poc -p 'test_*.py'
bash -n inspection-poc/deploy.sh inspection-poc/bootstrap.sh inspection-poc/tunnel.sh
```

The real-binary fixture suite requires Agentgateway v1.7.0 and a separate fixture CA. Install the `zstd` CLI to include its compression cases; those cases are skipped when it is absent. Never copy the deployed inspection signing key to your laptop.

For an Apple Silicon Mac:

```sh
mkdir -p .secrets/fixture
gh release download v1.7.0 --repo agentgateway/agentgateway \
  --pattern agentgateway-darwin-arm64 --pattern agentgateway-darwin-arm64.sha256 \
  --dir .secrets/fixture
cd .secrets/fixture
EXPECTED="$(awk '{print $1}' agentgateway-darwin-arm64.sha256)"
printf '%s  %s\n' "$EXPECTED" agentgateway-darwin-arm64 | shasum -a 256 -c -
chmod +x agentgateway-darwin-arm64
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj '/CN=Local fixture CA' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -keyout ca.key -out ca.crt
cd ../..
python3 inspection-poc/test_gateway.py \
  --gateway .secrets/fixture/agentgateway-darwin-arm64 --ca-dir .secrets/fixture
```

For Linux, select the matching release asset. Fixtures exercise local behavior without calling GitHub or an LLM.

## Retention and rollback

VM metadata cleanup is installed by bootstrap. A macOS local-evidence cleanup job is optional:

```sh
python3 inspection-poc/install_local_retention.py
```

It targets recognized evidence filenames in this checkout, not credentials or arbitrary files. The cleanup label is `com.github.agentgateway.inspection-retention`.

Close the tunnel and relaunch clients normally to leave inspection. Review resources before deletion; stopping the gateway alone does not stop VM compute charges. See [operations](../docs/operations.md).

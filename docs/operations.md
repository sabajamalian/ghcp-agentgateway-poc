# Operating boundaries

## Prerequisites and permissions

The deployment scripts create resource groups and register resource providers. Subscription-level Contributor access is a straightforward lab setup. In a restricted subscription, ask an administrator to register providers and create the groups, then assign only the permissions needed for their resources.

You also need available regional VM capacity for the inspection pilot, Container Apps availability for the HTTPS endpoints, and a Copilot account whose policies permit the chosen models.

Enterprise custom-model registration needs the appropriate enterprise/organization role. Foundry deployment and provider-key access require separate Azure permissions.

## Mandatory routing needs network enforcement

Distributing a proxy setting alone does not stop direct access.

A production enterprise design needs managed endpoint settings, approved CA distribution if inspecting, firewall/SWG rules that deny client-direct Copilot destinations, approved gateway egress, and off-network coverage through VPN or SSE.

Avoid a PAC `DIRECT` fallback for mandatory traffic. Check fail-closed behavior when the gateway is unavailable. Review all required destinations against the current [Copilot allowlist](https://docs.github.com/en/copilot/reference/copilot-allowlist-reference).

[Subscription-based routing](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/manage-access/manage-network-access) distinguishes individual, business, and enterprise traffic. It is not a replacement for authentication or full destination coverage.

Hosted GitHub workloads run outside the endpoint network. Their network and model governance require a separate design.

## Private client ingress

The original CONNECT reproduction has public ACI ingress. HTTP proxy basic-auth credentials are not encrypted on the client-to-proxy hop, even though Copilot's tunneled payload is TLS-encrypted.

Use private ingress over a trusted VPN or another approved encrypted transport before serving users. The inspection pilot's SSH tunnel avoids public proxy ingress, but is a single-operator access method.

The BYOK API uses Azure-managed public HTTPS because GitHub must reach the enterprise-provider endpoint. Its inbound key and upstream Foundry key are separate. Review stronger identity, source restrictions, key rotation, and private upstream connectivity for production.

## Configuration and recovery

The ACI CONNECT gateway uses an `emptyDir` volume for writable UI configuration. It is not durable cloud storage.

Run `bash managed-proxy/export-config.sh` before a manual restart, stop, or replacement. Normal `deploy.sh` updates first export the live configuration; `--apply-config` intentionally replaces runtime rules with the local configuration.

Exports can contain secrets. They remain in the local, ignored `.secrets/` directory. See [Azure's emptyDir lifecycle](https://learn.microsoft.com/en-us/azure/container-instances/container-instances-volume-emptydir).

For the inspection VM, systemd starts both SSH and the gateway at boot. SSH host keys are obtained over authenticated Azure management and pinned locally. The CA has no automatic renewal; inspect its lifetime and redistribute the public certificate after renewal.

## Retention

Azure workspace/table retention and the VM/local cleanup policies are defined in [main.bicep](../inspection-poc/main.bicep), [bootstrap.sh](../inspection-poc/bootstrap.sh), and [retention.py](../inspection-poc/retention.py).

Cleanup targets only recognized metadata/evidence files. It does not rotate keys, remove certificates, or erase Copilot's own local history. Cleanup waits while the host is asleep or deallocated.

## Costs and availability

ACI, the inspection VM, disks, public IPs, Container Apps, monitoring ingestion, and model calls can incur charges. There is no cost-saving claim in this repository.

Stopping a gateway process does not stop VM compute billing. Deallocate the VM to stop its compute charges, while remembering that disks, IPs, and monitoring can still cost money.

These deployments are single-instance labs. Production needs an availability design, capacity planning, health monitoring, durable configuration, and rollout/rollback procedures.

## Leave or remove the PoC

To stop using inspection, close the tunnel and relaunch clients without its proxy/CA environment. Do not disable TLS verification to recover connectivity.

List each resource group's contents before deleting it. Then explicitly delete only the groups you created for this PoC:

```sh
az resource list --subscription "$SUBSCRIPTION" --resource-group YOUR_POC_GROUP -o table
az group delete --subscription "$SUBSCRIPTION" --name YOUR_POC_GROUP
```

The CLI asks for confirmation. No cleanup script in this repository automatically deletes a resource group.

Review local credentials and retention jobs separately. Never reuse the inspection CA or lab SSH key as enterprise infrastructure.

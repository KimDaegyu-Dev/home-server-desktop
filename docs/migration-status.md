# Current migration status

Updated: 2026-11-09

## Platform

- WSL2 Ubuntu has cgroup v2 and systemd enabled.
- K3s `v1.37.2+k3s1` is active; node `home-server-wsl` is Ready.
- Argo CD `v3.6.1`, Vault Secrets Operator chart `1.6.2`, and Tailscale Operator chart `1.104.4` are installed.
- Docker Desktop was stopped by request and was not started during this work.
- Vault is installed by Argo CD as a Helm-managed single-node Raft StatefulSet with a `local-path` PVC. The Raft snapshot restore is complete; the Vault and Vault Ingress Applications are `Synced` and `Healthy`.

## Vault transition

- Vault has been restored into the K3s Helm release from the source Raft snapshot. It is initialized, unsealed with the original Shamir shares, and reports the original Vault cluster ID.
- A Raft snapshot is stored outside Git at `/home/eorbr/vault-raft-pre-migration-20261109.snap`, mode `0600` (27,824 bytes).
- Original init material is `/home/eorbr/vault-init.json`, mode `0600`. Do not print it, commit it, or replace the original unseal shares during migration.
- The old systemd Vault package and service are removed, along with `/var/lib/vault`, `/etc/vault.d`, the old CoreDNS/host aliases, TLS bridge, and `vault-access` namespace. The guarded Ansible playbook completed successfully after checking Vault unsealed status, VSO readiness, and the new Ingress address.
- Auto Unseal is not configured. Vault continues to use Shamir 3-of-5 and requires manual unseal after a Vault pod or WSL restart.
- VSO connects to `http://vault.vault.svc:8200`; the Vault Ingress points directly to the in-cluster Service. Argo CD has pruned the former NGINX bridge and disabled Vault Injector resources.
- A WSL2 MTU of 1280 caused Tailscale HTTPS handshakes to time out even though ping and backend checks worked. `prepare-host.yml` now enables `home-server-wsl-mtu.service` to set WSL `eth0` to 1500 before K3s starts; Flannel and the Tailscale proxy Pods use MTU 1450. From Windows, both MagicDNS Ingress URLs now return HTTP 200.
- The protected Raft snapshot and original init material remain outside Git at the paths above. See [`vault.md`](vault.md) for the migration sequence and recovery details; do not repeat the restore against the active cluster.

## Tailscale prerequisite

The OAuth credential permits `tag:k8s-operator`, and the tailnet policy delegates `tag:k8s` ownership to `tag:k8s-operator`:

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"]
}
```

The Tailscale Operator is running and the Ingress resources have addresses:

- Argo CD: `https://argocd.tail5389e9.ts.net`
- Vault: `https://vault.tail5389e9.ts.net`

## Identity and monitoring roadmap

- Local Argo CD and Vault logins were confirmed before starting Authentik. GitOps configuration now adds Authentik, a dedicated PostgreSQL Helm release, VSO-managed secrets, and a Tailscale Ingress. Deployment awaits the Vault bootstrap step documented in [`authentik-sso.md`](authentik-sso.md).
- Authentik will become the OIDC provider for native integrations (Argo CD, Vault, Grafana) and the proxy provider for compatible apps. Keep local break-glass access until each integration is verified.
- Prometheus/Grafana, read-only Grafana MCP, and Docker workload migrations remain subsequent stages. Existing Docker service inventory and staged data are listed below.

## Data copied without starting Docker

The original directories remain. File-level staging copies were made while the source containers were offline:

| Data | Original | Copy | Check |
|---|---|---|---|
| Hindsight PostgreSQL 16 | `/home/eorbr/ai-memory/hindsight/postgres` | `/var/lib/home-server-data/imports/2026-11-08/hindsight-postgres` | `diff -qr` reported no file differences; 70 MB |
| AI Memory | `/home/eorbr/ai-memory/ai-memory/data` | `/var/lib/home-server-data/imports/2026-11-08/ai-memory-data` | `diff -qr` reported no file differences; 94 MB |

These are staging copies, not yet mounted by Kubernetes. Keep originals until each restored application is checked.

## Compose inventory observed

- Snowball: `knowledge-api`, `collector`, Fuseki, Neo4j, Qdrant. The two custom knowledge services build from the local Snowball workspace. Named-volume data remains inside Docker Desktop's data disk.
- AI Learning Companion: `companion-api`, `watch-skill`, `asr-worker`. It mounts the Docker socket and model volume; the Docker socket integration needs redesign for Kubernetes.
- Hindsight: API plus PostgreSQL. PostgreSQL data is staged; the service needs Vault-provided LLM and database credentials.
- AI Memory: image `docker.io/akitaonrails/ai-memory@sha256:6d107f95f7c4a79d3b2de789baf2013a3b20ddabdf12da83ca4c56f85b84b0c8`, port 49374, with staged data; its auth token must come from Vault.
- Other stopped projects and images (including QEV, Beacon, LLM Wiki, OpenClaw, and OMP) were not active Compose workloads selected for the first migration pass.

## Remaining migration work

1. Configure Authentik's first admin and validate OIDC for Argo CD and Vault while keeping local recovery access.
2. Deploy Prometheus/Grafana with a selected retention and storage budget, then add service-specific metric exporters or ServiceMonitors.
3. Deploy a read-only Grafana MCP transport and verify its caller authentication and Grafana service-account permissions.
4. Publish custom images to GHCR and pin immutable tags or digests. Snowball currently has no Git remote.
5. Recover Docker named-volume exports after Docker Desktop is repaired; preserve the existing staged data copies and originals until each restore is verified.
6. Migrate services in waves, starting with workloads whose data is already staged; defer Docker-socket-dependent workloads until redesigned for Kubernetes.

See [`migration.md`](migration.md) for the repeatable per-application cutover sequence. The Docker VHDX and named volumes have not been deleted or intentionally modified.

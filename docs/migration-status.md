# Current migration status

Updated: 2026-11-09

## Platform

- WSL2 Ubuntu has cgroup v2 and systemd enabled.
- K3s `v1.37.2+k3s1` is active; node `home-server-wsl` is Ready.
- Argo CD `v3.6.1`, Vault Secrets Operator chart `1.6.2`, and Tailscale Operator chart `1.104.4` are installed.
- Docker Desktop was stopped by request and was not started during this work.
- Git now defines Vault as a Helm-managed single-node Raft StatefulSet with a `local-path` PVC. The chart is not yet installed in the running cluster; the manifest update must be pushed and reconciled before the data restore.

## Vault transition

- Vault has been restored into the K3s Helm release from the source Raft snapshot. It is initialized, unsealed with the original Shamir shares, and reports the original Vault cluster ID.
- A Raft snapshot is stored outside Git at `/home/eorbr/vault-raft-pre-migration-20261109.snap`, mode `0600` (27,824 bytes).
- Original init material is `/home/eorbr/vault-init.json`, mode `0600`. Do not print it, commit it, or replace the original unseal shares during migration.
- The systemd Vault service is disabled. Run `ansible/playbooks/archive-external-vault.yml` after the Vault, VSO secret, and replacement Tailscale Ingress checks pass to purge the old package, host data, TLS/configuration, and obsolete network bridge.
- Auto Unseal is not configured. Vault continues to use Shamir 3-of-5 and requires manual unseal after a Vault pod or WSL restart.
- Git routes VSO to `http://vault.vault.svc:8200` and points the Vault Ingress directly to the in-cluster Service. The guarded archive playbook removes the former NGINX bridge and external Vault DNS/TLS configuration after migration checks pass.
- Follow [`vault.md`](vault.md) for the ordered snapshot restore. The chart destination must be initialized once, restored with `-force`, then unsealed with three of the original five shares.

## Tailscale prerequisite

The OAuth credential now has the `tag:k8s-operator` tag allowed, and Vault has synced the OAuth Secret into namespace `tailscale`. The operator logs still report that `tag:k8s` is invalid/not permitted because the tailnet policy is missing the ownership delegation. Merge this into the existing `tagOwners` block:

```json
"tagOwners": {
  "tag:k8s-operator": [],
  "tag:k8s": ["tag:k8s-operator"]
}
```

After saving the policy, the Operator should retry without recreating the OAuth client. Both Ingress resources currently have no Tailscale `ADDRESS` until proxy creation succeeds.

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

## Remaining work

1. Add the Tailscale `tagOwners` relationship in the admin policy.
2. Push the Git changes and let Argo CD install the Vault chart; perform the documented Raft restore and verify VSO.
3. Confirm the Tailscale Operator creates its node and ingress proxies, then verify the Argo CD and Vault MagicDNS HTTPS addresses from a tailnet-connected device.
4. Run `archive-external-vault.yml` only after the in-cluster Vault is verified. The playbook disables the old service and removes its host DNS/CoreDNS/Ingress bridge while preserving the old data directory and TLS files.
5. Publish custom images to GHCR and pin immutable tags or digests. Snowball currently has no Git remote.
6. Export Snowball and companion Docker named volumes through a Docker-aware backup after Docker Desktop is repaired. Those named volumes cannot be read through Docker CLI while the engine is stopped.
7. Add each service as an Argo child Application only after its image, Vault references, and persistent data are ready.

See [`migration.md`](migration.md) for the repeatable per-application cutover sequence. The Docker VHDX and named volumes have not been deleted or intentionally modified.

# Current migration status

Updated: 2026-11-09

## Installed and running

- WSL2 Ubuntu uses cgroup v2 and systemd.
- K3s v1.37.2+k3s1 is enabled as a systemd service; node `home-server-wsl` is Ready.
- Argo CD v3.6.1 and Vault Secrets Operator chart v1.6.2 are installed through the root Application. Both Argo CD Applications are Synced and Healthy.
- WSL uses `vmIdleTimeout=-1` so the idle distro remains available. Docker Desktop is stopped by request and was not started during this work.
- HashiCorp Vault 2.2.1-1 is installed as a systemd service outside K3s, with single-node Raft storage at `/var/lib/vault/data`, TLS at `https://vault.home.arpa:8200`, and a dedicated local CA. Its public CA is available to VSO in `vault-secrets-operator-system/vault-ca`.
- Vault is intentionally uninitialized and sealed. The one-time initialization and secure storage of key shares/root token remain a manual step for the owner.
- Git now defines VaultConnection and Tailscale Operator credentials through VSO, plus a private Tailscale Ingress for Argo CD. These resources are awaiting the root Application sync; the Tailscale OAuth secret has not been supplied.
- Tailscale Admin still needs MagicDNS/HTTPS and the Operator OAuth client/tag permissions configured. No Tailscale credential is stored in Git.

## Data copied without starting Docker

The original directories are still present. A separate file-level copy was made while the source containers were offline:

| Data | Original | Copy | Check |
|---|---|---|---|
| Hindsight PostgreSQL 16 | `/home/eorbr/ai-memory/hindsight/postgres` | `/var/lib/home-server-data/imports/2026-11-08/hindsight-postgres` | `diff -qr` reported no file differences; 70 MB |
| AI Memory | `/home/eorbr/ai-memory/ai-memory/data` | `/var/lib/home-server-data/imports/2026-11-08/ai-memory-data` | `diff -qr` reported no file differences; 94 MB |

These copies are staging data, not yet mounted by Kubernetes. Keep the originals until the restored applications have been checked.

## Compose inventory observed

- Snowball: `knowledge-api`, `collector`, Fuseki, Neo4j, Qdrant. The two custom knowledge services build from the local Snowball workspace. Their named-volume data remains inside Docker Desktop's data disk.
- AI Learning Companion: `companion-api`, `watch-skill`, `asr-worker`. It has a Docker socket mount and model volume; the Docker socket integration needs redesign for Kubernetes.
- Hindsight: API plus PostgreSQL. PostgreSQL data is staged above; the service also needs Vault-provided LLM and database credentials.
- AI Memory: image `docker.io/akitaonrails/ai-memory@sha256:6d107f95f7c4a79d3b2de789baf2013a3b20ddabdf12da83ca4c56f85b84b0c8`, port 49374, data staged above; its auth token must come from Vault.
- Other stopped projects and images (including QEV, Beacon, LLM Wiki, OpenClaw, and OMP) were not active Compose workloads selected for this first migration pass.

## Remaining prerequisites

1. Initialize and unseal Vault, then run `bash scripts/bootstrap-vault-tailscale.sh` after Argo creates the Tailscale auth ServiceAccount. Configure the Tailscale Admin prerequisites in `docs/networking-tailscale.md` and provide OAuth credentials through the hidden prompt.
2. Confirm the root Application syncs the Vault and Tailscale child Applications, and verify the Argo CD MagicDNS HTTPS address from a Tailscale-connected device.
3. Publish custom images to GHCR and pin immutable tags or digests. The Snowball workspace currently has no Git remote.
4. Export the Snowball and companion Docker named volumes through a Docker-aware backup when Docker Desktop can be safely repaired. They cannot be read through Docker CLI while its engine is stopped.
5. Add each service as an Argo child Application only after its image, Vault references, and persistent data are ready. Keep source data until each application is verified.

The Docker VHDX and Docker named volumes have not been deleted or modified intentionally. See [migration.md](migration.md) for the repeatable per-application cutover sequence.
## Copy command note

An initial copy command mistakenly addressed the pre-existing root-level paths /hindsight-postgres and /ai-memory-data. Their current file content compares identical to the source with diff -qr; those pre-existing paths were left in place because no earlier metadata baseline is available. The intended staging copies are under /var/lib/home-server-data/imports/2026-11-08.

# Vault on K3s

Argo CD installs HashiCorp's Vault Helm chart into the `vault` namespace. The chart runs one Vault server with integrated Raft storage and a `local-path` persistent volume. This is a single-node deployment: the volume survives Pod replacement, but it does not provide node or disk redundancy. Back up snapshots outside the cluster.

Vault listens on HTTP inside the cluster. VSO connects to `http://vault.vault.svc:8200`; the Tailscale Operator exposes the Vault UI/API as the private HTTPS MagicDNS Ingress `vault`. No host IP, host TLS CA, CoreDNS alias, or CA Secret is needed for in-cluster traffic.

## Current WSL migration

The previous systemd Vault is still the source of truth until the restore is confirmed. Its Shamir keys, root token, and Raft data remain in place. A protected source snapshot was saved at `/home/eorbr/vault-raft-pre-migration-20261109.snap` (mode `0600`). Do not initialize a clean Vault and begin using it for secrets before restoring that snapshot.

1. Commit and push the Vault Helm Application. Argo CD creates the `vault` namespace, StatefulSet, and PVC. Check `kubectl -n vault get pod,pvc` and wait until `vault-0` is Running.
2. Use the guarded restore script. It first confirms that the destination is reachable and uninitialized, validates and copies the snapshot, initializes the destination into a protected temporary file, unseals it with the temporary key, then submits the forced restore with the temporary root token. It refuses to overwrite an already initialized destination:

   ```bash
   sudo bash ./scripts/restore-vault-raft.sh /home/eorbr/vault-raft-pre-migration-20261109.snap
   ```

3. Wait for the restore to finish in the pod logs. The command returning does not mean the restore has finished. Check the latest logs for `post-unseal setup complete` and `vault is unsealed`; if it seals before becoming unsealed, proceed to the manual unseal. Use three of the original five shares (the shares from `/home/eorbr/vault-init.json`):

   ```bash
   sudo k3s kubectl logs -n vault vault-0 --since=10m
   sudo k3s kubectl exec -it -n vault vault-0 -- vault operator unseal
   ```

   Run the unseal command three times, entering a different original share each time. Confirm `Initialized: true`, `Sealed: false`, and `Storage Type: raft` with `sudo k3s kubectl exec -n vault vault-0 -- vault status`.

4. The snapshot also restores the previous Kubernetes auth configuration, which points at the host API address. Run `sudo bash ./scripts/bootstrap-vault-tailscale.sh --auth-only` from the repo to update TokenReview to the in-cluster API; this leaves the restored OAuth secret untouched. Then verify the Tailscale KV record, auth mount, policy, and role. Check VSO's `VaultStaticSecret` becomes `Ready=True`, then check the Tailscale Operator logs and both Ingress `ADDRESS` fields. After verification, remove the temporary init file and in-Pod snapshot: `sudo k3s kubectl exec -n vault vault-0 -- rm -f /tmp/vault-destination-init.json /tmp/vault-raft-source.snap`.
5. Only after those checks, run `ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/archive-external-vault.yml` to disable the old systemd Vault service and remove its host DNS/TLS bridge configuration. Keep `/var/lib/vault/data`, the source snapshot, and the original initialization file as rollback material until a separate backup has been verified.

The snapshot contains encrypted Vault state but is sensitive backup material. Keep it outside Git, restrict its permissions, and copy it to durable storage before relying on this migration as the only backup.

## Kubernetes authentication and VSO

The Vault Helm chart grants its ServiceAccount Kubernetes TokenReview permission. Configure the Kubernetes auth method to call `https://kubernetes.default.svc` and use the Vault Pod's projected ServiceAccount token and mounted cluster CA. VSO presents a short-lived ServiceAccount JWT whose audiences include both `vault` and the Kubernetes API. Keep the Tailscale Vault role restricted to the `tailscale/tailscale-vault-auth` ServiceAccount and its read-only KV policy.

Run `scripts/bootstrap-vault-tailscale.sh` from Ubuntu after Vault is restored and unsealed, VSO is installed, and the Argo CD credentials Application has created its ServiceAccount. The script talks to Vault through `kubectl exec` and stores the OAuth values in Vault KV; it does not place credentials in Git.

## Fresh WSL or Linux dual-boot setup

1. Run `prepare-host.yml`, `install-k3s.yml`, and `bootstrap-argocd.yml` from the chosen Ansible inventory.
2. Configure Tailscale policy/credentials and initialize Vault once after Argo CD installs the chart.
3. Save the init output and unseal shares in a protected file outside the repository. Unseal with the configured threshold after each host reboot.
4. Configure Kubernetes auth and write required application secrets into Vault. Argo CD and VSO then reconcile the app manifests and Kubernetes Secrets.

The Helm chart version, Vault image, PVC size/storage class, and cluster Applications are versioned in Git. The Vault data and initialization material are not.

## References

- [HashiCorp Vault Helm deployment](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/helm/run)
- [Vault Kubernetes auth](https://developer.hashicorp.com/vault/docs/auth/kubernetes)
- [Vault Raft snapshots](https://developer.hashicorp.com/vault/docs/sysadmin/snapshots)
- [Vault Raft snapshot restore](https://developer.hashicorp.com/vault/docs/sysadmin/snapshots/restore)

# Version pins

Ansible installs the K3s release pinned in [`versions.yml`](../versions.yml). Argo CD itself is bootstrapped from that same file. The Ansible control-node dependencies are pinned in [`ansible/requirements.txt`](../ansible/requirements.txt).

Helm charts and their application images are pinned in each environment's Argo CD Application:

- Vault Helm chart `0.34.1`, Vault image `2.2.1`.
- Vault Secrets Operator chart `1.6.2`.
- Tailscale Operator chart `1.104.4`.

Update WSL and dual-boot Application definitions together when changing a platform chart or image. Review the upstream release notes, update the version pins, and reconcile the WSL cluster before applying the change to dual-boot.

Each environment pins a stable Raft `node_id`. The WSL value preserves the source node identity during the current snapshot migration; the dual-boot profile uses its own identity for a fresh cluster. When restoring a snapshot from another environment, match the destination node ID to the source cluster's Raft peer ID before restoring.

## Upstream releases

- [K3s releases](https://github.com/k3s-io/k3s/releases)
- [Argo CD releases](https://github.com/argoproj/argo-cd/releases)
- [Vault Helm chart releases](https://github.com/hashicorp/vault-helm/releases)
- [Vault Secrets Operator releases](https://github.com/hashicorp/vault-secrets-operator/releases)
- [Tailscale Kubernetes Operator releases](https://github.com/tailscale/tailscale/releases)
- [Ansible Core releases](https://docs.ansible.com/projects/ansible/latest/reference_appendices/release_and_maintenance.html)

# Architecture

```text
GitHub home-server-desktop
  ├─ Ansible: Ubuntu host preparation, K3s install, Argo CD bootstrap
  └─ Argo CD root Application
       ├─ Vault Helm chart + Raft PVC
       ├─ Vault Secrets Operator
       ├─ Tailscale Operator + Vault-synced OAuth Secret
       └─ application child Applications

Vault Pod ── Kubernetes Service / HTTP ──> VSO ──> app Kubernetes Secrets
Tailnet device ── HTTPS MagicDNS ──> Tailscale proxy ──> Kubernetes Service
```

Git contains the repeatable configuration. Vault data, init keys, root tokens, kubeconfigs, and app secret values stay outside Git. Vault uses single-node integrated Raft storage backed by a local-path PVC; its snapshots must be stored outside the cluster.

Ansible does not install the Vault server. Argo CD installs the HashiCorp Vault chart and manages its configuration. Vault remains manually initialized and Shamir-unsealed; Kubernetes auth is configured after initialization so Vault can use its in-cluster ServiceAccount and Kubernetes API Service. VSO then syncs narrowly scoped KV values into Kubernetes Secrets.

K3s uses its bundled containerd runtime. Docker Desktop image cache and named volumes are not shared with K3s. App images must be published to a registry, and persistent data must be migrated using per-application backup and restore procedures.

# Tailscale Kubernetes Ingress

The Tailscale Kubernetes Operator is managed by Argo CD. It exposes selected Kubernetes Services as HTTPS MagicDNS names reachable only from devices in the tailnet. It does not use Windows port forwarding, `kubectl port-forward`, Tailscale Serve, or Funnel.

## Operator and secret flow

- Argo CD installs the pinned stable Helm chart `tailscale-operator` version `1.104.4` into `tailscale`.
- The chart uses the pre-created `operator-oauth` Secret. Its `client_id` and `client_secret` fields are synchronized from HashiCorp Vault by Vault Secrets Operator.
- The secret path is KV v2 `kv/tailscale/operator`. The bootstrap script creates a Kubernetes auth role restricted to the `tailscale/tailscale-vault-auth` ServiceAccount and grants read access to only that Vault path.
- The OAuth client needs Write scopes for `General/Services`, `Devices/Core`, and `Keys/Auth Keys`, with `tag:k8s-operator` allowed. Do not put its values in Git.

## Tailnet setup

In the Tailscale admin console:

1. Enable MagicDNS and HTTPS certificates.
2. Merge these owners into the existing tailnet policy; retain any existing policy entries:

   ```json
   "tagOwners": {
     "tag:k8s-operator": [],
     "tag:k8s": ["tag:k8s-operator"]
   }
   ```

3. Create an OAuth client with the three Write scopes above and permit it to use `tag:k8s-operator`. Save its ID and secret; Tailscale only shows the secret at creation time.
4. Initialize and unseal Vault, then run `sudo bash ./scripts/bootstrap-vault-tailscale.sh` inside the repository in Ubuntu. Enter the OAuth credentials at its hidden prompts. The script stores them directly in Vault and removes its short-lived temporary file.
5. Allow the intended users or groups to reach tagged Tailscale ingress proxies on TCP 443 in the tailnet policy. Do not enable Funnel unless public Internet access is deliberately required.

## Ingress pattern

An app keeps a `ClusterIP` Service and adds an Ingress only when tailnet access is needed:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app
  namespace: app
spec:
  ingressClassName: tailscale
  tls:
    - hosts:
        - app
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: app
                port:
                  number: 8080
```

The repository includes this pattern for the Argo CD web service. Read the generated MagicDNS address from the Ingress `ADDRESS` field. The operator provisions HTTPS certificates and a proxy for each standalone Ingress. This WSL cluster is a single node, so it starts without a multi-replica ProxyGroup; a ProxyGroup can later consolidate proxies if service count grows.

## References

- [Install the Tailscale Kubernetes Operator](https://tailscale.com/docs/kubernetes-operator/install-operator)
- [Expose a cluster workload to your tailnet](https://tailscale.com/docs/kubernetes-operator/ingress/expose-workload-to-tailnet-l7)
- [Vault authentication for VSO](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/vso/sources/vault/auth)

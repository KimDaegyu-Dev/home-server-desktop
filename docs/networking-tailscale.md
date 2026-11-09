# Tailscale Kubernetes Ingress

The Tailscale Kubernetes Operator is managed by Argo CD. It exposes selected Kubernetes Services as HTTPS MagicDNS names reachable only from devices in the tailnet. It does not use Windows port forwarding, `kubectl port-forward`, Tailscale Serve, or Funnel.

## Operator and secret flow

- Argo CD installs the pinned stable Helm chart `tailscale-operator` version `1.104.4` into `tailscale`.
- The chart uses the `operator-oauth` Secret. Vault Secrets Operator syncs its `client_id` and `client_secret` fields from Vault KV v2 path `kv/tailscale/operator`.
- The Vault policy and Kubernetes auth role restrict access to the `tailscale/tailscale-vault-auth` ServiceAccount and only that KV path.
- The OAuth client needs Write scopes for `General/Services`, `Devices/Core`, and `Keys/Auth Keys`, with `tag:k8s-operator` allowed. Do not put its values in Git.

## Tailnet setup

In the Tailscale admin console:

1. Enable MagicDNS and HTTPS certificates.
2. Merge these owners into the existing tailnet policy; retain any existing policy entries. The empty owner list on `tag:k8s-operator` means tailnet admins own that tag. The second line delegates `tag:k8s` ownership to the operator so it can create tagged proxy devices:

   ```json
   "tagOwners": {
     "tag:k8s-operator": [],
     "tag:k8s": ["tag:k8s-operator"]
   }
   ```

3. The existing OAuth client should have the three Write scopes above and be permitted to use `tag:k8s-operator`. Keep using the same credential so the current Vault value remains valid.
4. Allow intended users or groups to reach tagged ingress proxies on TCP 443 in the tailnet policy. Do not enable Funnel unless public Internet access is deliberately required.

The Tailscale Operator applies `tag:k8s-operator` to its own node and `tag:k8s` to ingress proxy devices. The `tagOwners` relationship authorizes the operator tag to create proxy nodes under `tag:k8s`; it does not grant user access to those proxies. User traffic access is separately controlled by the tailnet grants/ACL rules.

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

The repository includes this pattern for Argo CD and Vault. Vault runs inside K3s in namespace `vault`; its Ingress points directly to the Vault Service. Vault uses HTTP within the cluster, while Tailscale terminates external HTTPS using its MagicDNS certificate. VSO connects to `http://vault.vault.svc:8200`.

The configured tailnet hostnames are `argocd` and `vault`. Read their full `https://<tailnet>.ts.net` addresses from `kubectl get ingress -A` after the Operator authenticates and issues certificates. The addresses only work from devices connected to the tailnet.

The operator provisions a proxy for each standalone Ingress. This WSL cluster is a single node, so it starts without a multi-replica ProxyGroup; a ProxyGroup can later consolidate proxies if service count grows.

## References

- [Install the Tailscale Kubernetes Operator](https://tailscale.com/docs/kubernetes-operator/install-operator)
- [Tag customization and ownership](https://tailscale.com/docs/kubernetes-operator/reference/tags)
- [Expose a cluster workload to your tailnet](https://tailscale.com/docs/kubernetes-operator/ingress/expose-workload-to-tailnet-l7)
- [Vault authentication for VSO](https://developer.hashicorp.com/vault/docs/deploy/kubernetes/vso/sources/vault/auth)

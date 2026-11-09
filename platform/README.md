# Platform manifests

Cluster-wide credential resources are collected in `cluster-credentials/` and Tailscale Ingress resources in `cluster-ingress/`. Each is one feature-focused Argo CD Application generated from `clusters/<profile>/apps/catalog/` by the profile's ApplicationSet. Helm charts remain individual generated Applications.

Keep secret values in Vault KV. Git contains only VaultStaticSecret references and non-secret configuration. The ApplicationSet preserves workloads when an Application descriptor is removed, so deleting a catalog entry does not automatically delete the underlying resources.

VaultStaticSecret resources that copy Vault KV fields directly into a Kubernetes Secret must not set `destination.transformation.excludeRaw: true`; that option omits the source fields from the destination unless templates explicitly recreate them. Use narrowly scoped Vault policies for secret access and set `rolloutRestartTargets` for applications that read credentials from environment variables.

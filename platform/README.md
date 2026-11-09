# Platform manifests

Cluster-wide credential resources are collected in `cluster-credentials/` and Tailscale Ingress resources in `cluster-ingress/`. Each is one feature-focused Argo CD Application generated from `clusters/<profile>/apps/catalog/` by the profile's ApplicationSet. Helm charts remain individual generated Applications.

Keep secret values in Vault KV. Git contains only VaultStaticSecret references and non-secret configuration. The ApplicationSet preserves workloads when an Application descriptor is removed, so deleting a catalog entry does not automatically delete the underlying resources.

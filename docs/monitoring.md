# Prometheus and Grafana

Argo CD installs the Prometheus Community `kube-prometheus-stack` chart. It includes Prometheus Operator, Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics, and Kubernetes dashboards/rules. Prometheus retains up to 30 days and 18 GB of samples on a 20 GiB local-path PVC. Grafana uses a 5 GiB PVC; Alertmanager uses 2 GiB. WSL disables node-exporter's host mount propagation, which WSL2 does not support; it still scrapes kubelet/cAdvisor and Kubernetes state metrics. The Linux dual-boot profile uses the chart's normal node-exporter mounts.

## Bootstrap Grafana credentials

1. Push GitOps manifests and wait for the `monitoring-credentials` Application to create the namespace, VaultAuth, and `monitoring-vault-auth` ServiceAccount.
2. From Ubuntu, seed a random Grafana admin password into Vault:

   ```bash
   cd ~/src/home-server-desktop
   sudo bash ./scripts/bootstrap-monitoring-vault.sh --token-file /home/eorbr/vault-init.json
   ```

   The script stores credentials at `kv/monitoring/grafana`, gives VSO read-only access to that single path, and never prints the password. It refuses to overwrite existing data.

3. Wait for the `monitoring-stack` Application and all monitoring Pods to become Healthy/Ready. Grafana's admin username is `admin`; retrieve its password from the Vault UI at `kv/monitoring/grafana` if a local login is needed.

## Access and metrics

- Grafana: `https://grafana.tail5389e9.ts.net`
- Prometheus: `https://prometheus.tail5389e9.ts.net`

Both addresses are Tailscale-only. Prometheus discovers Kubernetes ServiceMonitors and PodMonitors across namespaces. New applications still need to expose a metrics endpoint or have a suitable exporter; add those monitors with each service's Argo CD app.

## Backups and limits

The PVCs are single-host storage, not backups. Back up Grafana dashboards/configuration and Prometheus data as needed, and include Vault's external Raft snapshot procedure. Prometheus sample retention is capped by both 30 days and 18 GB; high-cardinality metrics can consume that cap sooner.

## References

- [kube-prometheus-stack chart](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [Prometheus Operator ServiceMonitor](https://prometheus-operator.dev/docs/developer/getting-started/)

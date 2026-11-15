# WSL GPU observability

The WSL profile reads GPU state through Windows' `nvidia-smi.exe`, stores Prometheus textfile metrics on the WSL host, and exposes them through the existing node-exporter, Prometheus, Grafana, and Grafana MCP path. The collector is read-only and runs every 15 seconds.

## Why this does not install GPU Operator

The active node uses Ubuntu 24.04 on the Microsoft WSL2 kernel and exposes `/dev/dxg`, not a standard Linux NVIDIA driver device. NVIDIA's GPU Operator platform matrix validates K3s on supported Linux distributions and bare-metal/VM GPU configurations; it does not list WSL2. NVIDIA's CUDA on WSL guide also says not to install a Linux GPU driver inside WSL. Installing the operator's default driver/toolkit/device-plugin stack here could fail or destabilize the node.

This collector therefore provides host telemetry only. It does not register a Kubernetes `nvidia.com/gpu` resource, and it is not DCGM.

## Metrics and agent access

- `wsl_gpu_utilization_percent`: GPU utilization.
- `wsl_gpu_memory_used_bytes`, `wsl_gpu_memory_total_bytes`: aggregate VRAM use and capacity.
- `wsl_gpu_process_info`: Windows PID, process name, and GPU UUID for each process reported by `nvidia-smi` as holding a GPU context.
- `wsl_gpu_process_memory_bytes`: per-process VRAM only when the Windows driver reports it.
- `wsl_gpu_exporter_scrape_error`, `wsl_gpu_exporter_last_success_timestamp_seconds`: collector health and freshness.

Agents can use the existing Grafana MCP `query_prometheus` tool with the `prometheus` datasource. The **WSL NVIDIA GPU** dashboard is provisioned from `platform/gpu-observability/grafana-dashboard.yaml`. The Skillshare `gpu-runtime-triage` skill documents the queries and interpretation rules.

Windows WDDM commonly reports per-process VRAM as unavailable. The process list can also include normal desktop applications. Treat a PID as a candidate for investigation; correlate it with the process/service state and active model runs before calling it an orphan. GPU memory use by itself does not identify the owning process.

## Host bootstrap

Run `ansible/playbooks/prepare-host.yml` against the WSL inventory to install and enable the collector timer. Argo CD then manages the node-exporter textfile mount and Grafana dashboard from the WSL catalog. No GPU process is stopped or changed by this collector.

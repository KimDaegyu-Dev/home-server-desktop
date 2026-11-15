---
name: gpu-runtime-triage
description: Inspect WSL NVIDIA GPU utilization, memory, and Windows GPU-context PIDs through Grafana MCP before diagnosing stuck or orphaned local-model processes.
---

# GPU Runtime Triage

Use this when a task mentions GPU saturation, unexpectedly high GPU memory, a local model that did not start, or a suspected orphan process.

## Read current telemetry

- Use the Grafana MCP Prometheus query tool (`grafana:query_prometheus`) with the existing `prometheus` datasource.
- Current GPU load: `wsl_gpu_utilization_percent`.
- Current and total memory: `wsl_gpu_memory_used_bytes` and `wsl_gpu_memory_total_bytes`.
- Windows PIDs with a GPU context: `wsl_gpu_process_info` (labels include `pid`, `process_name`, and `gpu_uuid`).
- Per-process VRAM, SM, and memory-controller metrics: `wsl_gpu_process_memory_bytes`, `wsl_gpu_process_sm_utilization_percent`, and `wsl_gpu_process_memory_controller_utilization_percent` when the Windows driver reports them.
- Collector health: `wsl_gpu_exporter_scrape_error` and `wsl_gpu_exporter_last_success_timestamp_seconds`.
- The Grafana dashboard is titled **WSL NVIDIA GPU**.

## Decide whether a process is orphaned

1. Check collector health and timestamp first. Stale or failed telemetry is not evidence that a process stopped.
2. Compare GPU-context PIDs and process names with the current local-model service state and active task/run records.
3. Treat an unrecognized PID as a candidate for investigation, not proof of an orphan. Windows WDDM can include ordinary desktop processes and may report per-process memory as unavailable.
4. Do not terminate a PID based only on GPU memory use or low utilization. Verify the process owner, command line, service state, and active work before recommending termination.
5. Report the sample time, PID, process name, utilization, used/total GPU memory, and the evidence connecting the process to an active or stale run.

## Known limits

- WSL exposes Windows-host GPU telemetry; this is not DCGM and does not make the GPU a Kubernetes schedulable resource.
- Per-process VRAM is often unavailable under WDDM. Total GPU memory cannot be attributed to one PID reliably.
- Per-process SM/MEM utilization may also be unavailable under WDDM; confirm the metric exists before interpreting a blank panel as zero.
- The collector is read-only. It does not stop processes or change model/server state.

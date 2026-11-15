#!/usr/bin/env python3
"""Export WSL's Windows-host NVIDIA telemetry for node-exporter's textfile collector."""

import csv
import io
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional


OUTPUT = Path("/var/lib/node_exporter/textfile_collector/wsl_gpu.prom")
NVIDIA_SMI = Path("/mnt/c/Windows/System32/nvidia-smi.exe")


def number(value: str) -> Optional[float]:
    cleaned = value.strip()
    if not cleaned or cleaned.upper() in {"N/A", "[N/A]", "NOT SUPPORTED"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def run_smi(*args: str) -> str:
    result = subprocess.run(
        [str(NVIDIA_SMI), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=8,
    )
    return result.stdout


def collect() -> list[str]:
    lines = [
        "# HELP wsl_gpu_exporter_scrape_error 1 if the most recent Windows nvidia-smi query failed.",
        "# TYPE wsl_gpu_exporter_scrape_error gauge",
        "wsl_gpu_exporter_scrape_error 0",
        "# HELP wsl_gpu_exporter_last_success_timestamp_seconds Unix timestamp of the last successful collection.",
        "# TYPE wsl_gpu_exporter_last_success_timestamp_seconds gauge",
        f"wsl_gpu_exporter_last_success_timestamp_seconds {time.time():.3f}",
    ]
    gpu_rows = csv.reader(io.StringIO(run_smi(
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    )))
    for row in gpu_rows:
        if len(row) != 7:
            continue
        index, name, utilization, memory_used, memory_total, temperature, power = [item.strip() for item in row]
        labels = f'gpu="{label(index)}",name="{label(name)}"'
        metrics = (
            ("wsl_gpu_utilization_percent", utilization, "GPU utilization percentage."),
            ("wsl_gpu_memory_used_bytes", memory_used, "GPU memory used in bytes."),
            ("wsl_gpu_memory_total_bytes", memory_total, "GPU memory total in bytes."),
            ("wsl_gpu_temperature_celsius", temperature, "GPU temperature in Celsius."),
            ("wsl_gpu_power_watts", power, "GPU power draw in watts."),
        )
        for metric, raw, help_text in metrics:
            value = number(raw)
            if value is None:
                continue
            if metric.endswith("_bytes"):
                value *= 1024 * 1024
            lines.extend((f"# HELP {metric} {help_text}", f"# TYPE {metric} gauge", f"{metric}{{{labels}}} {value:g}"))

    lines.extend((
        "# HELP wsl_gpu_process_info A Windows process reported by NVIDIA as holding a GPU context.",
        "# TYPE wsl_gpu_process_info gauge",
        "# HELP wsl_gpu_process_memory_bytes GPU memory attributed to a process, when the Windows driver reports it.",
        "# TYPE wsl_gpu_process_memory_bytes gauge",
    ))
    process_rows = csv.reader(io.StringIO(run_smi(
        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    )))
    for row in process_rows:
        if len(row) != 4:
            continue
        gpu_uuid, pid, process_name, used_memory = [item.strip() for item in row]
        process_name = process_name.replace("\\", "/").rsplit("/", 1)[-1]
        labels = f'gpu_uuid="{label(gpu_uuid)}",pid="{label(pid)}",process_name="{label(process_name)}"'
        lines.append(f"wsl_gpu_process_info{{{labels}}} 1")
        memory_mib = number(used_memory)
        if memory_mib is not None:
            lines.append(f"wsl_gpu_process_memory_bytes{{{labels}}} {memory_mib * 1024 * 1024:g}")
    return lines


def main() -> None:
    try:
        lines = collect()
    except (OSError, subprocess.SubprocessError, csv.Error):
        previous = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        last_success = re.search(
            r"^wsl_gpu_exporter_last_success_timestamp_seconds\s+([0-9.]+)$",
            previous,
            flags=re.MULTILINE,
        )
        lines = [
            "# HELP wsl_gpu_exporter_scrape_error 1 if the most recent Windows nvidia-smi query failed.",
            "# TYPE wsl_gpu_exporter_scrape_error gauge",
            "wsl_gpu_exporter_scrape_error 1",
        ]
        if last_success:
            lines.extend((
                "# HELP wsl_gpu_exporter_last_success_timestamp_seconds Unix timestamp of the last successful collection.",
                "# TYPE wsl_gpu_exporter_last_success_timestamp_seconds gauge",
                f"wsl_gpu_exporter_last_success_timestamp_seconds {last_success.group(1)}",
            ))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".prom.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)


if __name__ == "__main__":
    main()

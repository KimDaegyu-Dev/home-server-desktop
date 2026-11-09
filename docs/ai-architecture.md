# LLM gateway architecture

```text
Tailnet client → Tailscale HTTPS → LiteLLM (virtual-key API / Authentik UI)
                                  → Headroom callback → WSL-only authenticated bridge
                                                       → Windows llama-swap 127.0.0.1:8081
LiteLLM OTLP → Alloy → Prometheus → Grafana metrics dashboard
                    └→ Langfuse OTLP → ClickHouse / Postgres / SeaweedFS
```

LiteLLM, Headroom, Langfuse, their VaultStaticSecrets, Authentik OIDC providers, Alloy trace export, and the Langfuse storage alert are reconciled from the profile catalog. The WSL Windows-host endpoint is refreshed by a systemd timer; the Windows bridge firewall is scoped to the current WSL address prefix. The dualboot profile installs the same AI charts but leaves LiteLLM's initial model list empty.

See [`litellm-headroom-langfuse.md`](litellm-headroom-langfuse.md) for setup and operations.

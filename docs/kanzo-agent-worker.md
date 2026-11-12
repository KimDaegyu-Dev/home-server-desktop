# Kanzo Hermes worker

The deployment has a Kanzo bridge worker, a Hermes gateway, and a read-only Hermes executor in the `mcp` namespace. The bridge polls the `kanzo-project-catalog` every five minutes and handles one parent task at a time. Only project cards in `doing` are active; `todo` is inactive, `review` pauses claims, and `done` archives the project. The gateway owns Hermes Kanban dispatch. `Recreate` prevents overlapping bridge/gateway pods.

## Task behavior

- Kanzo `next_task` is called with `-is:human -is:waiting`; Kanzo also skips waiting tasks itself. Tasks that need a person are moved back to `todo` with `human=true`, with the reason in a comment. Clear Human after resolving the blocker to retry.
- Each active project card declares `slug`, `repo`, `model_lane`, and `read_only`; `provider` and `model` default to `openai-api` and `iq_3s`, and `reason` defaults to `high`. Optional `profile` defaults by lane (`researcher`, `builder`, or `reviewer`). A provider/model override must include both fields. Provider authentication is checked before a task runs; there is no silent model fallback.
- Oh My Hermes is pinned in `versions.yml` and installed into Hermes' own Python environment so its plugin bridge and Kanban board engine are importable. The persistent Hermes home is bootstrapped with three profiles: `researcher` (`iq_3s`), `builder` (`coder`), and `reviewer` (`iq_3s`).
- The parent agent uses `/dag-ulw` for bounded parallel work and the Hermes Kanban board for durable cross-profile work. It waits for child handoffs and returns the combined result to the original Kanzo card.
- Hermes output is drained asynchronously under a 45-minute wall-clock deadline. The bridge refreshes its liveness heartbeat while the model is silent, and a timeout terminates the Hermes process group so Kanzo can record the task as needing human review instead of leaving it in `doing`.
- `read_only: true` routes Hermes through a sidecar with its own isolated Hermes home and gateway, plus a read-only `/workspace` mount. It has no Kanzo write token or GitHub credential. It can still use terminal and code execution, but repository writes fail at the filesystem boundary; the parent task always returns to `review`. If the sidecar is unavailable, the worker fails closed.
- Writable code tasks use an ephemeral checkout. The agent process does not receive the GitHub write token. The worker commits, pushes, opens a draft PR, links it on the Kanzo card, and moves it to `review`. It never merges or deploys.
- Hermes home and OAuth state live on the `kanzo-agent-hermes-home` PVC. The workspace is ephemeral. Prometheus scrapes worker health and task counters at `/metrics`.

## Credentials and enablement

1. Build and import the pinned image with `sudo bash scripts/build-kanzo-agent-worker.sh`, then allow Argo CD to create the `kanzo-agent-vault-auth` ServiceAccount and `VaultAuth`.
2. Run `sudo bash scripts/bootstrap-mcp-vault.sh` to install the dedicated read-only Vault policy and Kubernetes role for `kv/mcp/kanzo-agent`.
3. In MCPProxy, create two agent tokens. The worker token should allow only Kanzo task read/claim/comment/update operations. The Hermes token should allow only read-only services and tools needed for research and context. Store them in Vault as `MCPPROXY_KANZO_AGENT_TOKEN` and `MCPPROXY_AGENT_READ_TOKEN`.
4. Create a LiteLLM virtual key restricted to the local analysis model alias and store it as `LITELLM_API_KEY`. Store a repository-scoped GitHub token as `GITHUB_TOKEN`; grant only repository contents write and pull-request write. Add all four values to Vault path `kv/mcp/kanzo-agent`. Never put them in Git or a task description.
5. The initial active catalog card is `hermes-harness-lab`, read-only, on `openai-api/iq_3s` with high reasoning. Add or edit project cards in `kanzo-project-catalog`; no per-project deployment is needed.
6. `KANZO_ENABLED=true` enables the catalog-driven worker. Invalid or duplicate active cards are moved to `review` before project tasks are claimed.

Keep all OAuth login state in Hermes' persistent home, not in this repository. When Codex or Anthropic is added later, create and authenticate a dedicated profile rather than sharing single-use OAuth state across profiles.

## Local checks

Run the worker unit tests with:

```bash
python3 -m unittest discover -s images/kanzo-agent-worker -p 'test_*.py'
```

Render the application with:

```bash
kustomize build platform/kanzo-agent-worker
```

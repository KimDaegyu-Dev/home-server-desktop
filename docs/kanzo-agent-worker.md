# Kanzo Hermes worker

The deployment has a Kanzo bridge worker and a Hermes gateway sidecar in the `mcp` namespace. The bridge polls the configured Kanzo project every five minutes and handles one parent task at a time. The gateway owns Hermes Kanban dispatch and starts specialist profile workers. `Recreate` prevents overlapping bridge/gateway pods. The worker starts enabled for the configured Kanzo project `babmukdang`.

## Task behavior

- Kanzo `next_task` is called with `-is:human -is:waiting`; Kanzo also skips waiting tasks itself. Tasks that need a person are moved back to `todo` with `human=true`, with the reason in a comment. Clear Human after resolving the blocker to retry.
- Analysis and review use the LiteLLM `iq_3s` alias with extra-high reasoning. Implementation uses the LiteLLM `coder` alias. Both stay behind the shared LiteLLM endpoint; no Codex or Anthropic login is required for this initial route. There is no silent model fallback.
- Oh My Hermes is pinned in `versions.yml` and installed into Hermes' own Python environment so its plugin bridge and Kanban board engine are importable. The persistent Hermes home is bootstrapped with three profiles: `researcher` (`iq_3s`), `builder` (`coder`), and `reviewer` (`iq_3s`).
- The parent agent uses `/dag-ulw` for bounded parallel work and the Hermes Kanban board for durable cross-profile work. It waits for child handoffs and returns the combined result to the original Kanzo card.
- Code tasks require a repo linked to the Kanzo project or present in `KANZO_PROJECT_REPOS_JSON`. The current copilot project maps to `KimDaegyu-Dev/home-server-desktop`; add other mappings in the ConfigMap when needed.
- Code is edited in an ephemeral checkout. The agent process does not receive the GitHub write token. The worker commits, pushes, opens a draft PR, links it on the Kanzo card, and moves it to `review`. It never merges or deploys.
- Hermes home and OAuth state live on the `kanzo-agent-hermes-home` PVC. The workspace is ephemeral. Prometheus scrapes worker health and task counters at `/metrics`.

## Credentials and enablement

1. Build and import the pinned image with `sudo bash scripts/build-kanzo-agent-worker.sh`, then allow Argo CD to create the `kanzo-agent-vault-auth` ServiceAccount and `VaultAuth`.
2. Run `sudo bash scripts/bootstrap-mcp-vault.sh` to install the dedicated read-only Vault policy and Kubernetes role for `kv/mcp/kanzo-agent`.
3. In MCPProxy, create two agent tokens. The worker token should allow only Kanzo task read/claim/comment/update operations. The Hermes token should allow only read-only services and tools needed for research and context. Store them in Vault as `MCPPROXY_KANZO_AGENT_TOKEN` and `MCPPROXY_AGENT_READ_TOKEN`.
4. Create a LiteLLM virtual key restricted to the local analysis model alias and store it as `LITELLM_API_KEY`. Store a repository-scoped GitHub token as `GITHUB_TOKEN`; grant only repository contents write and pull-request write. Add all four values to Vault path `kv/mcp/kanzo-agent`. Never put them in Git or a task description.
5. `KANZO_ANALYSIS_MODEL=iq_3s` and `KANZO_CODE_MODEL=coder` are configured in GitOps. The analysis profile uses `reasoning_effort: xhigh`. Codex and Anthropic remain optional future profiles; their OAuth logins are not copied between Hermes profiles.
6. `KANZO_ENABLED=true` is scoped to the `babmukdang` Kanzo project. Change it through GitOps if the project scope needs to change.

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

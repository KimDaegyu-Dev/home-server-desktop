# Kanzo Hermes worker

The worker is a single Hermes-backed process in the `mcp` namespace. It polls all Kanzo projects every five minutes and handles one task at a time. `Recreate` deployment strategy prevents overlapping worker instances. The worker starts disabled and does not claim tasks until `KANZO_ENABLED` is set to `true` after credentials and model aliases are configured.

## Task behavior

- Kanzo `next_task` is called with `-is:human -is:waiting`; Kanzo also skips waiting tasks itself. Tasks that need a person are moved back to `todo` with `human=true`, with the reason in a comment. Clear Human after resolving the blocker to retry.
- Non-code tasks use a configured LiteLLM model alias. Code tasks use the configured Hermes OAuth provider and model. There is no model fallback between these routes.
- Code tasks require a repo linked to the Kanzo project or present in `KANZO_PROJECT_REPOS_JSON`. The current copilot project maps to `KimDaegyu-Dev/home-server-desktop`; add other mappings in the ConfigMap when needed.
- Code is edited in an ephemeral checkout. The agent process does not receive the GitHub write token. The worker commits, pushes, opens a draft PR, links it on the Kanzo card, and moves it to `review`. It never merges or deploys.
- Hermes home and OAuth state live on the `kanzo-agent-hermes-home` PVC. The workspace is ephemeral. Prometheus scrapes worker health and task counters at `/metrics`.

## Credentials and enablement

1. Build and import the image with `sudo bash scripts/build-kanzo-agent-worker.sh`, then allow Argo CD to create the `kanzo-agent-vault-auth` ServiceAccount and `VaultAuth`.
2. Run `sudo bash scripts/bootstrap-mcp-vault.sh` to install the dedicated read-only Vault policy and Kubernetes role for `kv/mcp/kanzo-agent`.
3. In MCPProxy, create two agent tokens. The worker token should allow only Kanzo task read/claim/comment/update operations. The Hermes token should allow only read-only services and tools needed for research and context. Store them in Vault as `MCPPROXY_KANZO_AGENT_TOKEN` and `MCPPROXY_AGENT_READ_TOKEN`.
4. Create a LiteLLM virtual key restricted to the local analysis model alias and store it as `LITELLM_API_KEY`. Store a repository-scoped GitHub token as `GITHUB_TOKEN`; grant only repository contents write and pull-request write. Add all four values to Vault path `kv/mcp/kanzo-agent`. Never put them in Git or a task description.
5. `KANZO_ANALYSIS_MODEL` is set to the LiteLLM alias `iq_3s`; Hermes uses `reasoning_effort: xhigh` for the requested extra-high level. After the user completes `hermes auth add openai-codex` in the worker pod, set `KANZO_CODE_MODEL` to the selected Codex model ID. Anthropic authentication is intentionally deferred.
6. Keep `KANZO_ENABLED=false` until the user confirms Codex authentication and the code model is configured. Then enable it through GitOps after confirming the Kanzo project scope.

The OpenAI Codex provider uses ChatGPT/Codex OAuth. Keep login state in Hermes' persistent home, not in this repository. Anthropic is not part of the initial worker setup.

## Local checks

Run the worker unit tests with:

```bash
python3 -m unittest discover -s images/kanzo-agent-worker -p 'test_*.py'
```

Render the application with:

```bash
kustomize build platform/kanzo-agent-worker
```

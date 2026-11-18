# Kanzo Hermes worker

See [Kanzo · Hermes execution roadmap](kanzo-hermes-roadmap.md) for the reviewed limitations, sharing boundaries, staged changes, and acceptance criteria. The roadmap is a proposal; the behavior below describes the current deployment.

Execution progress and deployed revisions are tracked in [Kanzo · Hermes status](kanzo-hermes-status.md).

The deployment has Kanzo, a small SSE event adapter, a Hermes dispatcher, a Hermes gateway, a Hermes `serve` endpoint for Desktop, and a read-only Hermes executor in the `mcp` namespace. Kanzo publishes task-free change events from its in-process write notifications. The adapter keeps one stream open and wakes the dispatcher through loopback; it reconnects with backoff and requests a queue reconciliation after every connection. The dispatcher claims work through Kanzo MCP after an event or startup wake, drains all currently eligible tasks, then sleeps without querying Kanzo. Kanzo remains the durable source of truth, so events are hints rather than task payloads. Only project catalog cards in `doing` are active; catalog `todo` is inactive. In an active project, task `backlog` is unapproved, `todo` is eligible for an agent or human action, `doing` is claimed, `review` pauses claims, and `done` archives the task. The optional `backlog` setting is already enabled in the current Kanzo board; it persists in Kanzo's database. `Recreate` prevents overlapping dispatcher/gateway pods.

## Task behavior

- Kanzo `next_task` is called with `-is:human -is:waiting`; Kanzo also skips waiting tasks itself. Claims are sequential: Kanzo takes the top eligible task in board order, and the worker finishes it before claiming the next. When Hermes emits a valid `KANZO_HUMAN_ACTION` marker, the worker creates or reuses a `human-action` task with `human=true`, sets the agent task back to `todo` with `human=false` and a `waits_on` link, and posts the action cards on the parent. Mark every linked human card `done` after completing the action; the Kanzo event wakes the worker and the waiting agent task becomes eligible. The task restarts from its prompt and Kanzo comments. Writable code tasks reuse their persistent workspace on manual resume; linked tasks with the same `workspace_id` share one checkout sequentially. Missing provider authentication, LiteLLM credentials, code-model configuration, or the GitHub token are converted into concrete human action cards. Other execution errors go to `review` with `human=true`; they are not automatically retried. Resolve the issue, clear Human, and move the same task to `todo` when you want to resume it.
- To cancel a run already in progress, move its task to `review` and set `human=true`. A Kanzo change event makes the worker re-read only its active task; that exact combination cancels the run. `todo` is never a cancel signal. Writable Hermes runs receive SIGTERM for their process group and SIGKILL after a 10-second grace period. Read-only runs send the task ID and random run ID to the executor sidecar, which validates both before terminating that process group. Cancellation preserves the user's current Kanzo state and adds a reason comment. For a confirmed writable run with a persistent workspace, the worker transfers the final sandbox snapshot and keeps the workspace/branch; if transfer fails, the comment warns that in-flight edits may be missing. Cancellation creates no commit, push, PR, or automatic retry. The active task/run identity is exposed in worker metrics.
- Hermes may emit `KANZO_BACKLOG` markers for concrete, evidence-backed follow-up work outside the current task. The worker searches the same project for a matching title before creating a card in `backlog`; it does not load the whole backlog into the model prompt and imposes no count or age limit. A human proposal is tagged `human-action` and created with `human=true`; agent work uses `human=false`. Users decide what to promote to `todo`. When rejecting a proposal, add the `declined` tag, leave a short reason, and move it to `done`. Backlog items are never auto-expired or auto-archived.
- Open a task in the `kanzo-project-catalog` project to edit **Agent routing** fields: project slug, repository, model lane, Hermes profile, provider, model, reasoning, and read-only mode. Kanzo stores these separately from the task description and exposes them through MCP. Existing cards with `slug:`, `repo:`, `model_lane:`, and related description lines remain readable; saving their form converts the settings to structured fields. Provider/model overrides must be supplied together. Provider authentication is checked before a task runs; there is no silent model fallback.
- Oh My Hermes is pinned in `versions.yml` and installed into Hermes' own Python environment so its plugin bridge and Kanban board engine are importable. The persistent Hermes home is bootstrapped with three profiles: `researcher` (`iq_3s`), `builder` (`coder`), and `reviewer` (`iq_3s`).
- The parent agent uses `/dag-ulw` for bounded parallel work and the Hermes Kanban board for durable cross-profile work. It waits for child handoffs and returns the combined result to the original Kanzo card.
- Hermes output is drained asynchronously under a 45-minute wall-clock deadline. The bridge refreshes its liveness heartbeat while the model is silent, and a timeout terminates the Hermes process group so Kanzo can record the task as needing human review instead of leaving it in `doing`.
- `read_only: true` routes Hermes through a sidecar with its own isolated Hermes home and gateway, plus a read-only `/workspace` mount. It syncs the selected profile's settings, skills, and auth state into that separate home before each task. The executor has no Kanzo write token or GitHub credential. It can still use terminal and code execution, but repository writes fail at the filesystem boundary; the parent task always returns to `review`. If the sidecar is unavailable, the worker fails closed.
- Writable code tasks with a Git repository use a persistent workspace and one writer lease. Linked tasks can share the same `workspace_id` sequentially. The agent does not see `.git` or the GitHub write token; the worker manages Git. Before Hermes starts, the worker runs `git push --dry-run` and stops on denied access. After a successful run it commits, pushes, opens or updates a draft PR, links it on the Kanzo card, and moves it to `review`. It never merges or deploys.
- Hermes home and OAuth state live on the `kanzo-agent-hermes-home` PVC. The dispatcher workspace registry and shared checkouts live on the `kanzo-agent-workspaces` PVC. Prometheus scrapes worker health and task counters at `/metrics`.
- Hermes Desktop connects to the remote `hermes serve` service on port `9119`, not to `hermes gateway run`. The service is published only through the Tailscale Ingress at `https://hermes.tail5389e9.ts.net`; it uses a username/password gate and stable signing secret. These values come from Vault as `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`, `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD`, and `HERMES_DASHBOARD_BASIC_AUTH_SECRET`.

## Credentials and enablement

1. Build and import the pinned Kanzo overlay with `sudo bash scripts/build-kanzo.sh`. The overlay patch is applied to the upstream tag and commit pinned in `versions.yml`; worker and adapter code are deployed from ConfigMaps.
2. Allow Argo CD to create the `kanzo-agent-vault-auth` ServiceAccount and `VaultAuth`.
3. Run `sudo bash scripts/bootstrap-mcp-vault.sh` to install the dedicated read-only Vault policy and Kubernetes role for `kv/mcp/kanzo-agent`.
4. In MCPProxy, create two agent tokens. The worker token should allow only Kanzo task read/claim/comment/update operations. The Hermes token should allow only read-only services and tools needed for research and context. Store them in Vault as `MCPPROXY_KANZO_AGENT_TOKEN` and `MCPPROXY_AGENT_READ_TOKEN`.
5. Create a LiteLLM virtual key restricted to the local analysis model alias and store it as `LITELLM_API_KEY`. Store a repository-scoped GitHub token as `GITHUB_TOKEN`; grant only repository contents write and pull-request write. Add all four values to Vault path `kv/mcp/kanzo-agent`. Never put them in Git or a task description.
   The same Vault path also contains the Hermes Desktop dashboard login values listed above. The dashboard password and signing secret are generated randomly and must remain in Vault.
6. The initial active catalog card is `hermes-harness-lab`, read-only, on `openai-api/iq_3s` with high reasoning. Add or edit project cards in `kanzo-project-catalog`; no per-project deployment is needed.
7. `KANZO_ENABLED=true` enables the catalog-driven worker. Invalid or duplicate active cards are moved to `review` before project tasks are claimed.

Keep all OAuth login state in Hermes' persistent home, not in this repository. When Codex or Anthropic is added later, create and authenticate a dedicated profile rather than sharing single-use OAuth state across profiles.

To connect Hermes Desktop, open **Settings → Gateways → Remote gateway**, enter `https://hermes.tail5389e9.ts.net`, sign in with the Hermes credentials in Vault, then save and reconnect. The Windows device must be connected to the same Tailscale tailnet.

## Local checks

Run the worker unit tests with:

```bash
python3 -m unittest discover -s images/kanzo-agent-worker -p 'test_*.py'
```

Render the application with:

```bash
kustomize build platform/kanzo-agent-worker
```
# WSL local repository runner

`platform/kanzo-agent-worker/run-host-runner.sh` runs the same Kanzo worker from WSL. It listens to the Kanzo SSE event stream and sends work to the resident Hermes executor and sandbox broker over Tailscale HTTPS. The in-cluster dispatcher only accepts catalog routes whose `runner_id` is `k3s`; a host runner accepts only its own runner ID.

Configure a project catalog card with:

```json
{
  "slug": "babmukdang-local",
  "repo": "KimDaegyu-Dev/Babmukdang-Backend",
  "source_kind": "local",
  "source_path": "/mnt/c/Github/Babmukdang/backend",
  "runner_id": "wsl-home",
  "runner_os": "wsl",
  "workspace_id": "babmukdang-backend",
  "model_lane": "coding",
  "provider": "openai-api",
  "model": "coder",
  "reason": "high",
  "profile": "builder",
  "read_only": false
}
```

The source path must be the absolute WSL path to the Git root. The checkout must be clean, including untracked files, and its `origin` must match `repo`. The runner clones that source into its persistent workspace directory; it never edits or stashes the source directory. Linked tasks reuse the configured `workspace_id` and one writer lease.

Start the runner from the WSL checkout with `bash platform/kanzo-agent-worker/launch-wsl-runner.sh`. It reads the worker, broker, and Langfuse Kubernetes Secrets through `k3s kubectl`, passes their values through process memory, and starts the runner as the current WSL user; it does not write credentials to a file. A non-root shell prompts for sudo access to K3s. Credentials are read once at launch, so after changing Vault values, wait for Vault Secrets Operator to sync and relaunch the WSL runner to load the new values. Set `KANZO_RUNNER_ID`, `KANZO_WORKSPACE`, and `KANZO_WORKER_STATE_DIR` before launch to override the defaults. The runner binds its local wake/metrics endpoint to `127.0.0.1`; its defaults use `kanzo.tail5389e9.ts.net`, `mcp.tail5389e9.ts.net`, `hermes-executor.tail5389e9.ts.net`, and `kanzo-broker.tail5389e9.ts.net`.

Local sources are currently supported on Linux/WSL runners. Windows drive paths and a native Windows runner are not accepted. The repo card's `repo` remains the GitHub remote used for PR publication. A task-level `read_only: true` may further restrict a writable project route; task settings cannot relax a project-level read-only restriction.
# Workspace and runner metrics

The worker exports low-cardinality Prometheus metrics for operations:

- `kanzo_worker_runner_info{runner_id,runner_os}` identifies the scraped worker process.
- `kanzo_worker_executor_ready` reports whether the configured resident Executor API is ready for claims.
- `kanzo_worker_workspace_registry_ready` reports whether the persistent registry can be read.
- `kanzo_worker_workspaces{state}` reports workspace counts by lifecycle state, including retained tombstones.
- `kanzo_worker_workspace_leases` and `kanzo_worker_workspace_dirty` report active writer leases and dirty workspaces.
- The worker sends a broker-authenticated runner heartbeat every 30 seconds. The broker exports `kanzo_runner_up{runner_id,runner_os}` and `kanzo_runner_last_seen_timestamp_seconds`; `up=0` means no heartbeat was received within 90 seconds. The runner registry is capped at 64 identities and prunes entries older than 30 days when a heartbeat arrives.

The `Kanzo Workspace Operations` Grafana dashboard uses these metrics. It deliberately avoids workspace IDs as labels to keep Prometheus cardinality bounded. Host runner process metrics remain loopback-only; runner connectivity is reported through the authenticated broker heartbeat instead.

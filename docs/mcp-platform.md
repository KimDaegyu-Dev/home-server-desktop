# Unified MCP platform

## Intended request path

```text
Agent client
  -> https://mcp.tail5389e9.ts.net/mcp (Tailnet HTTPS, OAuth login through Authentik)
  -> MCPProxy (single replica, tool allowlist, OTel spans)
       -> SearXNG MCP adapter -> private SearXNG JSON API -> public search engines
       -> Context7 documentation lookup
       -> ai-memory read-only MCP tools
       -> Graphiti project and event memory (query tools only; candidate facts)
       -> Graphify code graph for the pinned home-server-desktop revision
       -> Kanzo task board (delete tool excluded)
       -> Watch Skill video knowledge (read-only MCP tools behind bearer auth)
       -> Agent Browser (limited browse/read tools; no arbitrary eval)
       -> Grafana MCP (approved dashboard and Prometheus tools)
  -> Alloy -> Prometheus metrics and Langfuse traces
```

Only the MCP gateway is intended to be reachable by agent clients. SearXNG and all future backends stay on ClusterIP Services. MCPProxy is deliberately single replica: sessions and OAuth authorization state are process-local, and session affinity alone cannot make its approvals or OAuth flow safe to load balance. A restart invalidates active MCP sessions; the OAuth signing key remains stable because it is held in Vault. The gateway does not enable its raw session WAL, so prompts and tool payloads are not durably copied into a second log store.

## First implementation: search and gateway

`platform/mcp-stack` contains SearXNG, the read-only `searxng_search` adapter, Context7, the Grafana MCP server, MCPProxy, and the Tailnet ingress. The gateway exposes a reviewed allowlist for SearXNG, Context7, Grafana, ai-memory, Graphiti, Graphify, Kanzo, Watch Skill, and Agent Browser. The search adapter returns source URLs and snippets, and describes search results as untrusted input. JSON search output is enabled because SearXNG returns 403 when a client asks for a format absent from `search.formats`.

MCPProxy reaches Grafana MCP over its ClusterIP DNS name. Grafana MCP's DNS-rebinding protection allowlists that exact service host and port. Keep the allowlist aligned with the backend URL; a custom `Host` entry in backend headers may not override the HTTP transport's host derived from the URL.

Kanzo keeps its own Host and Origin guard enabled. Its pod hostname is fixed to `kanzo`; an `ExternalName` alias in the `mcp` namespace lets MCPProxy connect to `http://kanzo:4242/mcp`, so the request Host matches the hostname Kanzo accepts. The Kanzo NetworkPolicy still limits connections to pods in `mcp`.

MCPProxy exposes aggregated tool names as `<backend>_<tool>`, but its call policy evaluates the original upstream tool name. Keep both the prefixed and bare names in `allowed_tools`: the prefixed name keeps the tool visible in `tools/list`, and the bare name permits its routed call.

SearXNG has no UI ingress or PVC. Its cache and `/tmp` are ephemeral; its queries are sent to external search engines and may be sensitive. Context7 is used through its public remote MCP endpoint without an API key, so it has low provider rate limits and receives library lookup queries. Grafana MCP stays on its internal ClusterIP; its previous direct Tailnet ingress is removed so agent clients have one MCP endpoint. MCPProxy receives only the Grafana MCP server token from Vault through a role that can read only `kv/monitoring/grafana-mcp`. The gateway permits dashboard reads, Prometheus queries, folder creation, and dashboard updates; datasource creation and updates remain blocked. SearXNG's session-encryption secret is in `kv/mcp/searxng`. MCPProxy's Authentik client secret and stable P-256 OAuth signing key are in `kv/mcp/mcpproxy`; the matching Authentik client secret is added to `kv/authentik/sso-clients`. Rotate these values only through a documented Vault and Authentik client rotation.

MCPProxy keeps session and OAuth login state in memory. Its short-lived outbound-token state directory is ephemeral because the initial backends do not use outbound OAuth. On restart, agents log in again. The OAuth signing key is backed up as part of Vault's protected Raft snapshot. The search service itself stores no user corpus. Do not turn on `wal_dir` without deciding content redaction, retention, access, and backup first.

The MCP ingress is Tailnet-only through Tailscale Operator and uses the hostname `mcp.tail5389e9.ts.net`. Authentik provides the OAuth login for MCP clients; the gateway then issues its own ES256 client token. The lightweight Python sidecar publishes RFC 9728 protected-resource metadata and adds the `resource_metadata` challenge parameter that MCP clients need to discover the embedded authorization server. It forwards MCP, OAuth, health, and metrics requests to MCPProxy over loopback. The Authentik provider callback is fixed to `https://mcp.tail5389e9.ts.net/callback`. Local bootstrap must finish before GitOps exposes the Ingress: create the MCP client in Vault and run `scripts/bootstrap-mcp-vault.sh` before syncing the MCP Application.

## Data and persistence decisions for the remaining systems

| Service | Source of truth | Data written | Persistence / backup decision | Exposure |
| --- | --- | --- | --- | --- |
| ai-memory | Existing WSL `/home/eorbr/ai-memory/ai-memory/data` plus checked backup | Existing SQLite, wiki, model files, bearer-protected MCP | Source stopped and archived before migration; PVC import uses per-file SHA-256 comparison. Source and archive remain intact. New PVC is 2Gi. | MCPProxy only; read-only tool allowlist until a reviewed-write flow is implemented. |
| Graphiti | Approved project and interaction events | Temporal entity and relationship graph; inferred facts must be candidates until reviewed | FalkorDB and multilingual FastEmbed model cache use PVCs; daily RDB snapshots remain on a separate PVC for 14 days. This is same-node protection, not off-node backup; export encrypted copies off-node before storing valuable user history. | ClusterIP only. The reference MCP server has no native auth, so never expose it directly. |
| Graphify | Public `home-server-desktop` repository at the pinned revision in `versions.yml` | Rebuildable AST code graph; docs and non-code files are excluded so no semantic model upload occurs | Rebuilt in an init container from the exact commit; graph stored on a 1Gi PVC and source revision recorded alongside it. Inferred edges remain labeled as inferred and are not promoted to confirmed memory. | ClusterIP plus NetworkPolicy from `mcp`; query tools only through MCPProxy. |
| Snowball | Existing Compose source and named-volume data | Research documents, ontology/index data in Fuseki, Neo4j, Qdrant and custom APIs | Source path and Docker volume export are not yet located. Preserve the old data first; do not deploy replacements or prune the originals until checksums, counts, and restore are verified. | No ingress until service inventory and access controls are known. |
| Context7 | Upstream Context7 documentation service | Search query and library/version selection go to the external provider; no local corpus | No local persistence. Add an API key from Vault if rate limits require it; do not commit a provider token. | Outbound MCP backend only. |
| agent-browser | Target websites and transient browser session | Page content and screenshots may contain personal data | Ephemeral browser profile, 1Gi temporary storage, 256Mi shared memory, and 2Gi memory limit; no host browser profile, Docker socket, or persistent downloads. NetworkPolicy permits public HTTP(S) only and excludes common private/reserved ranges. | MCPProxy only; no browser UI ingress. The backend also requires a bearer token and the gateway exposes only the reviewed browse/read subset. |
| watch-skill | Existing `/home/eorbr/.watch-skill` index and lesson databases | `watch_video` ingests a video and writes its index, frames and cache; `ask_video` and `get_moment` query indexed content | The supplied checked archive contains `index.db` only; it does not move source videos or local files. Import it into a 5Gi PVC, cap cache at 2Gi, and make nightly SQLite-consistent backups to a separate 5Gi PVC with 14-day/two-copy retention. Keep the WSL source and initial archive intact. Backups on the same K3s node are not an off-node backup. | MCPProxy only through a constant-time bearer-auth sidecar; allow only `watch_video`, `ask_video`, `get_moment`. No UI ingress, `capture`, `forget`, or workspace-control tools. `watch_video` can fetch public URLs and write durable data. |
| Kanzo | New task board; no prior Kanzo database was found on the WSL/Windows source paths | Tasks and project notes in SQLite | 2Gi local-path PVC; use SQLite's online backup API and preserve config/attachments. UI remains internal until an Authentik-protected route is available. | ClusterIP plus NetworkPolicy from `mcp`; task create/update tools are enabled, delete is excluded. |
| LiteLLM | Existing LiteLLM deployment and PostgreSQL | Requests, usage, cost, virtual keys | Reuse the existing endpoint and database; do not deploy a second proxy or database. API keys remain in Vault. | Existing Tailnet HTTPS endpoint; MCPProxy only receives a deliberately scoped credential if a backend requires it. |
| Langfuse | Existing Langfuse deployment and ClickHouse/PostgreSQL | Prompt/response traces and evaluations | Existing PVC backup plan applies; trace retention must be bounded before adding more spans. Avoid duplicating prompt bodies in MCPProxy WAL. | Existing Tailnet HTTPS endpoint. |

The WSL and dualboot clusters share the manifests but not their PVC contents. WSL is the active target. Copying state to dualboot is an explicit backup/restore operation, not a second live writer.

## Observability and evaluation

MCPProxy exports OTLP spans to the existing Alloy receiver and exposes Prometheus metrics. LiteLLM continues to report model latency, token usage, and cost through the current Alloy-to-Prometheus and Alloy-to-Langfuse path. Tool spans and model spans can appear in Langfuse, but the same trace is not proven merely because both arrive: the agent must propagate W3C `traceparent`/`tracestate`, and that context must reach LiteLLM. Verify this end to end before claiming a single linked trace. Evaluation scores need an explicit Langfuse scoring step; they are not automatically created by OTLP export.

Each tool addition must pass the following before it is put on the gateway allowlist:

1. Check backend health and the exact `tools/list` names.
2. Check a fresh client OAuth flow and confirm the MCP endpoint is the only configured server.
3. Make one representative request and confirm only intended tools were called.
4. Inspect Langfuse for the tool span, LiteLLM model span, trace/session identifiers, latency and cost; attach a score through a verified evaluation path.
5. Confirm inferred content remains a candidate until a user reviews it, and that no search result or tool response is promoted to confirmed memory by default.

## Rollout order

1. Run `scripts/bootstrap-mcp-vault.sh` against the in-cluster Vault using the protected init token file. It reuses existing values and never prints secrets.
2. Commit/push the manifests. Wait for `ai-credentials` to create the service accounts and VaultAuth objects, then verify both new VaultStaticSecrets are `Ready=True`.
3. Wait for `mcp-stack` to become `Synced / Healthy`; verify SearXNG `/healthz`, JSON API search, MCPProxy `/healthz`, `/metrics`, OAuth metadata, and exactly the single allowed search tool.
4. Confirm the Tailnet Ingress address, complete Authentik OAuth from a real client, and sync the single endpoint into Windows and WSL clients with Skillshare.
5. Context7, Grafana MCP, Graphify, Kanzo, ai-memory, and Watch Skill are configured in Git. Graphify builds a code-only graph from a pinned revision, without the semantic pass that sends documents to a model. ai-memory and Watch Skill migrations preserve their original sources and verify checksums/integrity. Run the matching Vault bootstrap and migration procedures in their service runbooks before relying on imported data. The MCP gateway exposes only the approved tools; inferred edges remain labeled and must not be saved as confirmed facts without user review.
6. Run `sudo bash scripts/bootstrap-watch-skill-vault.sh` after the LiteLLM deployment is healthy. It creates or reuses a virtual key limited to LiteLLM's `iq_3s` model group and API routes, stores the MCP bearer separately, and never prints either value. Wait for both Watch Skill VaultStaticSecrets to become Ready. After the Watch Skill Application has created its PVC and runtime ConfigMap, run `sudo bash scripts/migrate-watch-skill-to-k3s.sh /home/eorbr/.watch-skill/backups/watch-skill-20261109T165612Z.tar.gz`; it checks the checksum, imports into an empty PVC, and verifies SQLite integrity without deleting the source or archive. The archive contains only `index.db`; local media files remain on WSL and are not mounted into K3s. New URLs passed to `watch_video` are fetched and indexed in the pod. Confirm the three allowed tools through MCPProxy. The package's doctor and extractor self-healing can download or update ffmpeg/yt-dlp/deno dynamically; do not expose doctor or capture tools, and treat binary reproducibility as a remaining deployment constraint until those assets are pinned in a built image.
7. Run `sudo bash scripts/bootstrap-graphiti-vault.sh` to create or reuse a LiteLLM key restricted to the `iq_3s` model, the FalkorDB password, and the Agent Browser backend token. The script creates the narrow Vault policies and Kubernetes roles used by VSO; secret values are never printed. The first Argo CD bootstrap builds and imports the pinned browser and FastEmbed images with Buildah, without Docker. Graphiti uses a local multilingual 384-dimensional FastEmbed endpoint; only the embedder pod downloads model files, and Graphiti sends embedding requests to it over ClusterIP. Its candidate group starts empty and its MCP surface is read-only. The browser can visit public HTTP(S) destinations permitted by NetworkPolicy. Verify backend health and exact aggregate tool names before client use.
8. Locate Snowball's original Compose source and named-volume data before migrating it. Current searches did not locate a source tree or volume export under the checked Windows/WSL paths. Keep any discovered source intact through backup, import, count/hash comparison, query verification, and restore test. Do not deploy a replacement from guessed defaults.

## Skillshare client configuration

Skillshare is a local CLI that writes native MCP client config; it is not the MCP gateway. Its pinned release and verified installers are in `versions.yml` and `scripts/install-skillshare.*`. The tracked project config is `.skillshare/config.yaml`, and approved skills live under `.skillshare/skills/`. The initial `home-server` entry targets Codex and contains only the Tailnet MCP URL; Authentik OAuth is handled by the client and no credential is stored in Git.

Install on Windows with `.\scripts\install-skillshare.ps1`, or in WSL/Linux with `bash scripts/install-skillshare.sh`. Preview changes with `skillshare sync mcp --project --dry-run --json`; apply with `skillshare sync mcp --project`. Skillshare writes the project-scoped `.codex/config.toml`; Codex must trust the repository before project MCP servers load. Do not remove the pre-existing local gateway or direct Grafana entries until their remaining tools have been migrated and checked through MCPProxy. Then remove those direct entries through Skillshare and verify that clients list only MCPProxy.

Only reviewed skills belong under `.skillshare/skills/`. Use the included `home-server-mcp` skill for source citations and the rule that inferred facts remain review candidates until the user confirms them. Discover external skills with a `find-skills` workflow, inspect their source and permissions, and add them only after human approval. Skillshare MCP checks can confirm URL reachability and the OAuth challenge, but Skillshare does not perform the browser sign-in; a user-authorized client must complete OAuth before tool calls can be verified. The project config is tracked in `.skillshare/config.yaml`; `.codex/` is generated client output and is ignored.

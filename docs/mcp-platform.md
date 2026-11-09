# Unified MCP platform

## Intended request path

```text
Agent client
  -> https://mcp.tail5389e9.ts.net/mcp (Tailnet HTTPS, OAuth login through Authentik)
  -> MCPProxy (single replica, tool allowlist, OTel spans)
       -> SearXNG MCP adapter -> private SearXNG JSON API -> public search engines
       -> Context7 / project-memory / code-graph / task / browser / watch tools (to be added)
       -> existing Grafana MCP and LiteLLM endpoints (to be added)
  -> Alloy -> Prometheus metrics and Langfuse traces
```

Only the MCP gateway is intended to be reachable by agent clients. SearXNG and all future backends stay on ClusterIP Services. MCPProxy is deliberately single replica: sessions and OAuth authorization state are process-local, and session affinity alone cannot make its approvals or OAuth flow safe to load balance. A restart invalidates active MCP sessions; the OAuth signing key remains stable because it is held in Vault. The gateway does not enable its raw session WAL, so prompts and tool payloads are not durably copied into a second log store.

## First implementation: search and gateway

`platform/mcp-stack` contains SearXNG, a read-only `searxng_search` MCP adapter, the MCPProxy Deployment and the Tailnet ingress. The gateway allows only `searxng_search` until each additional tool has an explicit source, scope, and data policy. The adapter returns source URLs and snippets, and describes search results as untrusted input. JSON search output is enabled because SearXNG returns 403 when a client asks for a format absent from `search.formats`.

The SearXNG service has no UI ingress and no PVC. Its cache and `/tmp` are ephemeral; its request path is sent to external search engines and should be treated as potentially sensitive. Its session-encryption secret is in Vault at `kv/mcp/searxng`. MCPProxy's Authentik client secret and stable P-256 OAuth signing key are in `kv/mcp/mcpproxy`; the matching Authentik client secret is added to `kv/authentik/sso-clients`. The VSO roles can read only these MCP paths. Rotate these values only through a documented Vault and Authentik client rotation.

MCPProxy keeps session and OAuth login state in memory. Its short-lived outbound-token state directory is ephemeral because the initial backends do not use outbound OAuth. On restart, agents log in again. The OAuth signing key is backed up as part of Vault's protected Raft snapshot. The search service itself stores no user corpus. Do not turn on `wal_dir` without deciding content redaction, retention, access, and backup first.

The MCP ingress is Tailnet-only through Tailscale Operator and uses the hostname `mcp.tail5389e9.ts.net`. Authentik provides the OAuth login for MCP clients; the gateway then issues its own ES256 client token. The lightweight Python sidecar publishes RFC 9728 protected-resource metadata and adds the `resource_metadata` challenge parameter that MCP clients need to discover the embedded authorization server. It forwards MCP, OAuth, health, and metrics requests to MCPProxy over loopback. The Authentik provider callback is fixed to `https://mcp.tail5389e9.ts.net/callback`. Local bootstrap must finish before GitOps exposes the Ingress: create the MCP client in Vault and run `scripts/bootstrap-mcp-vault.sh` before syncing the MCP Application.

## Data and persistence decisions for the remaining systems

| Service | Source of truth | Data written | Persistence / backup decision | Exposure |
| --- | --- | --- | --- | --- |
| ai-memory | Existing local AI Memory workspace and staged import | Curated user/project memories, token-protected API | Preserve current staged import and origin; do not reindex or overwrite until a source-vs-import count/hash comparison is complete. Back up the database and source documents. | MCPProxy only, after read/write scopes and review flow are verified. |
| Graphiti | Approved project and interaction events | Temporal entity and relationship graph; inferred facts must be candidates until reviewed | PVC for graph DB, separate encrypted snapshots, restore test. Begin with read/search tools only; no delete/clear/confirm tool through MCPProxy. | ClusterIP only. The reference MCP server has no native auth, so never expose it directly. |
| Graphify | Git repositories selected by the user | Rebuildable static code graph, no private keys or generated build outputs | Generated graph is rebuildable from pinned commits. Store a shallow read-only checkout and graph artifact on a bounded PVC; record source commit IDs. | ClusterIP only; read-only queries through MCPProxy. |
| Snowball | Existing Compose source and named-volume data | Research documents, ontology/index data in Fuseki, Neo4j, Qdrant and custom APIs | Source path and Docker volume export are not yet located. Preserve the old data first; do not deploy replacements or prune the originals until checksums, counts, and restore are verified. | No ingress until service inventory and access controls are known. |
| Context7 | Upstream Context7 documentation service | Search query and library/version selection go to the external provider; no local corpus | No local persistence. Add an API key from Vault if rate limits require it; do not commit a provider token. | Outbound MCP backend only. |
| agent-browser | Target websites and transient browser session | Page content and screenshots may contain personal data | Ephemeral browser profile by default; no host browser profile, Docker socket, or persistent downloads. Restrict egress to required sites when an allowlist is known. | MCPProxy only; no browser UI ingress. |
| watch-skill | Existing local videos, screen captures, notes, and index DB | Media and human-reviewed lessons | Preserve `/home/eorbr/.watch-skill/index.db`, `.config/watch-skill`, and the existing backup archive. Set an explicit media-retention policy and backup path before moving data. Never mount `docker.sock`. | MCPProxy only; restrict tools to read/annotate until destructive operations are reviewed. |
| Kanzo | Current Kanzo SQLite task database | Tasks and project notes | SQLite PVC and consistent SQLite backup (`VACUUM INTO` or online backup API); test restore. | ClusterIP only because upstream Kanzo has no login. Authentik protection is required before any UI route. |
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
5. Add Context7, existing Grafana MCP, project memory, and code graph one backend at a time. Each backend gets the narrowest Vault policy and explicit tool allowlist.
6. Locate Snowball's original Compose and named-volume data before migrating it. Keep source volumes intact through backup, import, count/hash comparison, query verification, and restore test.

## Skillshare client configuration

Skillshare is a local CLI that writes native MCP client config; it is not the MCP gateway. Keep the portable MCP entry and approved skills in a Skillshare config repository. Each Windows/WSL agent host installs Skillshare and runs `skillshare sync --all`; credentials are environment references or the client's OAuth session, never repository values. Use a `find-skills` skill to discover and review community skills, but require explicit human approval before installation. A separate Skillshare HTTP MCP server is unnecessary for clients on the same workstation and would duplicate skill listings.

# Authentik and cluster SSO

Authentik is deployed by Argo CD into the `authentik` namespace and exposed through a Tailscale Operator Ingress. Its PostgreSQL database is a separate Helm release with an 8 GiB `local-path` PVC. Database credentials and Authentik's signing key are stored in Vault KV and synchronized by VSO; no credential values belong in Git.

## Bootstrap Authentik

1. Push the GitOps configuration and wait for Argo CD to create the `authentik` namespace, `authentik-vault-auth` ServiceAccount, and VaultStaticSecret resources. The database and Authentik pods wait for their VSO-managed Secrets until the next step completes.
2. From Ubuntu, run the bootstrap script and enter the Vault admin token at its hidden prompt:

   ```bash
   cd ~/src/home-server-desktop
   sudo bash ./scripts/bootstrap-authentik-vault.sh --token-file /home/eorbr/vault-init.json
   ```

   The script reads the root token from the protected init file without printing it, creates a narrowly scoped Vault policy and Kubernetes auth role, generates unique credentials, writes them to `kv/authentik/postgresql` and `kv/authentik/config`, and removes temporary files. It refuses to overwrite existing Authentik KV data. Omit `--token-file ...` to enter a Vault token at the hidden prompt instead.

3. Wait for the `platform-credentials`, `authentik-postgresql`, and `authentik` Argo CD Applications to become Healthy. Confirm VSO reports both VaultStaticSecrets Ready and both Deployments Ready.
4. Open `https://authentik.tail5389e9.ts.net/if/flow/initial-setup/` from a tailnet-connected device. Set the `akadmin` password and recovery email, then store the password in a password manager.
5. Keep `akadmin` and the current Argo CD admin/Vault root-token recovery paths until every OIDC integration is tested. Do not remove local break-glass access as part of the first SSO rollout.

If the initial setup flow is unavailable after pods become healthy, use Authentik's documented worker recovery command rather than changing Vault secrets.

## SSO rollout

Configure native OIDC for Argo CD, Vault, and Grafana. Use a separate Authentik OAuth2/OpenID provider and client secret for each service. Keep each provider's redirect URI exact, scope Vault roles and Argo CD permissions to explicit Authentik groups, and sync each client secret from Vault through VSO. For applications without native OIDC, use an Authentik Proxy Provider only when the app's session and API behavior are compatible.

Roll out one service at a time. Verify an SSO login in a private browser session, check the expected role, then verify local break-glass login still works before moving to the next service. Keep tailnet access and the existing Tailscale Ingress layer in place.

## Recovery and backup

Back up the Authentik PostgreSQL database and the Vault Raft snapshot outside the cluster. A PVC alone does not protect against loss of the single WSL/Linux host. The Authentik `secret_key` is required to preserve signed sessions and encrypted values across restores; keep it in Vault and restore it with the database.

## References

- [Authentik Kubernetes installation](https://docs.goauthentik.io/install-config/install/kubernetes/)
- [Authentik configuration](https://docs.goauthentik.io/install-config/configuration/)
- [Authentik first steps](https://docs.goauthentik.io/install-config/first-steps/)
- [Authentik OIDC providers](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/)
- [Argo CD OIDC user management](https://argo-cd.readthedocs.io/en/latest/operator-manual/user-management/)
- [Vault JWT/OIDC auth method](https://developer.hashicorp.com/vault/docs/auth/jwt)

## Connect cluster services to Authentik

The GitOps blueprint creates separate OIDC clients for Grafana, Argo CD, and Vault. Their client secrets are generated locally and stored in Vault; the blueprint reads them from VSO-synced Kubernetes Secrets. Grafana SSO assigns the `Editor` role so the account can edit dashboards while local admin login stays available for recovery. Argo CD maps the Authentik `authentik Admins` group to `role:admin`; the local admin remains enabled. Vault's `home-server-admin` OIDC role is bound to the `akadmin` username, while token login remains enabled.

Before syncing the Authentik chart changes, seed the generated client secrets into Vault:

```bash
sudo bash ./scripts/bootstrap-sso-vault.sh --token-file /home/eorbr/vault-init.json --seed-only
```

After the Authentik chart and its blueprint have created the three OIDC providers, configure Vault OIDC and provision Argo CD's client secret:

```bash
sudo bash ./scripts/bootstrap-sso-vault.sh --token-file /home/eorbr/vault-init.json --configure-only
```

Wait for Authentik, Grafana, and Argo CD to reload their OIDC configuration. Sign in to Grafana or Argo CD with the Authentik button. In Vault, choose the `oidc` auth method and role `home-server-admin`. Keep local login as a break-glass path until each SSO flow has been verified.

## Grafana MCP

The `grafana-mcp` Argo CD Application runs Grafana's Streamable HTTP MCP server at `https://grafana-mcp.tail5389e9.ts.net/mcp`. Tailscale provides HTTPS and network access is restricted to the tailnet. MCP requests also require a bearer token. The server uses a dedicated Grafana `Editor` service account and exposes search, datasource, Prometheus, dashboard, and folder tools; it does not receive Grafana administrator credentials.

After Grafana is healthy and the SSO bootstrap above has run, create the service account and Vault credentials:

```bash
sudo bash ./scripts/bootstrap-grafana-mcp.sh --token-file /home/eorbr/vault-init.json
```

The MCP caller token is stored at `kv/monitoring/grafana-mcp`, field `mcp_server_token`. The service-account token is stored beside it and synchronized only into the MCP namespace. Do not put either token in Git. The Windows Codex client can use this MCP URL with the caller token supplied through the `GRAFANA_MCP_TOKEN` environment variable.

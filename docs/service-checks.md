# 서비스 로그인과 기본 점검

서비스 주소는 Tailscale tailnet에 연결된 기기에서만 열립니다. 브라우저에서 아래 HTTPS 주소로 접속하고, 인증 화면에서는 Authentik 계정을 사용합니다.

| 서비스 | 주소 | 확인할 내용 |
| --- | --- | --- |
| Authentik | <https://authentik.tail5389e9.ts.net> | 계정으로 로그인 |
| Argo CD | <https://argocd.tail5389e9.ts.net> | Authentik 로그인 후 Applications가 `Synced` / `Healthy`인지 확인 |
| Vault | <https://vault.tail5389e9.ts.net> | 로그인 화면에서 OIDC/Authentik 로그인 |
| Grafana | <https://grafana.tail5389e9.ts.net> | Authentik 로그인 후 대시보드 열기 |
| Prometheus | <https://prometheus.tail5389e9.ts.net> | Graph 화면에서 PromQL 조회 |
| Langfuse | <https://langfuse.tail5389e9.ts.net> | Authentik 로그인 후 Tracing의 Traces 확인 |
| LiteLLM | <https://litellm.tail5389e9.ts.net> | Authentik 로그인 후 Keys 메뉴에서 API 키 발급 |
| Grafana MCP | <https://grafana-mcp.tail5389e9.ts.net/mcp> | MCP 클라이언트에 Streamable HTTP endpoint로 등록 |

## Langfuse 프로젝트 키 만들기

1. Langfuse에 Authentik으로 로그인합니다. 프로젝트가 없으면 먼저 프로젝트를 만듭니다.
2. 프로젝트를 선택하고 **Project settings → API Keys → Create new API keys**를 엽니다.
3. Alloy가 trace를 보내기만 하도록 권한 선택 화면에서 **Ingestion**을 선택합니다.
4. 생성 직후 표시되는 Public Key와 Secret Key를 복사합니다. Secret Key는 생성 화면에서만 확인할 수 있습니다.
5. WSL Ubuntu 저장소 디렉터리에서 다음 명령을 실행합니다. Vault admin token, 두 Langfuse 키는 숨김 입력됩니다.

   ```bash
   cd ~/src/home-server-desktop
   sudo bash scripts/bootstrap-ai-vault.sh --langfuse-project-keys
   ```

   스크립트가 `kv/ai/langfuse-exporter`에 저장하고 Vault Secrets Operator 동기화 후 Alloy를 재시작합니다. 키를 채팅, Git, 셸 명령 인자로 입력하지 마세요.

6. 아래 명령으로 Alloy 파드가 재시작 후 Ready인지 확인하고, 로그에 HTTP 401이 사라졌는지 봅니다.

   ```bash
   sudo k3s kubectl -n monitoring get pods -l app.kubernetes.io/name=alloy
   sudo k3s kubectl -n monitoring logs deployment/alloy --since=10m
   ```

7. LiteLLM 테스트 요청을 보낸 다음 Langfuse의 **Tracing → Traces**에서 trace를 확인합니다.

프로젝트 키는 Langfuse의 project-scoped API key입니다. 키 생성 화면에서 Ingestion 권한을 지원하지 않는 버전이면, trace 전송이 가능한 최소 권한을 선택하세요. Secret Key를 분실하면 다시 표시할 수 없으므로 새 키를 만든 뒤 기존 키를 폐기해야 합니다.

## 서비스 동작 확인

### Argo CD와 클러스터

웹 UI의 Applications에서 모든 앱의 동기화/건강 상태를 확인합니다. 터미널에서는 다음을 실행합니다.

```bash
sudo k3s kubectl -n argocd get applications
sudo k3s kubectl get ingress -A
sudo k3s kubectl get pods -A
```

모든 앱이 `Synced` / `Healthy`이고, 서비스 파드가 Ready인지 확인합니다. Vault는 수동 unseal이 필요한 구성이므로 재부팅 후에는 Vault UI와 `vault status`에서 unsealed 상태도 확인합니다.

### Prometheus와 Grafana

Prometheus Graph에서 아래 쿼리를 각각 실행합니다.

```promql
up
node_memory_MemAvailable_bytes
kube_pod_status_ready
```

`up` 결과가 있고 주요 타깃이 `1`이면 스크레이프가 동작 중입니다. Grafana에서는 Kubernetes/노드 대시보드를 열어 CPU, 메모리, 파드 상태를 봅니다. Prometheus 외부 주소도 tailnet 접근만 허용됩니다.

### LiteLLM 모델 API와 Langfuse trace

LiteLLM UI의 **Keys** 메뉴에서 virtual key를 발급합니다. Windows PowerShell에서 키를 숨겨 입력하고 사용합니다.

```powershell
$secure = Read-Host "LiteLLM virtual key" -AsSecureString
$env:LITELLM_API_KEY = [Net.NetworkCredential]::new("", $secure).Password
$headers = @{ Authorization = "Bearer $env:LITELLM_API_KEY" }
Invoke-RestMethod -Uri "https://litellm.tail5389e9.ts.net/v1/models" -Headers $headers
```

모델 목록에 `coder`, `iq_3s`가 있는지 확인한 뒤 각 모델에 짧은 chat completion을 보냅니다.

```powershell
$body = @{
  model = "coder"
  messages = @(@{ role = "user"; content = "Reply with: coder route OK" })
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Uri "https://litellm.tail5389e9.ts.net/v1/chat/completions" `
  -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

`model` 값을 `iq_3s`로 바꿔 한 번 더 실행합니다. 두 요청이 성공하면 LiteLLM UI의 Logs/Spend와 Langfuse Traces를 확인합니다. LiteLLM 키는 UI 로그인 세션과 별개이며 API 호출에는 virtual key가 필요합니다.

끝나면 현재 PowerShell 세션에서 키를 지웁니다.

```powershell
Remove-Item Env:LITELLM_API_KEY
```

### Grafana MCP

MCP 클라이언트의 Streamable HTTP 서버 주소는 `https://grafana-mcp.tail5389e9.ts.net/mcp`입니다. 요청에는 Vault의 `kv/monitoring/grafana-mcp`에 저장된 `mcp_server_token`을 Bearer token으로 넣습니다. Grafana MCP 연결이 되면 `search`, `datasource`, `prometheus`, `dashboard`, `folder` 도구로 조회·대시보드 작업을 확인할 수 있습니다. 토큰을 브라우저 URL이나 Git에 넣지 마세요.

## GitOps 자동 동기화 기본값

WSL과 dualboot ApplicationSet은 모든 생성 Application에 자동 sync, self-heal, prune을 설정합니다. 새 앱은 `clusters/<profile>/apps/catalog/`에 Application 설명 파일을 추가하고 stage label을 지정하면 이 설정을 상속합니다. prune이 켜져 있으므로 카탈로그 파일을 삭제하면 생성된 Application과 그 Application이 관리하던 리소스도 제거됩니다.

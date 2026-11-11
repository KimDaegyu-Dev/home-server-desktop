# LiteLLM, Headroom, Langfuse

## 구성

WSL 프로필은 Tailscale Ingress로 `litellm.tail5389e9.ts.net`과 `langfuse.tail5389e9.ts.net`을 제공합니다. 두 UI는 Authentik OIDC를 사용하고, LiteLLM API는 별도의 virtual key로 인증합니다. LiteLLM의 Headroom callback은 `coder`, `iq_3s` 요청을 기존 Windows llama-swap으로 전달하기 전에 메시지를 압축합니다. LiteLLM OTLP telemetry는 Alloy로 전달하며, Alloy는 메트릭을 기존 Prometheus에 쓰고 trace를 Langfuse에 보냅니다.

Langfuse trace에는 프롬프트와 응답 내용이 포함됩니다. Langfuse OSS self-hosted 구성은 기본적으로 trace를 자동 만료하지 않으므로 ClickHouse PVC 사용량을 감시합니다. `LangfusePersistentVolumeNearlyFull` 경보는 Langfuse PVC가 85% 이상 찼을 때 발생합니다.

## 최초 설치

1. WSL Ubuntu에서 최신 저장소를 받은 뒤 `sudo bash scripts/bootstrap-ai-vault.sh`를 실행합니다. Vault admin token은 숨김 입력합니다. 스크립트는 앱 암호화 키, DB 암호, LiteLLM master key, 브리지 토큰, Authentik OAuth client secrets를 생성해 Vault에 넣습니다. 값은 터미널에 표시하지 않습니다. Langfuse 전용 Authentik secret은 `ai/langfuse-auth`에 별도로 저장하고, Authentik namespace에서만 공유되는 클라이언트 secret 경로를 Langfuse에 복제하지 않습니다. `ai/langfuse` 또는 `ai/litellm` 경로가 이미 있으면 회전을 막기 위해 초기화를 중단합니다.

   The initial bootstrap also stores nonfunctional placeholder credentials for the Langfuse exporter. They let Alloy start before the Langfuse project API key exists; replace them with the real project keys in step 4.

2. LiteLLM Helm chart와 공식 이미지는 `docker.litellm.ai/berriai` 미러에서 가져옵니다. Headroom callback을 포함한 이미지는 WSL의 Buildah로 빌드한 뒤 K3s containerd에 직접 가져옵니다. Docker Engine과 GHCR은 필요하지 않습니다. 새 호스트는 `bootstrap-argocd.yml`이 Argo CD root Application을 적용하기 전에 이미지를 빌드합니다. 실행 중인 WSL 호스트에서 다시 빌드하려면 다음을 실행합니다.

   ```bash
   sudo bash scripts/build-litellm-headroom.sh
   ```

   강제 재빌드가 필요하면 `--force`를 추가합니다. 버전은 `versions.yml`에 고정합니다.

3. Argo CD는 앱들을 자동 동기화합니다. ClickHouse Operator와 AI Vault/VSO 자격 증명 등 선행 리소스가 먼저 Ready인지 확인하고, VaultStaticSecret이 Ready가 될 때까지 기다립니다. 의존성 리소스가 아직 없으면 sync retry를 확인합니다. LiteLLM DB 스키마 migration은 Argo CD PreSync hook으로 동기화할 때 실행됩니다.

4. Langfuse UI에서 사용자와 프로젝트를 만들고 프로젝트 API key를 발급합니다. 다음 명령을 실행해 키를 숨겨 입력하고 Alloy로 전달합니다. 키 생성 절차와 서비스 검증은 [`service-checks.md`](service-checks.md)를 참고합니다.

   ```bash
   sudo bash scripts/bootstrap-ai-vault.sh --langfuse-project-keys
   ```

5. Windows에서 관리자 PowerShell로 `scripts/windows/setup-strata-bridge.ps1`을 실행하고, Vault의 `ai/litellm` 경로에 저장된 `STRATA_BRIDGE_TOKEN`을 숨김 입력합니다. 스크립트는 Windows loopback `127.0.0.1:8081`의 llama-swap 앞에 인증 브리지를 만들고, WSL 주소 대역에서 오는 TCP 18081만 방화벽에서 허용합니다. 브리지는 로그인 시 자동 시작합니다. WSL 네트워크 대역이 바뀌면 스크립트를 다시 실행해 방화벽 규칙을 갱신합니다.

6. WSL에서 Ansible `prepare-host.yml`을 적용합니다. systemd timer가 Windows-host gateway를 읽어 `litellm/strata-bridge` Kubernetes Endpoint를 갱신합니다.

7. Authentik에서 callback 주소를 확인합니다.
   - LiteLLM: `https://litellm.tail5389e9.ts.net/sso/callback`
   - Langfuse: `https://langfuse.tail5389e9.ts.net/api/auth/callback/authentik`

## 모델 API와 Headroom

LiteLLM에는 `coder`, `iq_3s` 별칭이 설정됩니다. 클라이언트는 `https://litellm.tail5389e9.ts.net/v1`에 LiteLLM virtual key를 사용합니다. UI master key는 API 클라이언트에 쓰지 않습니다. 두 모델은 LiteLLM에서 Windows 브리지로 연결되어 기존 llama-swap 모델 전환 흐름을 따릅니다.

커스텀 LiteLLM 이미지의 `headroom_proxy_callback.instance`가 `HeadroomCallback` 인스턴스를 제공합니다. callback 오류가 나면 Headroom은 원본 메시지를 전달하는 fail-open 동작을 하므로 운영 로그에서 callback 오류를 살펴봅니다.

## Langfuse 사용자와 세션 추적

LiteLLM OTel v2는 요청 `metadata.trace_user_id`를 사용자 ID로, `metadata.session_id`를 세션 ID로 내보냅니다. LiteLLM의 metadata allowlist와 Alloy의 OTel 변환은 이 값을 Langfuse가 인식하는 `user.id`, `session.id` trace 속성으로 전달하도록 구성합니다.

클라이언트는 요청마다 다음 metadata를 포함해야 합니다.

```json
{
  "metadata": {
    "trace_user_id": "home-server-user",
    "session_id": "chat-session-unique-id"
  }
}
```

`trace_user_id`는 같은 사용자의 요청에서 안정적으로 유지하고, `session_id`는 같은 대화의 여러 요청에서 재사용한 뒤 새 대화마다 새 값으로 바꿉니다. 두 값은 200자 이하의 문자열이어야 합니다. 이메일 대신 `home-server-user`처럼 비식별 ID를 권장합니다. 클라이언트가 이 metadata를 보내지 않으면 LiteLLM만으로는 대화별 세션을 추론할 수 없습니다.

## 관측과 백업

Grafana의 `LiteLLM gateway` 대시보드는 요청량, 토큰량, GenAI 호출 지연시간을 보여줍니다. 상세 프롬프트/응답 trace는 Langfuse에서 봅니다. 이번 구성에서는 Tempo와 Loki를 추가하지 않습니다.

Prometheus는 기존 30일 보관을 유지합니다. Langfuse OSS trace는 기본적으로 만료되지 않으므로 ClickHouse PVC 사용량을 정기적으로 확인합니다. PostgreSQL, ClickHouse/Keeper, SeaweedFS, Valkey PVC는 노드 local-path에 저장됩니다. PVC를 cold backup하려면 K3s를 멈추고 다른 디스크에 보관합니다.

```bash
sudo systemctl stop k3s
sudo bash scripts/backup-k3s-local-path.sh --destination /mnt/your-backup-drive/home-server-backups
sudo systemctl start k3s
```

이 스크립트는 local-path PVC 전체를 권한 0600의 압축 파일로 보관하고, K3s가 중지됐는지 확인합니다. Vault Raft snapshot도 따로 만들고 다른 디스크나 위치에 보관하세요. cold backup은 DB와 object store를 같은 시점에 복사하기 위해 클러스터 전체를 멈춥니다.

## 운영 확인

```bash
sudo k3s kubectl -n argocd get applications ai-credentials langfuse litellm ai-observability
sudo k3s kubectl -n langfuse get vaultstaticsecret
sudo k3s kubectl -n litellm get vaultstaticsecret
sudo k3s kubectl -n monitoring get vaultstaticsecret langfuse-exporter
sudo k3s kubectl -n langfuse get pods,pvc
sudo k3s kubectl -n litellm get pods,pvc,service
sudo k3s kubectl -n monitoring get prometheusrule langfuse-storage
```

Secret 값은 `kubectl get secret -o yaml`이나 Argo CD parameter 화면에 출력하지 마세요.

## Dualboot 프로필

Dualboot 프로필은 같은 차트와 공통 설정을 사용하지만, `clusters/dualboot/apps/catalog/litellm.yaml`의 초기 `model_list`는 비어 있습니다. Windows llama-swap을 위한 WSL 브리지는 dualboot에서 사용하지 않습니다. Linux inference endpoint가 준비되면 해당 프로필에 별도 연결을 추가합니다.

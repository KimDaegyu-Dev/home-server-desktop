# Home Server Desktop

WSL2 Ubuntu에서 시작해 나중에 Linux 듀얼부팅으로 옮길 수 있는 홈서버 구성을 Ansible과 GitOps로 재현합니다.

## 원칙

- Ansible은 Ubuntu 호스트 준비, K3s 설치, Argo CD 부트스트랩을 담당합니다.
- Argo CD는 Git의 Application of Applications 구성을 따라 플랫폼과 앱을 배포합니다.
- HashiCorp Vault는 Helm chart로 K3s 안에 설치하고, VSO가 Kubernetes Service로 연결합니다.
- Tailscale Operator Ingress로 선택한 서비스에 tailnet HTTPS 접근을 제공합니다.
- 비밀값, Vault 초기화 키, 토큰, kubeconfig, `.env` 파일은 Git에 커밋하지 않습니다.

## Ubuntu 준비

WSL2에서는 systemd를 활성화하고, Windows에서 `wsl --shutdown` 후 Ubuntu를 다시 실행합니다. Ubuntu에서 아래 명령으로 K3s와 Argo CD를 준비합니다.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git
git clone https://github.com/KimDaegyu-Dev/home-server-desktop.git ~/src/home-server-desktop
cd ~/src/home-server-desktop
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement ansible/requirements.txt
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/prepare-host.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/install-k3s.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/bootstrap-argocd.yml
```

Linux 듀얼부팅에서는 inventory를 `ansible/inventory/dualboot.yml`로 바꿉니다. 전체 절차는 [`docs/setup-wsl.md`](docs/setup-wsl.md), [`docs/setup-dualboot.md`](docs/setup-dualboot.md), [`docs/vault.md`](docs/vault.md)에 있습니다.

## 구성

| 영역 | 관리 방식 |
|---|---|
| Ubuntu 호스트, K3s | Ansible |
| Argo CD 및 플랫폼 앱 | GitOps / Application of Applications |
| 컨테이너 이미지 | 공식 LiteLLM 미러와 WSL Buildah 로컬 빌드 → K3s containerd |
| 비밀값 | K3s 내부 Vault와 VSO |
| 앱 데이터 | PVC와 별도 백업 절차 |

Vault의 저장소는 단일 노드 Raft와 `local-path` PVC입니다. Pod 교체에는 데이터를 유지하지만 노드·디스크 장애 대비는 별도 스냅샷 백업이 필요합니다. Docker Desktop 이미지 캐시와 named volume은 자동으로 K3s로 이동되지 않습니다. 이미지와 데이터는 각각 registry와 백업을 통해 이전합니다.

서비스 로그인과 Langfuse 키 발급, 기본 점검은 [`docs/service-checks.md`](docs/service-checks.md)를 참고하세요.

## 저장소 구조

```text
ansible/       Ubuntu 호스트 준비, K3s 설치, Argo CD 부트스트랩
clusters/      환경별 App of Applications 루트
platform/      Vault 연동, VSO, Tailscale Ingress 매니페스트
apps/          앱별 Kubernetes 매니페스트
docs/          재현, 네트워크, Vault, 데이터 이전 문서
versions.yml   도구 버전 고정
```

현재 설치 상태와 이전 기록은 [`docs/migration-status.md`](docs/migration-status.md)를 참고하세요. Docker Engine은 별도 복구 지시 전까지 시작하지 않습니다.

Kanzo · Hermes의 작업 디렉터리, 프로필 공유, 실행 격리와 성능 개선 계획은 [`docs/kanzo-hermes-roadmap.md`](docs/kanzo-hermes-roadmap.md)에 정리되어 있습니다.

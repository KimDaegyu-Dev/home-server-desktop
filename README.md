# Home Server Desktop

Ubuntu 24.04 LTS를 기준으로 WSL2에서 시작해 나중에 듀얼부팅 Ubuntu로 옮겨도 같은 절차로 재구축할 홈 서버 구성입니다.

## WSL VM lifetime

Copy `wsl/.wslconfig` to `%UserProfile%\.wslconfig`. It enables cgroup v2 for K3s and disables automatic shutdown of an idle WSL VM. After changing it, run `wsl --shutdown` once so WSL loads the settings. This stops workloads during the one-time restart.

## 원칙

- Ansible은 Linux 호스트 준비와 K3s 설치를 담당합니다.
- K3s 위의 Argo CD는 Git의 선언 상태를 적용합니다.
- 운영 컴포넌트와 컨테이너 이미지의 버전을 고정합니다.
- 시크릿 값은 Vault에 저장하며 Git에 저장하지 않습니다.
- Kubernetes 재생성과 앱 데이터 백업은 별도 절차입니다.
- WSL과 듀얼부팅 Linux는 공통 정의를 공유하고 프로필별 차이만 분리합니다.

## 빠른 시작

WSL2 Ubuntu 안에서 systemd가 켜져 있는지 확인합니다. `/etc/wsl.conf`의 기존 설정을 보존하면서 아래 설정을 추가한 뒤 PowerShell에서 `wsl --shutdown`을 실행합니다.

```ini
[boot]
systemd=true
```

저장소 checkout은 `/mnt/c` 대신 WSL의 Linux 파일시스템에 둡니다.

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
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/install-vault.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/bootstrap-argocd.yml
```

듀얼부팅 Ubuntu에서는 같은 명령에 `wsl.yml` 대신 `dualboot.yml`을 지정합니다. 단계별 내용은 [`docs/`](docs/)를 참고하세요.

## 역할 분리

| 계층 | 관리 방법 |
|---|---|
| Linux 호스트와 K3s | Ansible |
| Argo CD 이후의 클러스터 구성 | GitOps / Application of Applications |
| 사용자 앱 이미지 | GitHub Actions에서 빌드 후 GHCR 게시 |
| 시크릿 값 | 클러스터 외부 Vault |
| DB·파일 데이터 | 앱별 백업·복원 절차 |

## 저장소 구조

```text
ansible/       Linux 호스트 준비, K3s 설치, Argo CD 초기 설치
clusters/      환경별 App of Applications 루트
apps/          향후 앱별 Kubernetes 매니페스트
docs/          구축·복구·이전 절차
versions.yml   고정된 구성요소 버전
```

## 현재 범위와 주의점

초기 골격은 K3s, Argo CD, Vault Secrets Operator 부트스트랩까지 포함합니다. 앱별 Kubernetes 정의는 데이터 백업·복원 방법을 정한 후 서비스 단위로 추가합니다. 현재 Docker Desktop의 이미지 캐시와 named volume은 WSL Ubuntu의 K3s와 공유되지 않습니다. 이미지는 registry에서 가져오고, 데이터는 별도로 백업·복원해야 합니다.

VSO는 Vault 값을 Kubernetes Secret으로 동기화하므로 값은 클러스터 etcd에도 존재하게 됩니다. etcd 암호화, namespace 분리, 최소 권한 RBAC를 적용합니다.

이 저장소는 공개 상태입니다. 비밀번호, 토큰, kubeconfig, Vault 초기화 키, 개인 키, 실제 `.env` 파일을 커밋하지 마세요. 민감한 인프라 주소도 공개해도 되는지 검토한 뒤 기록합니다.

## Current setup status

See [docs/migration-status.md](docs/migration-status.md) for the installed cluster and service migration inventory.

The external HashiCorp Vault and Tailscale Ingress setup is described in [docs/vault.md](docs/vault.md) and [docs/networking-tailscale.md](docs/networking-tailscale.md).

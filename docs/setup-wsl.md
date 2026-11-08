# WSL2 Ubuntu 구축

## 사전 조건

- WSL2 Ubuntu 24.04 LTS와 최신 WSL kernel
- Python 3.12 (Ansible control node)
- Ubuntu 내부의 systemd 활성화
- 저장소와 K3s 데이터는 WSL Linux 파일시스템에 저장
- GPU가 필요한 경우 WSL 내에서 별도로 GPU 접근 확인

`/etc/wsl.conf`의 기존 설정을 보존하면서 아래 항목을 추가합니다.

```ini
[boot]
systemd=true
```

PowerShell에서 `wsl --shutdown` 후 Ubuntu를 다시 시작하고 `systemctl status`로 확인합니다.

## 설치

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

K3s는 WSL Ubuntu 내부의 containerd를 사용합니다. Docker Desktop 이미지 캐시와 named volume은 공유되지 않으므로 앱 이미지를 registry에서 가져오고 데이터는 별도로 복원합니다.

K3s systemd 서비스는 Ubuntu 배포판이 시작될 때 올라옵니다. Windows 부팅 시 WSL이 자동 시작되지 않으면 클러스터도 시작되지 않습니다. 자동 시작을 원하면 Windows 예약 작업을 별도로 구성하고 해당 절차도 이 저장소에서 관리합니다.

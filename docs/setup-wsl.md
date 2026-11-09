# WSL2 Ubuntu 재현 절차

## Windows 준비

1. WSL2에 Ubuntu 24.04 LTS를 설치합니다.
2. `%UserProfile%\.wslconfig`에 저장소의 [`wsl/.wslconfig`](../wsl/.wslconfig)를 복사합니다. K3s에 필요한 cgroup v2를 사용하고, idle 종료를 막습니다.
3. Ubuntu에서 `/etc/wsl.conf`에 다음을 추가한 뒤 PowerShell에서 `wsl --shutdown`하고 다시 엽니다.

   ```ini
   [boot]
   systemd=true
   ```

## Ubuntu 설치

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

Argo CD가 Git의 root Application을 읽은 뒤 Vault Helm chart, VSO, Tailscale Operator, Ingress를 배포합니다. 최초 Vault 설정은 [`vault.md`](vault.md)의 Fresh setup 절차를 따릅니다. Tailscale 정책의 tag ownership은 [`networking-tailscale.md`](networking-tailscale.md)에 있습니다.

K3s는 WSL Ubuntu 내부 containerd를 사용합니다. Docker Desktop 이미지 캐시와 named volume은 자동 이동되지 않습니다. Docker Desktop이 꺼져 있어도 K3s bootstrap은 진행할 수 있습니다.

WSL VM이 정지되면 K3s와 클러스터가 함께 정지합니다. Windows가 WSL을 시작하도록 설정하지 않았다면 Ubuntu에서 다시 시작해야 합니다.

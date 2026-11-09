# Linux dual-boot 재현 절차

듀얼부팅 Ubuntu에서도 WSL과 동일한 Ansible playbook과 Argo CD GitOps 구성을 사용합니다. 차이는 inventory가 정하는 데이터 경로와 노드 이름입니다.

## Linux 호스트 준비

Ubuntu 24.04 LTS 설치 후 systemd가 활성화되어 있는지 확인하고, 저장소를 가져옵니다.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git
git clone https://github.com/KimDaegyu-Dev/home-server-desktop.git ~/src/home-server-desktop
cd ~/src/home-server-desktop
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement ansible/requirements.txt
ansible-playbook -i ansible/inventory/dualboot.yml ansible/playbooks/prepare-host.yml
ansible-playbook -i ansible/inventory/dualboot.yml ansible/playbooks/install-k3s.yml
ansible-playbook -i ansible/inventory/dualboot.yml ansible/playbooks/bootstrap-argocd.yml
```

기본 K3s 로컬 볼륨 경로는 `/srv/home-server-desktop/k3s-local-path`입니다. 저장장치가 다르면 `ansible/inventory/dualboot.yml`의 `k3s_storage_path`를 수정해 사용합니다. 해당 경로와 Vault PVC는 Linux filesystem에서 백업해야 합니다.

Argo CD가 Git의 root Application을 읽으면 Vault chart, VSO, Tailscale Operator, Ingress를 설치합니다. 새 Vault는 [`vault.md`](vault.md)의 Fresh setup 절차로 초기화합니다. WSL 클러스터에서 Vault 데이터를 이전하는 경우에는 init을 반복하지 말고 snapshot migration 절차를 따릅니다.

## WSL에서 듀얼부팅으로 데이터 이전

Vault Raft snapshot, Vault init key shares, Vault root token, 애플리케이션 데이터는 Git에 저장하지 않습니다. 별도 암호화 백업으로 이동합니다. Vault snapshot을 restore한 뒤 원래 unseal shares로 unseal하고, VSO가 secret을 동기화하는지 확인합니다. Kubernetes PVC의 local-path 데이터는 노드 경로가 다르면 자동으로 따라오지 않으므로, 애플리케이션별 백업/복구 절차를 사용합니다.

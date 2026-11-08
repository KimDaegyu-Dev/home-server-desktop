# 듀얼부팅 Ubuntu 재구축

Ubuntu 24.04 LTS와 Python 3.12를 준비합니다. 같은 Git 저장소와 플레이북을 사용하고 환경 프로필만 바꿉니다.

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

초기 저장소 경로는 `/srv/home-server-desktop/k3s-local-path`입니다. 별도 디스크를 쓸 경우 파일시스템이 먼저 마운트되도록 구성하고 Ansible에서 마운트 확인을 추가합니다.

WSL의 local PV 디렉터리를 새 Linux에서 직접 사용하지 않습니다. 새 클러스터를 만들고 Argo CD로 앱을 배포한 다음 서비스별 백업을 복원합니다.

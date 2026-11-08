# 외부 Vault 연결

WSL에서는 Vault를 K3s 외부의 systemd 서비스로 실행합니다. Vault는 HashiCorp APT 저장소의 고정 버전과 단일 노드 Raft storage를 사용합니다. K3s CoreDNS는 `vault.home.arpa`를 현재 Linux 노드 주소로 해석하고, Vault API는 전용 TLS CA와 서버 인증서를 사용합니다. CA 개인 키는 `/etc/vault.d/tls/ca.key`에 root 전용 권한으로 두며 Git에 넣지 않습니다.

K3s를 설치한 뒤 Vault 호스트 서비스를 재현하려면 Ubuntu에서 실행합니다:

```bash
cd ~/src/home-server-desktop
sudo .venv/bin/ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/install-vault.yml
```

Ansible은 Vault 패키지·Raft 디렉터리·TLS·K3s DNS 별칭·VSO용 공개 CA Secret을 설정합니다. Vault는 의도적으로 초기화하지 않습니다. 최초 초기화 때 출력되는 unseal key share와 initial root token은 되돌릴 수 없으므로, 사용자가 직접 안전한 오프라인 위치에 보관합니다. 같은 노드 Raft storage는 단일 장애 지점이며, WSL/컴퓨터 재시작 뒤 Vault는 sealed 상태로 시작해 수동 unseal이 필요합니다.

초기화는 Vault 서비스를 처음 설치한 뒤 한 번만 실행합니다. 출력되는 키와 token은 터미널 로그, Git, 채팅에 복사하지 말고 안전한 별도 위치에 보관합니다.

```bash
sudo env VAULT_ADDR=https://vault.home.arpa:8200 \
  VAULT_CACERT=/etc/vault.d/tls/ca.crt \
  vault operator init -key-shares=5 -key-threshold=3
```

Vault를 열 때는 각각의 key share를 숨김 입력 프롬프트에 넣습니다.

```bash
sudo env VAULT_ADDR=https://vault.home.arpa:8200 \
  VAULT_CACERT=/etc/vault.d/tls/ca.crt vault operator unseal
```

`unseal`을 threshold에 도달할 때까지 반복합니다. 출력된 root token을 보관된 위치에서 입력해 관리자 CLI를 사용할 수 있습니다. 루트 token을 Git이나 셸 기록에 저장하지 않습니다.

1. Vault 주소와 신뢰할 CA 인증서를 준비합니다.
2. Vault에서 Kubernetes auth method를 설정합니다.
3. 앱 namespace와 ServiceAccount마다 별도 Vault role과 최소 권한 정책을 만듭니다.
4. `VaultConnection`, `VaultAuth`, `VaultStaticSecret` 리소스를 앱별로 추가합니다.
5. 앱 Deployment가 VSO가 만든 Secret을 참조하도록 합니다.

Tailscale Operator용 Kubernetes auth, KV v2 mount, 최소 권한 policy와 role, OAuth 값 입력은 `bash scripts/bootstrap-vault-tailscale.sh`가 설정합니다. 이 스크립트는 Vault 초기화/unseal, Argo CD가 Tailscale auth ServiceAccount를 생성한 뒤 실행합니다. 관리 token과 OAuth client secret은 숨김 입력으로 받고, OAuth JSON은 짧은 수명의 `/dev/shm` 파일에만 기록한 뒤 삭제합니다.

시크릿 값 대신 Vault 경로와 최소 권한만 Git에 기록합니다. VSO는 Vault 값을 Kubernetes Secret으로 동기화하므로 해당 값이 etcd에도 저장됩니다. etcd 암호화, namespace 격리, 제한된 RBAC를 적용합니다. Vault 준비 전에는 앱 시크릿 CR을 추가하지 않습니다.

VSO는 Argo CD에서 고정한 Helm chart 버전으로 설치됩니다. 버전 변경 시 chart 호환성과 CRD 변경 사항을 확인합니다.

# 버전 고정과 업데이트

K3s와 Argo CD는 [`versions.yml`](../versions.yml)에 고정합니다. K3s 설치 스크립트 URL도 같은 K3s release tag를 사용하므로 최신 스크립트가 바뀌어도 기존 설치 절차가 바뀌지 않습니다.

Ansible control node는 [`ansible/requirements.txt`](../ansible/requirements.txt)에 고정합니다. Python 가상환경에서 설치합니다.

Vault Secrets Operator chart는 환경별 Argo CD Application의 `targetRevision`에 고정되어 있습니다. 두 환경의 버전 변경은 함께 적용합니다.

버전을 바꿀 때는 다음 순서로 진행합니다.

1. 공식 릴리스와 지원 조건을 확인합니다.
2. 고정 버전 값을 수정하고 변경 이유를 기록합니다.
3. WSL 클러스터에서 업그레이드를 먼저 진행합니다.
4. 복구 절차를 확인한 다음 듀얼부팅 프로필에 반영합니다.

공식 참조:

- [K3s releases](https://github.com/k3s-io/k3s/releases)
- [Argo CD releases](https://github.com/argoproj/argo-cd/releases)
- [Vault Secrets Operator releases](https://github.com/hashicorp/vault-secrets-operator/releases)
- [Ansible Core releases](https://docs.ansible.com/projects/ansible/latest/reference_appendices/release_and_maintenance.html)

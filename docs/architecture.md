# 아키텍처 메모

```text
GitHub home-server-desktop
  ├─ Ansible: Linux 준비 및 K3s 설치
  └─ Argo CD root Application
       ├─ 플랫폼 컴포넌트
       └─ 앱별 Application

외부 Vault ── VSO ── 앱별 Kubernetes Secret
GitHub Actions ── GHCR ── K3s workloads
```

앱 배포 기준은 Git입니다. 이미지는 Docker Desktop 로컬 캐시가 아니라 registry에서 가져옵니다. local-path PVC는 특정 노드 디스크에 묶이므로 복구는 별도 백업으로 처리합니다.

현재 부트스트랩은 K3s, Argo CD, Vault Secrets Operator를 설치합니다. Vault 주소·CA, 네트워크/Ingress, 별도 영속 디스크, 앱별 매니페스트와 백업 명령은 후속 설정입니다.

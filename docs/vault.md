# 외부 Vault 연결

Vault는 Kubernetes 외부에서 TLS로 접근 가능하게 준비합니다. Vault 초기화 키와 root token은 안전한 오프라인 위치에 보관하고 Git에는 넣지 않습니다.

1. Vault 주소와 신뢰할 CA 인증서를 준비합니다.
2. Vault에서 Kubernetes auth method를 설정합니다.
3. 앱 namespace와 ServiceAccount마다 별도 Vault role과 최소 권한 정책을 만듭니다.
4. `VaultConnection`, `VaultAuth`, `VaultStaticSecret` 리소스를 앱별로 추가합니다.
5. 앱 Deployment가 VSO가 만든 Secret을 참조하도록 합니다.

시크릿 값 대신 Vault 경로와 최소 권한만 Git에 기록합니다. VSO는 Vault 값을 Kubernetes Secret으로 동기화하므로 해당 값이 etcd에도 저장됩니다. etcd 암호화, namespace 격리, 제한된 RBAC를 적용합니다. Vault 준비 전에는 앱 시크릿 CR을 추가하지 않습니다.

VSO는 Argo CD에서 고정한 Helm chart 버전으로 설치됩니다. 버전 변경 시 chart 호환성과 CRD 변경 사항을 확인합니다.

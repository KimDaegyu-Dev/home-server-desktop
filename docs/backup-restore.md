# 백업과 복구

GitOps 매니페스트는 클러스터 설정을 재생성하지만 데이터 백업은 아닙니다.

## 백업 범위

- Vault: 서버 스토리지 백업, 복구 절차, 초기화·unseal 자료를 별도 보관
- K3s: 단일 서버 SQLite datastore와 K3s server token을 함께 백업
- 앱 데이터: DB별 네이티브 dump와 파일 데이터
- 구성: Git 커밋과 원격 저장소
- 이미지: GHCR digest와 빌드 커밋

K3s server token은 datastore의 민감 데이터를 암호화하는 데 사용되므로 K3s 상태 복구 시 snapshot과 함께 보관해야 합니다. 백업 파일은 K3s 데이터 경로 밖의 별도 디스크나 NAS로 보냅니다.

## WSL에서 듀얼부팅 Linux로 이동

1. 앱 쓰기를 멈추고 DB 네이티브 백업을 만듭니다.
2. 백업을 별도 위치에 복사하고 무결성을 확인합니다.
3. 새 Linux에서 Ansible과 Argo CD 부트스트랩을 실행합니다.
4. GitOps가 PVC와 앱을 만든 뒤 데이터를 복원합니다.
5. 헬스체크와 데이터 확인 후 접속 경로를 전환합니다.
6. 새 환경의 복구를 확인할 때까지 이전 데이터를 보존합니다.

K3s datastore 백업은 앱 PV 내용을 대신하지 않습니다. WSL 배포판 export도 Docker Desktop named volume이나 bare-metal Linux용 앱 백업을 대신하지 않습니다.

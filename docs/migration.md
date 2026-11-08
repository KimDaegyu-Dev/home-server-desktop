# Compose에서 K3s로 앱 이전

각 앱은 데이터 백업·복원을 확인한 뒤 하나씩 옮깁니다.

1. Compose의 포트, 환경변수 이름, bind mount, named volume, 헬스체크, GPU 요구를 기록합니다.
2. 이미지를 GHCR에 게시하고 tag 또는 digest를 고정합니다.
3. Deployment/StatefulSet, Service, PVC, ConfigMap, VSO secret reference를 작성합니다.
4. DB dump와 파일 데이터를 새 PVC로 복원합니다.
5. 내부 연결과 헬스체크를 확인합니다.
6. 쓰기를 전환하고 이전 Compose 데이터는 보존합니다.

Snowball의 Neo4j, Qdrant, Fuseki와 Hindsight PostgreSQL은 영속 데이터를 별도로 이전해야 합니다. ai-learning-companion의 Docker socket 마운트는 Kubernetes 노드의 Docker API와 같다고 가정할 수 없으므로 해당 기능을 재설계하기 전에는 배포하지 않습니다. GPU 워크로드는 WSL과 bare-metal Linux 양쪽에서 별도 검증합니다.

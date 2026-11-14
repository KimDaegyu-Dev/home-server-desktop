# Kanzo · Hermes 진행 상태

이 문서는 구현 진행률, 코드/배포 revision, 검증 결과, 롤백 이력을 관리한다. 아키텍처 원칙과 목표 구조의 SSOT는 [Kanzo · Hermes 실행 구조 개선 로드맵](kanzo-hermes-roadmap.md)이다. 설계 변경은 로드맵에 반영하고, 여기에는 진행 상태와 근거만 기록한다.

## 현재 상태

- 현재 기준: 2026-11-14, 로드맵 Phase 1 진행 중, Phase 2 조사 완료, Phase 3 일부 구현.
- 기준 스냅샷 `v1`: 커밋 `f11abf6` (`origin`에 push 완료). 런타임 변경 전 worker 코드와 설계 로드맵이 포함되어 있다.
- 워커 런타임 소스: `e59a758` (`feat(kanzo): persist and recover run artifacts`).
- 마지막 워커 롤아웃 확인: Argo CD `kanzo-agent-worker`가 revision `e59a7586ec2ed77c50ffa0af1fffa7866f6ca822`에서 Synced/Healthy였고 Pod `kanzo-agent-worker-f965d8879-ttf9m`는 5/5 Ready, restart 0, 태스크 active gauge 0이었다. 배포된 `worker.py`, `run_ledger.py`, `artifact_store.py` SHA-256은 저장소 파일과 각각 일치했다.
- 실제 Kanzo 태스크로는 검증하지 않았다. Python 문법 검사, Kustomize 렌더링, 배포 상태·코드 해시·유휴 active gauge만 확인했다.

## 단계별 진행

| 로드맵 단계 | 상태 | 진행 및 남은 작업 |
|---|---|---|
| 0. 기준선과 지원 경로 확인 | 일부 완료 | 클러스터 Hermes 0.23.1에서 Gateway 세션 생성·제출·중단 API와 pluggable terminal backend를 직접 확인했다. backend 종료 정리 키 불일치와 provider 원격 명령 취소 책임도 확인했다. 처리 시간 분해, 활성 부하 peak, 학습·회고·기억 경로 전체 검증은 남아 있다. |
| 1. 소스 준비·입력·산출물 | 진행 중 | repo가 있는 카탈로그 작업은 작업 유형과 무관하게 checkout을 준비하고 Git HEAD SHA를 프롬프트에 명시한다. checkout 디렉터리와 writable branch에 run ID를 붙여 재실행 간 충돌을 막았다. 긴 결과는 최대 4,200자 Markdown 댓글로 분할하고 코드 fence를 유지한다. task prompt에는 원 설명과 최근 비워커 댓글만 포함하고 이전 Hermes 워커 출력은 제외한다. 전체 Hermes 응답은 PVC의 `kanzo-worker/artifacts/<task_id>/<run_id>/result.md`와 SHA-256 manifest에 원자적으로 저장하고, 재시작 후 결과 댓글 게시를 이어간다. Graphify revision 확인과 Git object cache는 남아 있다. checkout은 run 종료 시 삭제된다. |
| 2. 프로필·상태·공유 데이터 경계 | 조사 완료, 구현 전 | ReadOnly executor는 별도 HOME과 `state.db`를 써 일반 Gateway/Desktop의 세션 검색·재개와 분리된다. 프로필 로컬 skills/plugins/scripts/`.omh`를 매 run 복사하며 대상 디렉터리를 지워 학습 변경이 사라질 수 있다. OMH 메모리 절대 경로는 공유되지만 반성 hook의 실행·다음 세션 검색은 검증되지 않았다. 지속 Gateway 세션과 run별 도구 backend로 전환해야 한다. |
| 3. durable run ledger와 이벤트 | 일부 구현 | Hermes HOME PVC의 SQLite 장부에 run 상태·설정·소스 SHA·결과와 게시 체크포인트를 기록한다. 재시작 시 미종료 run을 자동 실행하지 않고, 저장 응답과 댓글의 run별 marker를 이용해 누락된 결과 댓글만 복구한 뒤 사람 검토로 보낸다. 이벤트 중복·재연결 구간, 다중 dispatcher lease/CAS, PR 및 다른 외부 게시의 멱등 재개, 실태스크 검증은 남아 있다. 자동 재시도는 추가하지 않는다. |
| 4. 상주 executor와 작업 도구 격리 | 조사 일부 완료 | 클러스터 버전은 session API와 terminal provider 확장점을 제공한다. 작업별 도구 backend는 별도 HOME 복제 없이 붙일 수 있지만, provider가 원격 취소를 구현해야 하고 세션 종료 시 환경 정리 키도 맞춰야 한다. 상주 Gateway에 run별 backend를 연결하는 최소 검증과 분리 배포는 남아 있다. |
| 5. 결과·추적·위임 | 시작 전 | 구조화된 완료 근거, run/session tracing, 자식 작업 예산과 결과 게시의 일관성을 구현·확인해야 한다. |
| 6. 성능·학습 보존 검증 | 시작 전 | 동등 작업 비교와 학습 연속성·취소·격리 시나리오를 실행해야 한다. |

## 변경 이력

### 2026-11-14 — 장기 산출물과 결과 댓글 복구

- 커밋 `e59a758`를 GitOps `main`에 push하고 배포했다. Argo CD는 Synced/Healthy, worker Pod는 5/5 Ready, restart 0, active gauge 0이었다. `worker.py`, `run_ledger.py`, `artifact_store.py`의 배포 SHA-256이 로컬 파일과 일치했다.
- 전체 Hermes 응답과 메타데이터를 Hermes HOME PVC의 task/run 디렉터리에 원자적으로 기록한다. manifest는 source SHA와 결과 해시를 포함하며, 파일 권한은 사용자 전용으로 제한한다. 기존 artifact는 자동 삭제하지 않는다.
- 결과 댓글에 숨김 run/chunk marker와 정확한 게시 본문을 저장한다. 재시작 때 이미 올라간 조각은 건너뛰고 빠진 결과만 게시한다. 외부 작업을 자동 재실행하지 않는다. 출력의 알려진 토큰 패턴과 주입된 비밀값은 artifact/댓글 저장 전에 가린다.
- ReadOnly 데이터 경계를 조사했다. 별도 HOME 때문에 Hermes 세션 DB가 일반 프로필과 분리되고, 프로필 로컬 skills/plugins/scripts/`.omh`는 매 run 삭제 후 원본에서 다시 복사되어 학습 변경이 덮일 수 있다. OMH 절대 경로 일부는 공유되지만 회고와 학습이 다음 세션에서 검색되는지는 미확인이다. 별도 Gateway HOME 복사 방식을 상주 Gateway + run별 도구 backend로 대체하는 Phase 2/4 작업이 남아 있다.
- 이 배포는 실제 Kanzo 태스크로 검증하지 않았다. 게시 재생성·부분 댓글 복구 동작도 태스크 기반으로 아직 검증되지 않았다.

### 2026-11-14 — SQLite 실행 장부 1차 적용

- 커밋 `8ec721a`를 GitOps `main`에 push하고 Argo CD 반영을 확인했다. Argo CD는 Synced/Healthy, 새 worker Pod는 5/5 Ready, restart 0이었다.
- 실행 상태와 모델 원문 결과를 `kanzo-agent-hermes-home` PVC의 `kanzo-worker/run-ledger.sqlite3`에 기록한다. SQLite WAL/FULL 동기화와 트랜잭션을 쓰고, 저장 디렉터리·DB 파일 권한을 제한한다. 모델·provider, 태스크, source SHA, 결과 게시 단계도 기록한다.
- 재시작 뒤 종료 상태가 없는 run은 자동 재실행하지 않고, 원 Kanzo 카드에 재시작 중단을 기록해 `review + human=true`로 라우팅한다. 이미 `done` 또는 `review`인 카드는 상태를 덮어쓰지 않는다.
- 확인: Python 문법 검사, Kustomize 렌더링, ConfigMap 반영, 배포된 두 Python 파일의 SHA-256 일치, Argo Synced/Healthy, Pod 5/5 Ready와 active gauge 0.
- 실제 Kanzo 태스크 실행은 하지 않았다. 게시 체크포인트는 기록되지만 현재 외부 댓글·PR 게시를 멱등하게 재개하지 않으며, 이벤트 재연결과 lease/CAS 보장도 미구현이다.

### 2026-11-14 — 기준선 `v1`

- Git tag `v1` → `f11abf6`; 원격 `origin/v1`에 게시.
- Argo CD는 `b3a553e`에서 Synced/Healthy였고 워커 태스크는 유휴 상태였다.
- 한 번 측정한 유휴 Pod 사용량은 약 1,006 MiB와 250 millicores였다: dashboard 322 MiB/8m, Gateway 592 MiB/239m, event adapter 13 MiB/1m, ReadOnly executor 59 MiB/1m, worker 20 MiB/1m. peak 또는 sizing 기준으로 사용하지 않는다.
- Hermes `0.23.1`의 `chat --oneshot`은 canonical Gateway session 흐름으로 연결된다. 별도 HOME은 별도 Gateway/session 상태 경계가 된다.

### 2026-11-14 — ReadOnly 저장소 준비

- 커밋 `1bc102d`를 GitOps `main`에 push하고 Argo CD 자동 반영을 확인했다.
- 저장소가 연결된 ReadOnly 카탈로그 작업은 worker가 checkout한 소스를 읽는다. 실행 프롬프트에 checkout의 전체 Git SHA를 전달하고, Graphify 인덱스 revision이 동일하다고 단정하지 않도록 했다.
- 배포 검증: Argo Synced/Healthy, Pod 5/5 Ready, restart 0, 배포된 `worker.py` 해시가 저장소 파일과 일치, active gauge 0.
- 실제 Kanzo 태스크를 실행하지 않아 기능 동작은 아직 태스크 단위로 검증되지 않았다.

### 2026-11-14 — run별 checkout 및 Hermes API 확인

- 커밋 `80e121b`를 GitOps `main`에 push했다. Argo CD가 해당 전체 SHA에서 Synced/Healthy이고 worker Pod 5/5 Ready, restart 0임을 확인했다. 배포된 worker 코드 SHA-256이 저장소 파일과 일치하고 active gauge는 0이었다.
- 저장소가 연결된 카탈로그 작업은 읽기·수정 분류와 독립적으로 checkout한다. checkout 디렉터리와 쓰기 가능한 branch 이름에 run ID를 포함해 같은 Kanzo 태스크의 후속 실행이 기존 run 경로·branch와 충돌하지 않게 했다. run 종료 시 checkout 삭제 정책은 유지된다.
- 실제 클러스터 이미지 `Hermes Agent v0.23.1 (upstream 3b421578)`에서 `session.create`, `prompt.submit`, `session.interrupt` 및 `TerminalEnvironmentProvider` 등록/사용 경로를 확인했다. `terminal`, remote `file`, `execute_code`, delegate가 provider 환경을 공유할 수 있다.
- provider에는 독립적인 취소 API가 없고 원격 실행 취소를 backend가 직접 처리해야 한다. 세션 종료 시 cleanup 키와 resolved environment key가 일치하지 않을 수 있어, persistent sandbox에는 명시적인 run/session mapping과 회수 경로가 필요하다. 현재까지 Kanzo task로 새 실행 검증은 하지 않았다.

### 2026-11-14 — 긴 결과 댓글 보존

- 커밋 `314f758`를 GitOps `main`에 push했다. Argo CD가 해당 revision에서 Synced/Healthy이고 worker Pod 5/5 Ready, restart 0임을 확인했다. worker 코드 SHA-256 일치와 active gauge 0도 확인했다.
- Hermes 결과의 고정 5,000자 절단을 제거하고 긴 Markdown을 최대 4,200자 본문 단위의 번호가 있는 Kanzo 댓글로 분할한다. 분할 경계에서 열린 fenced code block은 닫고 다음 댓글에서 다시 연다.
- 실제 긴 결과를 Kanzo에 게시하는 태스크 검증은 아직 하지 않았다.

### 2026-11-14 — Kanzo 입력 댓글 범위

- 커밋 `e65308d`에서 task prompt의 기존 논의를 제한했다. `Hermes Kanzo Worker`가 작성한 시작·진행·실패·결과 댓글은 제외하고, 다른 작성자의 최근 최대 8개 댓글을 총 10,000자 이내로 포함한다. Kanzo 상태 판정은 필터 전 원문을 계속 사용한다.
- 코드 검토에서 지적된 fenced block 처리와 댓글 본문 안의 작성자/날짜 문자열 경계 문제를 `292ac46`에서 보완했다. serialized `Comments (N)`와 파싱한 댓글 개수가 다르면 원문을 보존해 잘못된 제거를 피한다. backtick/tilde fence를 같은 delimiter 길이로 닫고 다음 댓글에서 연다.
- 배포 확인: Argo Synced/Healthy, worker Pod 5/5 Ready, restart 0, 코드 SHA-256 일치, active gauge 0. Python 문법 검사와 Kustomize 렌더링은 통과했다. 실제 Kanzo task로 입력 필터나 긴 댓글 게시를 실행 검증하지는 않았다.

## 롤백

`v1`은 Git 기준선이다. `292ac46` 변경만 되돌릴 때는 `git revert 292ac46`를 만들고 push한다. 모든 런타임 변경을 기준선으로 되돌릴 때는 v1 이후 런타임 커밋을 역순으로 revert한다. `v1` tag 자체로 Argo CD의 추적 브랜치를 바꾸지는 않는다.

## 이전 런타임 변경 및 조사 이력

- `3b14149`: Kanzo 태스크 상태 문자열의 쉼표를 포함해 review + human=true 취소 신호를 정확히 읽도록 수정했다.
- `b3a553e`: ReadOnly profile의 Langfuse secret scope에 VSO 주입 키와 URL을 연결하고 dotenv 권한을 제한했다.
- `#18` 실행 `36f71317683e`는 45분 제한에 도달해 exit code 124로 끝났다. Langfuse trace `ba764768cca8a5f9ba39c56025016da6`에서 generation 36건과 tool observation 71건을 확인했지만 Kanzo task/run root metadata 연결은 확인하지 못했다. session ID는 있었고 `uid:10000`은 컨테이너 로컬 계정이므로 실제 사용자 식별로 간주하지 않았다. 이 실행을 성공으로 판정하지 않았다.
- 같은 조사에서 unattended heredoc 승인이 막힌 정황이 확인됐다. 입력/승인 실패 빈도와 실행 단계별 지연은 추가 측정 대상이다.


## 조사 기준선: 실제 설치 상태

아래는 설계 전 확인한 당시 동작과 데이터 경계다. 이는 현재 진행률이 아니라 조사 근거이며, 이후 runtime 변경과 어긋나면 진행 이력과 함께 갱신한다.

## 현재 확인한 동작

기준 코드는 `b3a553e`와 그 선행 커밋이며, 아래 내용은 해당 코드와 실행 환경을 조사한 결과다. 리소스 수치는 측정 시점의 스냅샷이며 용량 산정 결과가 아니다.

| 영역 | 현재 동작 | 개선 이유 |
|---|---|---|
| 배포 단위 | worker·이벤트 어댑터·Gateway·Desktop serve·ReadOnly executor가 한 Pod에 있음 | 작은 코드 변경도 대화용 서비스와 실행 환경 전체를 교체함 |
| 리소스 | idle 사용량 약 795Mi, 컨테이너 memory requests 합계 약 3.53Gi | idle 사용량만으로 requests를 줄일 수 없음. 실행 중 peak 측정 필요 |
| 작업 디렉터리 | `/workspace`는 4Gi 제한의 Pod `emptyDir` | Pod 교체 시 사라지며 로컬 Windows 저장소와 연결되지 않음 |
| 저장소 준비 | `is_code`로 분류된 태스크만 GitHub에서 새 clone | repo가 연결된 분석·리뷰도 소스가 준비되지 않을 수 있음 |
| checkout 수명 | `/workspace/<project>-<task-number>` 사용 후 성공·실패 모두 삭제 | 미보존 결과와 변경 사항이 사라짐. 실행별 경로도 필요 |
| ReadOnly | 별도 executor가 `/workspace`를 읽기 전용으로 마운트 | 소스 쓰기 제약은 있으나 `/opt/data`는 쓰기 가능하므로 전체 환경이 ReadOnly인 것은 아님 |
| 프로필 동기화 | 원본 역할 프로필을 별도 HOME으로 선택적으로 복사 | 설정·의존성·인증 전달이 갈라지고 복사 비용 및 상태 불일치가 발생함 |
| 프롬프트 | 태스크 상세와 기존 댓글 전체를 입력에 포함 | 실패 이력과 이전 결과가 쌓일수록 입력이 커짐 |
| 결과 게시 | 최종 댓글 본문을 5000자로 절단 | 긴 결과가 중간에서 잘릴 수 있음 |
| 초기화 | Pod 시작마다 프로필별 CLI 설정, OMH setup·doctor 등을 실행 | 상주 executor의 재시작·배포 시간도 늘어나므로 런타임 준비와 실행을 분리해야 함 |
| 실행 제어 | 프로세스와 Gateway의 수명이 분리될 수 있음 | 타임아웃 후 고아 Gateway와 회수되지 않은 자식 프로세스가 관측됨 |

### 실제 공유 범위

| 데이터 | 일반 프로필과 ReadOnly 실행 사이의 관계 |
|---|---|
| `state.db`, 대화·세션 | 서로 다른 HOME의 별도 상태 |
| Hermes Kanban | 일반 `/opt/data/home/kanban.db`, ReadOnly `/opt/data/readonly-hermes/kanban.db`로 기본 경로가 다름 |
| 프로필 로컬 스킬·플러그인·스크립트 | 원본에서 ReadOnly HOME으로 단방향 복사. 양방향 동기화 아님 |
| 외부 OMH 스킬 | 양쪽 모두 `/opt/data/home/.omh/skills` 참조 |
| OMH 메모리 | 양쪽 모두 `plugins.entries.omh.settings.omh_home: /opt/data/home/.omh` 설정 |
| ai-memory·Graphify 등 MCP | 같은 외부 서비스에 연결. 허용 범위와 데이터 scope는 별도 확인 필요 |

같은 메모리 저장소를 사용한다는 사실만으로 프로젝트·사용자별 검색 범위가 올바르다고 판단하지 않는다. 또한 별도 HOME이 있어도 원본 PVC 전체를 쓰기 가능하게 마운트한 상태에서는 강한 파일 접근 격리로 간주하지 않는다.

# Kanzo · Hermes 진행 상태

이 문서는 구현 진행률, 코드/배포 revision, 검증 결과, 롤백 이력을 관리한다. 아키텍처 원칙과 목표 구조의 SSOT는 [Kanzo · Hermes 실행 구조 개선 로드맵](kanzo-hermes-roadmap.md)이다. 설계 변경은 로드맵에 반영하고, 여기에는 진행 상태와 근거만 기록한다.

## 현재 상태

- 현재 기준: 2026-11-14, 로드맵의 Phase 1 진행 중.
- 기준 스냅샷 `v1`: 커밋 `f11abf6` (`origin`에 push 완료). 런타임 변경 전 worker 코드와 설계 로드맵이 포함되어 있다.
- 워커 런타임 소스: `1bc102d` (`fix(kanzo): prepare source for readonly repository tasks`). 이후 문서 전용 커밋은 워커 코드를 변경하지 않는다.
- 마지막 워커 롤아웃 확인: Argo CD `kanzo-agent-worker`가 revision `1bc102d`에서 Synced/Healthy였고 Pod `kanzo-agent-worker-5d5fd46c57-r42ms`는 5/5 Ready, restart 0, 태스크 active gauge 0이었다.
- 이 변경에서는 Hermes 실제 태스크를 실행하지 않았다. 문법 검사, Kustomize 렌더링, 배포된 ConfigMap 코드 SHA-256 일치만 확인했다.

## 단계별 진행

| 로드맵 단계 | 상태 | 진행 및 남은 작업 |
|---|---|---|
| 0. 기준선과 지원 경로 확인 | 일부 완료 | 설치된 Hermes 0.23.1 CLI/Gateway 흐름, Argo revision, idle 자원 샘플, active gauge를 기록했다. 처리 시간 분해, 활성 부하 peak, 학습·회고·기억 경로 전체 검증은 남아 있다. |
| 1. 소스 준비·입력·산출물 | 진행 중 | 카탈로그에 repo가 있는 ReadOnly 작업이 checkout을 준비하고 Git HEAD SHA를 프롬프트에 명시하도록 반영했다. Graphify 인덱스와의 revision 일치 검증, writable 작업의 공통 소스 준비, durable artifact, Git object cache는 남아 있다. checkout은 현재 run 종료 시 삭제된다. |
| 2. 프로필·상태·공유 데이터 경계 | 시작 전 | Hermes/OMH의 durable memory, reflection, learned skills, session history 보존과 ReadOnly 학습 쓰기 경계를 검증·구현해야 한다. |
| 3. durable run ledger와 이벤트 | 시작 전 | lease/run ID, dispatcher 재시작 조정, idempotent publication과 stale 결과 차단을 구현해야 한다. 자동 재시도는 추가하지 않는다. |
| 4. 상주 executor와 작업 도구 격리 | 시작 전 | 상주 Hermes executor, 작업별 도구 경계, 정확한 run 취소와 자식 프로세스 정리를 구현해야 한다. 현재 ReadOnly executor는 별도 HOME을 사용하고, Gateway session interrupt 경계가 아직 통합되지 않았다. |
| 5. 결과·추적·위임 | 시작 전 | 구조화된 완료 근거, run/session tracing, 자식 작업 예산과 결과 게시의 일관성을 구현·확인해야 한다. |
| 6. 성능·학습 보존 검증 | 시작 전 | 동등 작업 비교와 학습 연속성·취소·격리 시나리오를 실행해야 한다. |

## 변경 이력

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

## 롤백

`v1`은 Git 기준선이다. 현재 `main`에서 이 첫 런타임 변경만 되돌릴 때는 `git revert 1bc102d`를 만들고 push하면 Argo CD가 해당 코드를 되돌린다. 이후 변경도 되돌릴 때는 v1 이후 런타임 커밋을 역순으로 revert한다. `v1` tag 자체로 Argo CD의 추적 브랜치를 바꾸지는 않는다.

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

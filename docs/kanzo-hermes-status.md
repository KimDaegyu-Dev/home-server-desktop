# Kanzo · Hermes 진행 상태

이 문서는 구현 진행률, 코드/배포 revision, 검증 결과, 롤백 이력을 관리한다. 아키텍처 원칙과 목표 구조의 SSOT는 [Kanzo · Hermes 실행 구조 개선 로드맵](kanzo-hermes-roadmap.md)이다. 설계 변경은 로드맵에 반영하고, 여기에는 진행 상태와 근거만 기록한다.

## 현재 상태

- 확인 기준: 2026-11-15. Argo CD kanzo-agent-worker는 revision 977484467bd8710c558bc330bf582419d280bb57에서 Synced/Healthy다. Executor는 3/3 Ready이며 현재 sandbox run은 없다. #70과 #72 파일 도구 canary는 검증 후 Done 처리했다.
- #68은 최초 파일 도구 경로 수정의 실패 재현으로 Review에 남겼다. #70은 /workspace EROFS, /scratch write/read/cleanup 성공을 확인했고, #72는 symlink 경계 거부 로그를 확인해 둘 다 Done이다. #69/#71 제안은 사용자가 직접 Todo로 옮기기 전까지 Backlog에 둔다.
- tool-free 정책은 Hermes에 `no_mcp` toolset sentinel을 전달해 프로필 MCP까지 포함한 도구 호출을 차단한다. #39 run `ac261e85586d`는 기대한 응답을 반환했고, researcher session DB의 `tool_call_count=0`, executor ledger의 completed 상태 및 Langfuse의 도구 없는 trace를 확인했다.
- Langfuse task/run 귀속이 live canary로 확인됐다. #39 executor ledger task/run/session 매핑은 trace `975c391d227442973fa4338d29136e94`의 session ID와 일치한다. Observations API v2를 `fields=core,basic,metadata`로 조회했을 때 root `CHAIN`과 `GENERATION` 모두 `kanban_task_id=hermes-harness-lab#39`, `kanban_run_id=ac261e85586d`를 가진다. metadata가 `null`로 보였던 이전 조회는 기본 field set에서 metadata group을 요청하지 않아 생긴 관측 오류였다.
- 기존 #37 trace `c68e83bdc95fa51675b53ffd53201e34`도 metadata field group을 지정해 재조회했다. 27개 observation에 task/run ID가 모두 있으며, 이전 Phase 6 표본 설명은 유지한다. #37의 상충 지시는 정상 one-turn latency baseline으로 쓰지 않는다.
- Phase 6 worker duration histogram과 Prometheus scrape가 배포·확인됐다. 범위가 맞는 작업의 비교 표본, per-run 자원 peak, 학습 연속성 및 격리/cancel 시나리오 측정은 남아 있다.
- 다음 구현은 Phase 5의 Langfuse 종료 상태 대조와 generation/delegate 중복·부모 자식 추적이며, 이후 Phase 6의 비교 가능한 성능 표본과 Hermes 학습 연속성을 검증한다. ReadOnly 프로필의 스킬 동기화는 원본을 덮을 수 있어 별도 수정이 필요하다.

## 단계별 진행

| 로드맵 단계 | 상태 | 진행 및 남은 작업 |
|---|---|---|
| 0. 기준선과 지원 경로 확인 | 일부 완료 | 클러스터 Hermes 0.23.1에서 Gateway 세션 생성·제출·중단 API와 pluggable terminal backend를 직접 확인했다. backend 종료 정리 키 불일치와 provider 원격 명령 취소 책임도 확인했다. 처리 시간 분해, 활성 부하 peak, 학습·회고·기억 경로 전체 검증은 남아 있다. |
| 1. 소스 준비·입력·산출물 | 진행 중 | repo가 있는 카탈로그 작업은 작업 유형과 무관하게 checkout을 준비하고 Git HEAD SHA를 프롬프트에 명시한다. checkout 디렉터리와 writable branch에 run ID를 붙여 재실행 간 충돌을 막았다. 긴 결과는 최대 4,200자 Markdown 댓글로 분할하고 코드 fence를 유지한다. task prompt에는 원 설명과 최근 비워커 댓글만 포함하고 이전 Hermes 워커 출력은 제외한다. 전체 Hermes 응답은 PVC의 `kanzo-worker/artifacts/<task_id>/<run_id>/result.md`와 SHA-256 manifest에 원자적으로 저장하고, 재시작 후 결과 댓글 게시를 이어간다. Graphify revision 확인과 Git object cache는 남아 있다. checkout은 run 종료 시 삭제된다. |
| 2. 프로필·상태·공유 데이터 경계 | 코드 전환 초안, 실행 검증 전 | repo가 지정된 read-only run은 ReadOnly 전용 Gateway 복사본을 우회하고 주 Gateway에서 사전 생성한 세션을 사용하도록 worker 경로를 바꿨다. 프로필 DB와 memory/consolidation 데이터가 PVC에 존재하며 같은 프로필 경로를 가리키는 것도 확인됐다. 브로커 명세 테스트 2개와 live sandbox Pod 설정에서 runner `/workspace` read-only, root filesystem read-only, service-account token 자동 마운트 비활성화를 확인했다. 이는 설정 증거이며 실제 파일 쓰기 거부 동작은 아직 확인하지 않았다(#50 Todo 대기). ReadOnly run에서 만든 memory·skill의 일반 run 회수 및 Pod 교체 후 연속성, Desktop의 동일 Gateway 연결도 아직 입증되지 않았다. |
| 3. durable run ledger와 이벤트 | claim 복구 구현·배포, 주요 live 경로 확인, 종료·복구 검증 일부 대기 | Kanzo `run_id` claim, SQLite intent 복구, SSE wake 및 readiness gate가 배포됐다. 활성 worker drain도 반영됐다. 외부 댓글 게시 중 종료와 restart 복구, 경합 조건은 남아 있다. 다중 dispatcher lease/CAS는 미구현이며 replica 1개를 유지한다. 자동 재시도는 추가하지 않는다. |
| 4. 상주 executor와 작업 도구 격리 | 배포 완료, 파일 도구 경계 canary 통과, lifecycle 시나리오 일부 미검증 | 작업 sandbox/lease, heartbeat, 취소·불확실성 격리 경로가 배포됐다. #70에서 /workspace 쓰기는 sandbox EROFS로 차단되고 /scratch 파일 도구 왕복·정리가 성공했다. #72 Gateway 로그에서 symlink 해석 후 허용 roots 밖 target이 거부됨을 확인했다. 취소 후 자식 프로세스 회수, 동시성·재시작·장기 작업 검증은 남았다. |
| 5. 결과·추적·위임 | task/run metadata live canary 통과, terminal outcome·generation/delegation 검증 대기 | #39 run `ac261e85586d`에서 executor ledger→Gateway session→Langfuse trace를 같은 ID로 대조했다. `fields=metadata`를 명시한 Observations API v2 응답에서 root와 generation 모두 task/run ID가 일치한다. 정적 코드 검토에서는 결과 저장과 최종 ledger/Kanzo 상태 전이가 별도 경로이고, Langfuse terminal 상태 reconcile 기록, generation 중복 판별, delegate 부모·자식 식별 연결은 확인되지 않았다. 이는 실제 trace 중복이나 불일치가 발생했다는 증거가 아니며 각각 #48, #49에서 검증한다. |
| 6. 성능·학습 연속성 | 기초 계측·PVC 영속성 확인, 기준선과 재사용 검증 미완료 | phase duration histogram과 Prometheus scrape, 영속 Hermes HOME은 있다. 기존 표본은 비교 가능한 baseline이 아니고 p95·run별 resource peak 근거가 부족하다. 일반 HOME의 PVC 보존은 확인됐지만 ReadOnly 프로필에서 쌓은 스킬·기억·회고가 별도 실행과 재시작 후 재사용되는지는 검증되지 않았다. |

### 2026-11-15 — 파일 도구의 broker sandbox 경로 검증과 Phase 5·6 재감사

- #68은 /workspace와 /scratch의 write_file이 HERMES_WRITE_SAFE_ROOT에서 막혀 broker sandbox에 도달하지 못함을 재현했다. Hermes 0.23.1 Gateway session context에 source=oneshot이 전달되지 않아 해당 조건이 거부를 유지한 것이 원인으로 확인됐다.
- b9a32d2는 source 문자열 조건을 제거하고, 현재 세션 ID와 일치하는 active kanzo_broker environment, broker session/lease 증명, sandbox 내부 realpath 검사를 모두 통과한 경우에만 safe-root 거부를 해제한다. 다른 backend, stale session, workspace/scratch 밖의 resolved target은 계속 거부한다.
- #70 live canary에서 /workspace 쓰기는 host guard가 아니라 sandbox mount의 read-only filesystem 오류로 실패했다. /scratch write_file은 19 bytes verified, read_file 결과는 kanzo-scratch-probe였고 terminal cleanup 후 잔여물이 없었다.
- 9774844는 authorization 분기 결과를 로그에 남긴다. #72의 /scratch/khl72-outside -> /tmp symlink 쓰기는 생성되지 않았고, Gateway 로그에 file safe-root authorization denied (resolved target outside sandbox roots)가 남았다. #70/#72는 확인 근거 댓글을 남기고 Done 처리했다. worker Argo revision 977484467bd8710c558bc330bf582419d280bb57는 Synced/Healthy, Executor는 3/3 Ready다.
- Phase 5 감사: task/run ID의 Langfuse 귀속은 구현 및 live 확인됐다. 종료 상태 reconciliation, Langfuse generation dedupe, delegate 부모·자식 trace correlation은 확인되지 않았다. 실제 중복 발생 여부는 단정하지 않는다.
- Phase 6 감사: 비교 가능한 입력·도구·결과 조건의 성능 표본과 run window별 CPU/memory peak가 부족하다. Hermes HOME의 PVC 지속성은 확인됐으나 ReadOnly HOME이 스킬을 매 실행마다 원본에서 다시 복사하고, 학습/회고 결과를 원본과 합치는 경로는 확인되지 않아 메모리·스킬·회고 연속성은 미검증이다.

### 2026-11-15 — #47 비교 기준선 실행 시작 및 live sandbox 설정 대조

- 사용자가 로드맵 진행을 위임한 뒤 #47을 Backlog에서 Todo로 옮겼고, Kanzo 이벤트 경로가 run `db69643f27a1`을 시작했다(08:47 UTC). 이 실행은 동일 조건의 성공한 read-only researcher 표본을 수집하는 작업이며, 결과가 나오기 전에는 기준선으로 확정하지 않는다.
- 현재 live Argo Application `kanzo-agent-worker`는 revision `fb676c1356a04fee52fec8369609ce1c0a71ef36`에서 Synced/Healthy다. `mcp` namespace의 Dispatcher와 Executor는 각각 1/1 Ready다.
- Live runner Pod `kanzo-run-6812471543ec06998002`는 broker 코드의 결정적 이름 규칙 `kanzo-run-<sha256(task_id:run_id)[0:20]>`에 `hermes-harness-lab#47` / `db69643f27a1`을 대입한 값과 일치한다. Pod는 08:47:27 UTC에 시작했고 Running이다. 설정은 `readOnlyRootFilesystem=true`, `/workspace` read-only, `/scratch` writable, `automountServiceAccountToken=false`다. namespace의 NetworkPolicy는 기본 ingress/egress deny이며 runner의 egress는 broker와 cluster DNS로 제한된다. 이는 run에 귀속된 설정 증거다. 실제 파일 쓰기 거부, 취소 시 자식 프로세스 회수, memory/skill 연속성은 여전히 별도 런타임 검증이 필요하다.
- After user delegated roadmap execution, #50 was moved from Backlog to Todo at 09:02 UTC. It is queued behind the still-active #47 run and checks the terminal, file-tool, and execute_code write paths separately; no second run has started yet.
- Executor의 live `hermes-home` volume은 PVC `kanzo-agent-hermes-home`이며, 현재 `/opt/data/home/.kanzo-bootstrap-version` 값은 `1`이다. 해당 버전 marker 이후 bootstrap 경로는 Hermes setup/profile 복사를 다시 하지 않고 `sync_hermes_env.py`로 루트와 세 프로필의 관리 대상 `.env` 값만 갱신한다. 이는 재시작 시 학습 파일을 초기화하는 경로가 없음을 뒷받침하지만, ReadOnly run에서 memory/skill을 쓰고 일반 run에서 재조회하거나 Pod 교체 뒤 실제로 이어지는 동작은 아직 검증되지 않았다.

### 2026-11-15 — Phase 5 정적 감사와 후속 제안

- 읽기 전용 코드 검토에서 Hermes 원문 결과, 게시 단계 표식, 최종 run 상태가 별도 영속화 경로를 쓰는 것을 확인했다. 결과 저장 뒤 실패·취소가 발생하면 결과와 실행 상태가 서로 다른 의미를 가질 수 있다. 검토한 코드만으로 실제 run에서 불일치가 발생했다고 단정할 수는 없다.
- task/run ID는 Gateway session과 Langfuse metadata에 전달되지만, 워커가 최종 trace 상태를 reconcile하는 표식, generation 이벤트 중복 제거, delegate 부모·자식 trace 연결을 기록하는 경로는 확인되지 않았다. 이는 구현 범위의 공백이며 Langfuse 중복 관측이 실제 발생했다는 증거는 아니다.
- 모델을 재실행하지 않고 최종 결과 상태를 대조하는 #48, generation 중복 여부와 delegate trace 연결을 검증하는 #49를 backlog 제안으로 추가했다. 둘 다 Backlog에 두었으며 구현, 프로필 변경, 추가 모델 실행은 시작하지 않았다.
- Live sandbox 설정과 실제 쓰기 동작의 차이를 확인하기 위해 #50을 Backlog에 제안했다. 수락 기준은 source SHA를 보존하면서 terminal, Hermes file 도구, execute_code의 `/workspace` 쓰기를 각각 확인하고 `/scratch` 임시 쓰기를 검증하는 것이다. 이 카드는 사용자가 Todo로 옮기기 전까지 실행하지 않는다.

### 2026-11-15 — Phase 2·3·6 감사와 취소 감시 보강

- 현재 Argo revision은 `d21ccb23ddea971c8cc24ab098ba892720348005`; Dispatcher와 Executor는 각각 1 replica다. 취소 재확인과 race 보강 수정이 배포됐다. Recreate 교체 과정에서 60초 종료 유예가 지나 새 Dispatcher가 뜰 때까지 약 1분간 dispatcher 비가용 시간이 있었다. 새 Pod는 정상 기동했고 로그는 agent가 가져갈 수 있는 Todo가 없다고 기록한다. #29는 사람 소유 #31을 기다리므로 실제 실행은 시작하지 않았다.
- Phase 2 감사에서 프로필 DB와 OMH memory 자료가 PVC에 존재하는 것은 확인했으나, ReadOnly 실행에서 만든 memory·skill이 일반 실행에서 다시 사용되는지와 Pod 교체 후 연속성은 검증되지 않았다. Desktop은 내부 raw Gateway 포트가 아니라 Hermes remote backend(`/dashboard`, 9119)에 연결해야 한다([Hermes Desktop docs](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/desktop.md)). 현재 Tailscale Ingress는 `hermes.tail5389e9.ts.net`을 가리키며 Basic Auth 값은 Vault에서 dashboard로 전달된다. 이 Windows 장치의 Tailscale MagicDNS suffix는 `taila2b363.ts.net`이고 Hermes 주소 DNS 조회도 실패했다. 현재 장치는 다른 tailnet에 있으므로 Desktop 원격 연결은 아직 검증할 수 없다. 장치의 tailnet을 임의 변경하지 않는다.
- Phase 3 감사에서 단일 Dispatcher 설정이 확인됐다. 다중 replica는 현재 지원되는 소유권 모델이 아니므로 1 replica로 유지한다. 이벤트 뒤 `get_task`가 일시 실패하면 취소 신호를 놓칠 수 있는 경로를 찾아 재확인 상태를 보존하도록 수정·배포했다. 독립 검토에서 완료 경로 이후 취소 오인, `_refresh_cancel_state` 중복 전송, 취소 철회 뒤 오분류 경합을 찾아 lock 기반 소유권 검사, watcher 단일 전송, 취소 해결 상태 통지 및 경합 테스트를 추가했다. 취소 철회·원격 불확실 경로까지 포함한 새 worker/ledger 테스트 15개와 기존 worker 테스트 11개가 통과했고, 최종 코드가 revision `d21ccb2`에 배포됐다.
- Phase 6 감사는 idle 시점만 유효하다고 판정했다. 실행별 CPU·메모리 peak, 단계별 지연, Langfuse 사용량과 학습 연속성을 비교할 근거는 실제 성공 run이 생긴 뒤 수집해야 한다.

## 변경 이력

### 2026-11-14 — Gateway와 dispatcher 분리 경계 조사

- Hermes v0.23.1 소스에서 기본 attach가 로컬 control socket으로 session ticket을 발급받은 뒤 Gateway WebSocket에 연결하는 것을 확인했다. 명시적 `HERMES_TUI_GATEWAY_URL`은 해당 티켓 발급 절차를 건너뛰며, 공식 문서도 이를 일반적인 원격 attach 설정으로 설명하지 않는다.
- 다음 설계는 Gateway 세션·프로필 상태를 소유하는 상주 Hermes executor API를 두고 dispatcher와 그 API 사이에 인증된 내부 계약을 두는 것이다. 같은 HOME PVC를 독립 프로세스에서 직접 공유하거나 dispatcher가 Hermes 비공개 control socket을 직접 사용하는 방안은 검증되지 않았다.
- Gateway 연결 제약과 executor API 경계는 로드맵의 목표 구조 및 Phase 4 설계 항목에 반영했다. 여기에는 조사 근거와 미검증 상태를 남긴다.

### 2026-11-14 — Hermes 의존 이미지와 sandbox 런타임 분리

- broker와 run별 sandbox의 image 참조를 Hermes/OMH worker 이미지에서 분리했다. broker용 Python slim 이미지를 추가하고, 실행 이미지는 digest 고정 Node 22 기반에 Python 3·pip·venv·컴파일 도구, 셸·Git 유틸리티, 사전 준비된 pnpm을 포함하도록 구성했다. Buildah 스크립트가 두 이미지를 각각 빌드·K3s containerd에 import하도록 했다.
- worker·sandbox Kustomize 렌더링, sandbox/worker Python 14개 AST 구문 분석, `git diff --check`를 확인했다. 이미지 빌드/import는 실행하지 않았으므로 결과 digest, 실제 명령 환경, 패키지 설치용 내부 proxy, Pod 실행은 아직 검증되지 않았다. 직접 외부 egress는 현재 NetworkPolicy에서 거부된다.

### 2026-11-14 — Gateway submission과 run/session 추적 보강

- 독립 검토에서 session cwd가 worker workspace를 가리켜 run sandbox 밖 파일 도구 경로를 만들 수 있고, `session.create`의 초기 generation을 실제 prompt 실행 generation으로 오인해 취소가 거부될 수 있음을 확인했다. Gateway session cwd를 `/workspace`로 바꾸고, run 상태는 sandbox 준비가 끝나고 prompt 직전까지 `preparing`으로 둔다.
- Hermes v0.23.1의 공식 CLI 문서는 `stream-json` 출력에 admission ID가 없음을 확인했다. 그래서 broker-backed run은 CLI 재호출 대신 새 `gateway_execution.py` helper에서 canonical `prompt.submit`을 호출하고, receipt를 worker에 즉시 전달해 run ledger에 저장하도록 전환했다. `session.create` generation은 별도로 기록하고, 취소에 쓸 generation은 run 이벤트에서 확인된 값만 authoritative로 취급한다. 장부 저장 실패는 prompt 시작 전에 작업 실패로 처리한다.
- worker source archive, broker 검증기, sandbox init의 크기/파일 수 한계를 각각 4 GiB·20,000개로 일치시켰다. worker ConfigMap/Pod mount에 helper를 추가했다.
- 정적 확인: 10개 Python 파일 AST 파싱, sandbox/worker 두 Kustomize 렌더링, `git diff --check` 통과. 이는 코드와 manifest 구문만 확인한다. Hermes 설치 버전 API 응답, 실제 admission/cancel 처리, 격리 tool 실행은 아직 검증되지 않았고 배포하지 않았다.

### 2026-11-14 — admission 단위 취소와 프로필 연결 정밀화

- broker 작업 취소 시 run 장부에 저장된 admission ID를 `gateway_control.py`에 전달한다. 해당 admission이 queued면 그 ID만 취소하고, started면 같은 admission의 execution generation과 현재 running 상태가 확인될 때만 interrupt한다. 누락·중복·terminal admission은 취소 성공으로 추정하지 않는다. 이전 Gateway CLI 경로는 admission ID가 없는 기존 run에만 남긴다.
- `gateway_execution.py`는 현재 admission ID와 일치하는 Gateway 이벤트에서만 execution generation을 전달한다. submit receipt나 다른 admission 이벤트의 generation은 사용하지 않는다. 실행 helper도 선택한 프로필의 Gateway endpoint `profile_id`를 검사한다. admission receipt는 durable ledger 기록 전에도 실행 메모리에 보존해, ledger 오류 시 finally 경로에서 해당 admission만 취소할 수 있게 했다. helper 연결과 취소 연결 모두 Hermes의 context-local home override로 프로필별 Gateway ticket을 발급한다.
- 정적 확인을 다시 실행했다: worker/sandbox Python 파일 14개 AST 파싱, 두 Kustomize 렌더링, `git diff --check` 통과, 로드맵 파일 변경 없음. kubectl context 조회는 `current-context is not set`으로 실패했다. 따라서 클러스터 상태 확인이나 배포는 하지 않았으며, 실제 Gateway API 동작은 아직 검증 전이다.
- 추가 정적 리뷰에서 발견한 자동 세션의 local terminal fallback 가능성은 `KANZO_AUTOMATED_RUN`·task ID·run ID를 Gateway session client env에 포함하고, profile의 `client_env_passthrough`와 broker mapping 대조를 적용해 실패 시 broker-only로 만들었다. Hermes v0.23.1 `execute_code`는 terminal backend의 environment builder를 재사용하므로 같은 provider를 통과하는 계약이 확인됐다. 그 실제 세션 전파와 실행은 클러스터에서 아직 검증되지 않았다.
- broker run lease를 180초로 제한하고 worker가 30초 주기로 heartbeat를 보내 연장하도록 했다. worker가 사라지면 만료 후 reaper가 sandbox Pod를 회수하도록 바꿨다. 종료 중 API 장애와 프로세스 잔존은 런타임 장애 주입으로 확인해야 한다.
- 검토에서 남은 구조적 미완료는 Gateway와 dispatcher가 같은 `Recreate` Deployment/Pod에 들어 있는 점, broker·sandbox가 같은 Hermes 이미지 태그를 재사용하는 점, MCP 도구별 권한 경계와 실제 delegate inheritance 확인이다. 이미지 빌드/롤아웃은 진행하지 않았다.

### 2026-11-14 — run별 sandbox 수직 경로 코드 초안

- worker는 깨끗한 Git checkout에서 추적 파일만 archive로 만들고 broker에 보내도록 `broker_client.py`와 `workspace_transfer.py`를 연결했다. Hermes prompt 제출 전 Gateway 세션을 생성하고 task/run lease에 bind한 뒤 같은 세션을 resume한다. 완료 시 변경 archive의 SHA-256과 경로/삭제 manifest를 검증하고 checkout에 적용한 뒤 run을 finalize한다. read-only repo run은 같은 Gateway 세션을 사용하도록 연결했다.
- broker는 단일-use source archive, lease/session binding, Kubernetes API 기반 고정 per-run Pod, terminal start/poll/cancel proxy, 변경 archive 반환, 만료 cleanup을 구현하는 초안이다. sandbox 명령과 runner가 같은 UID를 쓰므로 runner에 `PR_SET_DUMPABLE=0`을 적용하고 자격 증명 파일·Secret 생성 및 Secret RBAC를 제거했다. runner는 시작 환경에서 토큰을 읽어 자식 프로세스 환경에서 제외하며, 제한된 argv 실행, process-group 취소, output/시간/동시성 제한과 workspace diff export를 제공한다. source init은 내부 broker에서 digest 확인 후 regular-file/directory만 안전하게 추출한다.
- GitOps 초안은 `kanzo-sandbox` Argo Application, broker Deployment/Service/PVC, VSO credentials, namespaced Pod-only RBAC, sandbox default-deny NetworkPolicy, ResourceQuota를 포함한다. worker/Gateway profile에 terminal provider를 설치·선택하도록 bootstrap 경로도 추가했다.
- 검증: 9개 Python 파일 AST 구문 검사 통과, `platform/kanzo-sandbox` 및 `platform/kanzo-agent-worker` Kustomize 렌더링 통과, `git diff --check` 통과. 이건 정적 검사만 의미한다. task 실행이나 클러스터 변경은 아직 하지 않았다.
- 남은 선행 작업: broker/worker API의 장애·재요청·pod cleanup 경합 점검, Hermes 0.23.1의 plugin 활성화 및 CLI resume 옵션을 설치 버전에서 확인, secret scope와 namespace RBAC 검토, ConfigMap/image mount 검증, 실제 sandbox run으로 terminal/file/execute_code·read-only·취소·결과 회수를 검증한다. 이 기준이 충족되기 전에는 롤아웃하지 않는다.


### 2026-11-14 — Gateway 세션 식별 및 정확한 취소 배포

- 커밋 `4b4b316`를 GitOps `main`에 push했고 Argo CD가 revision `4b4b316c0861a9461fe48db2676a2af6a5c17f62`를 Synced/Healthy로 적용했다. 새 Pod `kanzo-agent-worker-578c4977c7-fm266`은 5/5 Ready, restart 0이다.
- worker와 ReadOnly executor가 생성된 Gateway `session_id`를 run ledger 및 artifact metadata에 저장한다. 취소 helper는 고유 admission ID에 `prompt.cancel`, 실행 중인 generation에 `session.interrupt`를 보내며, 성공을 확인하기 전에 worker의 로컬 CLI viewer만 종료하지 않는다.
- worker 및 ReadOnly executor에 마운트된 변경 파일들의 SHA-256이 Git 저장소 파일과 일치했고, `kanzo_worker_task_active`는 0이었다. worker 로그는 현재 활성 프로젝트에 처리 가능한 태스크가 없다고 기록했다.
- 이 변경은 실제 Kanzo 작업을 실행하거나 중단해 검증하지 않았다. run별 도구 권한 격리와 broker 구현은 다음 Phase 4 작업이다.

### 2026-11-14 — 장기 산출물과 결과 댓글 복구

- 커밋 `e59a758`를 GitOps `main`에 push하고 배포했다. Argo CD는 Synced/Healthy, worker Pod는 5/5 Ready, restart 0, active gauge 0이었다. `worker.py`, `run_ledger.py`, `artifact_store.py`의 배포 SHA-256이 로컬 파일과 일치했다.
- 전체 Hermes 응답과 메타데이터를 Hermes HOME PVC의 task/run 디렉터리에 원자적으로 기록한다. manifest는 source SHA와 결과 해시를 포함하며, 파일 권한은 사용자 전용으로 제한한다. 기존 artifact는 자동 삭제하지 않는다.
- 결과 댓글에 숨김 run/chunk marker와 정확한 게시 본문을 저장한다. 재시작 때 이미 올라간 조각은 건너뛰고 빠진 결과만 게시한다. 외부 작업을 자동 재실행하지 않는다. 출력의 알려진 토큰 패턴과 주입된 비밀값은 artifact/댓글 저장 전에 가린다.
- ReadOnly 데이터 경계를 조사했다. 별도 HOME 때문에 Hermes 세션 DB가 일반 프로필과 분리되고, 프로필 로컬 skills/plugins/scripts/`.omh`는 매 run 삭제 후 원본에서 다시 복사되어 학습 변경이 덮일 수 있다. OMH 절대 경로 일부는 공유되지만 회고와 학습이 다음 세션에서 검색되는지는 미확인이다. 별도 Gateway HOME 복사 방식을 상주 Gateway + run별 도구 backend로 대체하는 Phase 2/4 작업이 남아 있다.
- 이 배포는 실제 Kanzo 태스크로 검증하지 않았다. 게시 재생성·부분 댓글 복구 동작도 태스크 기반으로 아직 검증되지 않았다.

### 2026-11-14 — Hermes session과 terminal 정책 계약 확인

- 배포 이미지 `Hermes Agent v0.23.1`의 `hermes_cli/gateway_chat.py`, `gateway/session_controls.py`, `gateway/session_policy.py`를 확인했다. canonical 대화 경로는 `session.create` → `prompt.submit`이며 중단은 `session.interrupt`다. Gateway connection이 선택한 profile scope에서 세션을 만들고, 생성 정책에는 model/provider, reasoning, toolsets, cwd 등이 전달된다.
- `session.create`가 받는 `CREATE_FIELDS`에는 run별 terminal backend override가 없다. `build_policy()`는 profile에서 terminal scope를 만들고 `terminal_json`으로 세션 정책에 고정한다. 따라서 Desktop의 전역 환경 변수를 태스크마다 바꾸면 안 된다. Kanzo profile에 등록 backend를 고정하고 broker가 run/session 매핑을 검사하는 방식이 다음 설계·구현 경로다.
- 확인은 설치된 코드 경로와 설정 계약을 읽은 것이며, API에 모델 요청을 보내거나 실제 Kanzo 태스크를 시작하지 않았다.

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


### 2026-11-14 — 상주 executor API 연결

- 인증된 executor API와 표준 라이브러리 클라이언트를 worker ConfigMap에 연결했다. worker는 API로 세션을 만들고 submit 이벤트를 스트리밍하며 admission/generation receipt를 장부에 기록하고 task/run/session/admission identity를 지정해 취소한다. API client가 없으면 Gateway helper 직접 호출로 우회하지 않고 실패 종료한다.
- 8766 포트 API sidecar, SQLite 상태 경로, worker URL/token 참조, `KANZO_EXECUTOR_API_TOKEN`용 VaultStaticSecret template을 추가했다. API와 dispatcher는 아직 같은 Pod에 있어 별도 수명주기 분리는 남아 있다.
- 세션 생성 시 Kanzo 및 Hermes Langfuse task/run 식별자를 Gateway session environment에 붙인다.
- 취소 경로는 API 소유 원격 실행을 로컬 process 부재보다 먼저 처리한다. worker가 generation receipt를 받기 전에도 API가 같은 admission에 저장한 generation을 사용해 취소하도록 보강했다.
- 정적 확인 통과: worker/sandbox Kustomize 렌더링, 실행 관련 Python 9개 AST 파싱, `git diff --check`. 테스트, 이미지 빌드, 클러스터 배포, 태스크 실행은 하지 않았다.
- Vault MCP 쓰기는 이 세션에 Vault token이 없어 실패했다. `KANZO_EXECUTOR_API_TOKEN`이 Vault에 준비됐는지는 확인되지 않았으며 sidecar 시작 전에 secret 경로를 검증해야 한다. 롤아웃은 하지 않았다.


### 2026-11-14 — Phase 4 실행 경계 보강 및 이미지 빌드

- executor API/client는 `executor_exit`의 `settled=true`와 code 0 및 정상 child 종료를 모두 요구한다. 스트림이 끊기거나 terminal receipt가 없으면 성공 처리하지 않고 `ambiguous`로 남긴다. SIGTERM은 새 요청을 차단하고, 기록된 admission이 있으면 그 실행만 취소 시도한 뒤 확인 결과에 따라 `cancelled` 또는 `ambiguous`를 저장한다. worker가 admission receipt를 아직 못 받았을 때도 API가 저장한 해당 run의 admission을 사용해 취소할 수 있다.
- Kanzo의 review + human=true 이후 Gateway 취소가 확인되지 않으면 worker는 결과 게시·sandbox finalize·새 태스크 claim을 멈추고 run과 lease를 격리한다. 취소 확인 전 완료나 종료로 꾸미지 않는다.
- broker가 sandbox init/runner에서 실행하는 인터프리터를 `python3`로 맞췄다. 기존 이미지 빌드에서 PNPM_VERSION build arg가 Docker build stage에 전달되지 않아 태그와 실제 패키지 버전이 달랐음을 확인했다. ARG를 stage에 다시 선언하고 pnpm 버전 일치 검사를 추가해 `12.18.0`을 직접 설치하도록 수정했다. 수정 이미지 두 개를 Buildah로 빌드해 로컬 K3s containerd에 import했다. sandbox 이미지에서 Python 3.11.2와 pnpm 12.18.0 실행을 확인했다.
- Hermes 0.23.1의 실제 file tool 구현은 현재 선택된 terminal environment의 `execute`를 사용한다. Kanzo 자동 세션은 `KANZO_AUTOMATED_RUN`·task/run ID를 전달하고 broker provider가 정확한 Hermes session mapping을 요구하므로, 파일 도구가 별도 로컬 workspace 경로로 바로 쓰는 것으로 보인다는 정적 감사 가정은 코드 경로와 일치하지 않는다. 실제 세션에서 파일 쓰기·read-only 거부는 아직 검증되지 않았다.
- 정적 확인 통과: worker/sandbox Kustomize 렌더링, worker 및 sandbox Python 16개 AST 구문 분석, `git diff --check`. 테스트와 클러스터 rollout은 하지 않았다. Vault MCP에 유효한 Vault token이 없어 `KANZO_EXECUTOR_API_TOKEN`은 기록하지 못했고, dispatcher와 executor는 아직 같은 Pod 수명주기를 공유한다.

### 2026-11-14 — dispatcher 상태 경로 분리 준비

- worker의 heartbeat, run ledger, 결과 artifact 경로를 Hermes 프로필 HOME과 독립적으로 지정할 수 있도록 `KANZO_WORKER_STATE_DIR`를 추가했다. 기본값은 기존 `/opt/data/home/kanzo-worker`를 유지하므로 PVC 전환 전 런타임 동작은 바뀌지 않는다.
- 다음 단계는 dispatcher 전용 영속 볼륨과 기존 ledger/artifact의 안전한 이관 절차를 마련한 뒤에만 새 경로를 켜는 것이다. 이 변경은 코드 준비 단계이며 클러스터에 배포하지 않았다.
- Vault MCP 도구 호출은 `vault token not provided for session`으로 실패했다. 따라서 `kv/mcp/kanzo-agent`의 `KANZO_EXECUTOR_API_TOKEN` 존재 여부와 VSO 동기화는 아직 확인되지 않았다.
- executor API에 인증된 provider/profile 인증 상태 확인 API와 무인증 `/livez`, `/readyz` probe 경로를 추가했다. dispatcher는 `KANZO_EXECUTOR_API_URL`이 설정되면 자기 컨테이너의 Hermes CLI를 실행하지 않고 resident executor에 인증 상태를 묻는다. 이로써 별도 Pod 전환에 필요한 API 계약은 준비됐지만, 현재 매니페스트 URL은 여전히 loopback이고 배포·런타임 확인은 하지 않았다.
- 기존 API sidecar에도 named port와 startup/readiness/liveness probe를 붙여 executor API가 프로세스 상태뿐 아니라 저장소·프로필 준비까지 보고되게 했다. 별도 Pod 전환 때 그대로 재사용할 수 있다.

### 2026-11-14 — Executor API 서비스 경계 및 상태 이관 준비

- dispatcher state migration 도구를 추가했다. worker 정지 상태에서만 실행하고, source에 unfinished run이 있으면 거부하며, SQLite backup·무결성/행 수·artifact 해시를 검증한다. 새 도구는 worker code ConfigMap에 포함된다. 아직 PVC 이관은 하지 않았다.
- Executor API에 내부 ClusterIP Service를 추가하고 worker의 호출을 Service DNS로 전환했다. ingress/egress NetworkPolicy는 worker Pod만 8766에 접근하도록 제한한다. API에는 Kubernetes readiness/liveness probe를 추가했다.
- resident executor Deployment 분리 자체는 미완료다. 현재 Service와 API는 기존 worker Pod를 가리키며, 클러스터는 이전 Argo revision `e0bb839f`에서 1/1 Ready 상태다. Vault API 토큰 키는 live Secret에서 비어 있어 배포하거나 롤아웃하지 않았다.
- 정적 점검: worker/API/client/migration Python AST parse, worker Kustomize render, `git diff --check` 통과. 테스트는 실행하지 않았다.
- dispatcher가 resident API URL을 사용하도록 설정되면 원격 API 실패 시 Hermes CLI, local Gateway control socket, ReadOnly loopback 경로로 후퇴하지 않도록 fail-closed 처리했다. 따라서 분리 후 프로필 인증/취소/작업 제출의 권위 상태가 dispatcher 컨테이너에 생기지 않는다.
- 리뷰에서 ingress 정책이 executor/worker Pod를 선택하면서 8766 이외 연결을 차단할 수 있음을 발견했다. 현재 클러스터에서 확인한 Prometheus label과 Tailscale ingress proxy label을 기준으로 9105/9119 ingress 허용을 추가했다. 실제 policy는 미배포 상태다.
- 리뷰 지적대로 `/readyz`를 DB/프로필 경로 확인에 그치지 않고 Hermes Gateway의 canonical endpoint가 `ready`인지 확인하도록 보강했다. SIGTERM drain 상태에서는 readiness가 즉시 실패한다. HTTP probe는 인증정보 없이 내부 readiness만 받으며, API 작업 경로는 bearer 인증을 유지한다.

### 2026-11-14 — GitOps 롤아웃 검증

- `0fb3b34`의 executor/dispatcher 분리와 sandbox 구성을 `main`에 반영했다. PVC가 `WaitForFirstConsumer` 상태에서 Argo sync wave를 막는 문제를 상태 PVC binder Job으로 해결했고, migration hook은 ledger 0행·artifact 0개를 검증했다.
- 대형 worker 코드 ConfigMap의 client-side apply 주석 크기 초과를 해당 ConfigMap의 Server-Side Apply로 바꿨다. Hermes broker plugin manifest의 개행 escaping 오류도 수정해 새 executor Pod에서 재확인 중이다.
- 현재 Argo는 `734afc7` 롤아웃 중이며 새 executor Pod의 Hermes 초기화 단계다. Kubernetes Secret에는 `KANZO_EXECUTOR_API_TOKEN` 항목이 있지만 디코드 값 길이는 0이다. Executor API는 32자 미만 토큰이면 시작을 거부한다. dispatcher는 아직 실행되지 않았고 Kanzo 작업도 처리하지 않았다.
- 필요한 Vault 항목은 mount `kv`, path `mcp/kanzo-agent`, field `KANZO_EXECUTOR_API_TOKEN`에 비어 있지 않은 실제 토큰이다. MCP Vault 도구 세션도 아직 Vault 인증 토큰을 받지 못해 직접 확인할 수 없다.

### 2026-11-15 — Executor 토큰 반영, heartbeat 수정 롤아웃

- 사용자가 `KANZO_EXECUTOR_API_TOKEN`을 추가한 뒤 live Kubernetes Secret의 항목을 확인했다. 디코드 값은 64바이트이며 실제 값은 출력하거나 문서화하지 않았다.
- 복구 후 생성한 읽기 전용 카나리 `hermes-harness-lab#29`는 run `70e4105000c1` 실행 30초 무렵 `AttributeError: 'Worker' object has no attribute '_heartbeat_sandbox'`로 종료됐다. heartbeat 호출 경로는 있었지만 구현 메서드가 누락된 것이 원인이다. 워커는 이를 실패로 처리해 카드를 review/human=true로 보냈고, 자동 재시도는 하지 않았다.
- `711a1fe`에서 run-scoped sandbox lease heartbeat 메서드를 추가하고 GitHub `main`에 반영했다. Argo가 새 revision을 감지한 뒤 상태 migration hook을 완료하고 Executor와 Dispatcher를 순차 재시작했다. 최종 상태는 Argo `Synced/Healthy`, revision `711a1fe901f4e64b9056bcaf5eabd58d21193d8e`, Executor 1/1 Ready, Dispatcher 1/1 Ready다.
- live Dispatcher ConfigMap에 `_heartbeat_sandbox` 정의가 있고 실행 중인 모듈에서 `Worker` 클래스 메서드로 로드되는 것을 확인했다. Executor API `http://127.0.0.1:8766/readyz`는 `200 {"status":"ready"}`를 반환한다.
- Canary #29는 review/human=true 상태 그대로 두었고 다시 실행하지 않았다. 수정된 heartbeat의 실제 작업 경로 검증은 새 명시적 Todo 실행이 필요하다. 그 전까지 이 수정은 배포 및 모듈 로드까지 확인된 상태로 기록한다.

### 2026-11-15 — Langfuse 중복 trace 경로 정리

- LiteLLM OTLP trace가 Alloy를 통해 Langfuse로 재전송되던 경로를 제거했다. Alloy의 Langfuse exporter/identity transform/전용 키 주입을 삭제하고, OTLP metrics → Prometheus remote write 경로는 유지했다. LiteLLM의 OTLP endpoint와 ServiceMonitor 설정도 유지했다.
- `2eec578`을 GitHub `main`에 반영했다. 상위 Argo Application을 새로고침한 뒤 `home-server-wsl` 및 `monitoring-alloy`가 Synced/Healthy가 됐고, Alloy Pod가 새 ReplicaSet으로 교체됐다. live Alloy ConfigMap에는 `metrics` output과 Prometheus remote write만 남았으며 trace output과 Langfuse exporter는 없다. 실행 중인 Alloy 컨테이너 환경에도 Langfuse 키가 주입되지 않는다. Alloy는 정상 기동 로그를 남겼다.
- 상주 Hermes에 설치된 Langfuse 플러그인 코드를 live 컨테이너에서 확인했다. `HERMES_KANBAN_TASK`와 `HERMES_KANBAN_RUN_ID`를 trace metadata의 `kanban_task_id`/`kanban_run_id`로 읽고 Hermes session ID를 Langfuse session grouping에 사용하는 구현이다. Gateway session 생성 코드가 해당 두 환경 값을 설정한다. 따라서 ID 전달 경로는 코드상 연결됐지만 실제 실행 trace의 metadata/session 값은 아직 대조하지 않았다.
- 사용자 식별은 플러그인에서 인증된 플랫폼 sender ID가 제공될 때만 `user_id`를 설정한다. Kanzo 자동 실행의 실제 요청자를 확인하지 못한 상태에서 `uid:10000`을 사용자 ID로 쓰지 않는다.
- 남은 Phase 5 확인: 실제 Kanzo run 한 건으로 task/run/session ID와 Kanzo·run ledger·Langfuse를 대조하고, Hermes에서 generation이 한 번만 보이는지 확인한다. `hermes-harness-lab#29`는 review/human=true라 재실행하지 않았다. Alloy 설정 YAML 파싱과 Prometheus 경로 보존 확인은 완료했으며, 클러스터에서는 Alloy 재기동까지 확인했다.

### 2026-11-15 — Kanzo run과 Langfuse 실제 대조

- 사용자가 요청한 수동 확인을 위해 `hermes-harness-lab#29`를 review에서 Todo로 한 번 재실행했다. run `85502b0b7854`는 researcher / openai-api / `iq_3s`로 실행했고 Gateway session `local-1104258490b4b3f2367873277530b1c756531718cdbd1a1adf478e079b1ebd43`을 실행 장부에 기록했다. 모델은 final 응답을 만들었지만 잘못된 human-action handoff를 생성해 장부와 카드는 `waiting` 상태가 됐다.
- Langfuse Observations API v2에서 동일 `sessionId`로 trace `74eccc23f500429cc53398a27a7a7223`의 observation 23개(Generation 8, Tool 14, Hermes turn 1)를 확인했다. 따라서 Kanzo task → run ledger → Hermes Gateway session → Langfuse session/trace 연결은 정확히 대조된다. 이 필터 범위에 Langfuse trace는 한 개였으며 LiteLLM/Alloy 중복 trace는 보이지 않았다.
- Hermes trace metadata에는 Hermes 내부 `task_id`/`turn_id`가 있지만 Kanzo `hermes-harness-lab#29`와 run ID는 없다. 현재 run ledger의 session 매핑이 상관관계의 다리 역할을 한다. terminal 실행에서 `HERMES_KANBAN_TASK`/`HERMES_KANBAN_RUN_ID`도 빈 값으로 관측되어 `gateway_session.py`의 client_env 전달이 Hermes 허용 목록에서 걸러지는 경로를 확인했다. 직접 Kanzo ID metadata 추가는 미완료다.
- Langfuse observation `userId`는 `uid:10000`이었다. 이는 컨테이너 실행 UID이지 검증된 사람 식별자가 아니다. `end_user_attribution=true`가 researcher/builder/reviewer 프로필에 활성화돼 있어 자동 실행에 잘못된 사용자 귀속이 발생한다. 실제 플랫폼 사용자와 실행 주체는 분리해야 한다.
- #29 실행 결과는 #31 human-action 카드 `Confirm rollout of heartbeat fix 711a1fe...`를 만들었고 #29를 `todo (waits on #31)`로 남겼다. #31의 근거는 `_heartbeat_sandbox`를 `/opt/hermes`에서 찾았다는 것이지만, 메서드는 Hermes 패키지가 아니라 `/opt/kanzo-agent/worker.py` ConfigMap에 있다. live Dispatcher의 `Worker` 클래스에서 메서드 로드를 확인했고, 이전 40초 terminal 호출은 exit 0으로 30초 heartbeat를 넘겼다. 따라서 #31의 “아직 미배포” 판단은 잘못됐다. 비교 결과와 반증 근거를 #31에 댓글로 남겼다. #31을 human-owned 상태에서 자동으로 완료 처리하지 않았다.
- Langfuse credential은 이제 executor bootstrap에서 root 및 routed profile `.env`에 동기화되고 파일 권한은 0600이다. 이후 run에서 plugin의 missing-credentials 경고는 없었고 researcher trace가 생성됐다.
- 남은 Phase 5 작업: per-session `KANZO_TASK_ID`/`KANZO_RUN_ID`를 Gateway allowlist 및 Hermes Langfuse hook으로 안전하게 전달하고, automated profile의 잘못된 `uid:10000` user attribution을 끈 뒤 실제 trace로 재검증한다. #29는 #31을 선행 카드로 기다리고 있어 자동 재개 전 상태 판단이 필요하다.

### 2026-11-15 — Langfuse 세션별 Kanzo 귀속 수정 배포

- `8dccff2`에서 Langfuse 플러그인이 공유 Gateway 프로세스 환경 대신 현재 Hermes 세션 컨텍스트의 `KANZO_TASK_ID`/`KANZO_RUN_ID`를 읽도록 시작 시 적용하는 가드된 어댑터를 추가했다. 세션 환경 변수의 허용 목록은 이미 존재하므로 전역 환경 변수로 우회하지 않는다.
- 자동 실행 프로필 `researcher`, `builder`, `reviewer`의 `end_user_attribution`을 false로 고정해 컨테이너 UID를 사람 사용자로 기록하지 않게 했다. 기본 프로필 설정은 유지한다.
- Python 문법, Kustomize 렌더링, `git diff --check`를 확인했다. Argo `kanzo-agent-worker`는 revision `8dccff2`에서 Synced/Healthy이며 Executor 3/3, Dispatcher 2/2 Ready다. executor init 로그에 네 프로필 어댑터 적용이 남았고 live config에서 세 자동 프로필 값이 false임을 확인했다.
- 아직 새 Kanzo 실행의 Langfuse trace에서 task/run ID와 userId 생략을 대조하지 않았다. #29는 #31 human-action 선행 상태라 자동 재개시키지 않았으며, 다음 유효한 Kanzo 실행 후 실제 trace 검증이 남아 있다.
- Worker의 crash window도 확인했다. 현재 MCP `kanzo:next_task`는 project/filter만 받고 Kanzo에서 먼저 카드를 `doing`으로 바꾸며, 이후 worker가 run ID와 SQLite ledger row를 만든다. `kanzo:update_task` 스키마에는 expected status/version 또는 idempotency key가 없어 worker 단독으로 안전한 CAS 전환을 할 수 없다. Kanzo API 지원을 더 조사하기 전에는 claim 경로를 비원자적 read-then-update로 바꾸지 않는다.

### 2026-11-15 — 롤아웃 후 idle 자원 및 시작 시간 기준점

- Kanzo 실행이 없는 단일 시점 `kubectl top` 관측: 노드 CPU 546m(1%), 메모리 15,697Mi(48%, allocatable 약 31.3Gi). MCP namespace는 Dispatcher 4m/36Mi, Executor 23m/618Mi, Sandbox Broker 1m/22Mi였다. 이는 반복 측정한 baseline이 아니라 초기 idle 표본이다.
- Executor Pod는 01:32:52Z 생성, 01:35:53Z Ready였다. `bootstrap-omh`는 165초 실행됐고, 전체 Pod 준비는 약 181초 걸렸다. 현재 반복 Hermes CLI bootstrap은 향후 시작 시간 개선 후보로 남긴다.
- 동등한 Todo 작업을 실행해 active peak, completion latency, token/turn usage를 함께 재지 않으면 성능 개선을 판정하지 않는다.
### 2026-11-15 — Executor persistent bootstrap fast path 검증

- `f3350f0`에서 one-time Hermes persistent-home setup marker와 매 기동 자격 증명 `.env` 동기화를 분리했다. marker가 없거나 버전이 다르면 기존 전체 bootstrap을 수행하고 성공 후 marker를 만든다. marker가 일치하면 프로필/OMH setup과 doctor를 반복하지 않는다. Langfuse session adapter와 automated-profile attribution 보정은 별도 init 단계에서 매번 확인한다.
- marker 최초 생성은 기존 초기화를 그대로 수행해 `bootstrap-omh` 165초가 걸렸다. 이후 executor Pod를 한 번 재시작해 marker 경로를 실제 검증했다. 세 init 컨테이너가 3초 만에 끝났고 Pod는 생성 `01:53:27Z`부터 Ready `01:53:43Z`까지 16초였다. `/readyz`는 `ready`를 반환했다.
- 롤아웃 후 Argo `kanzo-agent-worker`는 `Synced/Healthy`, revision `f3350f085fd07abb3aafd282a9fb4f49fdf7bce2`; Executor 3/3, Dispatcher 2/2 Ready다. `bootstrap-omh` 초기화 중 Hermes CLI가 `uv.lock` 동기화 경고를 반복했으나 exit 0으로 완료했고, 빠른 경로에는 그 반복 CLI 호출이 없다. 경고 자체의 정리는 별도 확인이 남았다.
- 실행 가능한 Todo가 없어 모델 작업은 만들지 않았다. #29는 #31에 대기 중이므로 새 Langfuse trace 검증은 아직 남아 있다.

### 2026-11-15 — run-ID 멱등 claim과 재시작 intent 복구 배포

- Kanzo `next_task`에 선택적 `run_id` 경로를 추가했다. `BEGIN IMMEDIATE` 안에서 Todo 선택·Doing 이동·run ID 기록을 묶고, 재호출은 원래 task만 반환한다. claim 이력은 task 삭제 후에도 남겨 같은 run이 다른 Todo로 바뀌지 않게 했다. GitOps build 스크립트가 기존 카탈로그 폼/SSE 패치와 claim 패치를 적용해 `localhost/home-server/kanzo:0.4.2-agent.3`를 빌드·K3s에 import했다.
- Worker ledger schema v2에 `claim_intents`를 추가했다. Kanzo 호출 전에 정확한 project/filter/run ID/route metadata를 저장하고, Kanzo 응답 후 intent→run 전환을 한 SQLite 트랜잭션으로 수행한다. 네트워크 오류나 해석 불가 응답에서는 intent를 남겨 같은 run ID로 재개한다. 카탈로그에서 비활성화된 project의 intent는 재활성화될 때까지 Kanzo에 재전송하지 않는다.
- 상태 이전 Sync hook이 기존 성공 migration의 ledger v1을 현재 버전으로 거부하는 것을 배포 중 발견했다. verifier가 v1과 v2 schema를 검증하도록 고쳐 hook을 성공시켰으며, dispatcher/executor도 새 ConfigMap을 참조한다. Argo `kanzo`와 `kanzo-agent-worker`는 commit `1ec912e`에서 Synced/Healthy다.
- 검증: Kanzo `cargo fmt --check`, 전체 Rust 테스트 75/75, release 이미지 빌드 및 K3s import, Worker Python 구문 확인, 신규 ledger와 v1→v2 migration smoke, 두 Kustomize 렌더링을 통과했다. 실행 중인 dispatcher에서 MCPProxy를 통해 `kanzo:next_task` signature의 선택적 `run_id` 인자를 확인했다.
- 실태스크 검증은 미완료다. 현재 `hermes-harness-lab#29`는 Todo이나 human-owned #31을 기다리며, dispatcher 로그도 agent-eligible task가 없다고 확인했다. 사람의 선행 판단 없이 이를 실행시키지 않았다.

### 2026-11-15 — Executor readiness claim gate 롤아웃 확인

- `67fa31b`를 배포했다. Executor 3/3 컨테이너와 Dispatcher 2/2 컨테이너가 Ready이며 Argo `kanzo-agent-worker`는 `Synced/Healthy`다.
- 롤아웃 중 Executor가 준비되지 않았던 짧은 구간에 Dispatcher 로그가 `Executor is not ready; leaving Kanzo tasks unclaimed`를 기록했다. Executor 준비를 기다리는 동안 Kanzo task를 claim하지 않는 동작을 live에서 확인했다. 이후 Dispatcher가 재기동되어 정상 Ready 상태가 됐다.
- 이 롤아웃에서 기존 human-gated 작업을 claim하지 않았다. 현재 자동 실행이 가능한 Todo가 없으므로 end-to-end 신규 run 검증은 보류다. #31 사람 검토 및 #29의 선행 관계가 해결된 뒤 실제 run으로 Kanzo 상태, run ledger, 산출물, Gateway session, Langfuse trace를 다시 대조해야 한다.

### 2026-11-15 — #31 doing→review 전환의 배포 시점 상관관계 확인

- Kanzo `get_task`의 현재 값은 `review, human=true`, 마지막 갱신은 `03:20 UTC`다. task 본문과 댓글을 읽었고 두 실행 시작 댓글도 기록되어 있어, worker가 카드를 못 읽은 문제가 아니다.
- 첫 run `ec9bc93b6c01`은 `03:18:51 UTC`에 시작했으나 `03:18:53` Executor 요청이 `ConnectionResetError`로 실패했다. Executor ReplicaSet `kanzo-agent-executor-7fd664fcb5` 생성 시각은 `03:18:54 UTC`로, Executor 롤아웃과 요청 실패가 겹쳤다.
- 두 번째 run `90ca4cd1f3c2`는 세션을 만들고 prompt admission ID `15de9eee02c44c959e850cc2f4ef42dd`까지 발급받았다. Executor ledger에는 submit이 `ambiguous`로 남았다. Dispatcher ReplicaSet `kanzo-agent-dispatcher-7d48974dfb`가 `03:20:12 UTC`에 생성된 직후, `03:20:13` worker 재시작 복구가 미완료 실행을 orphan/review로 격리했다. Hermes 완료 결과는 없다.
- 따라서 첫 실패는 Executor 롤아웃 중 연결 단절, 두 번째는 활성 실행 도중 Dispatcher 롤아웃으로 실행 스트림이 끊긴 것이 직접 원인이다. 이 기록은 최신 readiness-gate 배포 전의 것이다. 현재 `8ca32c5` 배포는 `Synced/Healthy`이며, readiness 미통과 때 claim을 건너뛰는 live 로그도 확인했다. 배포 뒤 #31에 대한 새 run은 ledger에 없다.
- 남은 운영 결함은 활성 Hermes 실행 중 Dispatcher 코드/설정 롤아웃을 유예하는 drain 절차가 없다는 점이다. readiness gate는 새 claim을 막지만, 이미 submit된 실행 스트림을 Dispatcher 재시작 후 재부착하지 않는다. 롤아웃 drain/장기 실행 보존을 별도 구현·검증해야 한다.

### 2026-11-15 — Dispatcher 롤아웃 drain 적용

- `6ee5954`에서 SIGTERM 시 새 claim 중단, `/readyz` 비정상 응답, 현재 `process_one()` 실행이 끝난 뒤 정상 종료하는 drain을 추가했다. 활성 작업 중에는 heartbeat 나이만으로 liveness가 재시작되지 않도록 했다. Dispatcher 종료 유예는 60초에서 1800초로 늘렸다.
- worker 단위 테스트 13개 통과, Python 문법 검사, Kustomize 렌더링, `git diff --check` 통과. Argo `kanzo-agent-worker`는 revision `6ee59540626009acc8c2f06e0f2d5eb95d603c7e`에서 Synced/Healthy이며 Dispatcher 2/2 컨테이너와 Executor 3/3 컨테이너가 Ready다.
- live ConfigMap에서 drain 코드와 1800초 grace를 확인했다. 같은 롤아웃에서 Executor가 준비되기 전 readiness gate가 Kanzo claim을 건너뛰는 로그도 확인했다.
- 활성 Kanzo run이 없어 SIGTERM 중 장시간 실행의 실시간 종료 대기는 재현하지 않았다. #31은 기존 두 run이 review 상태이며 최신 ledger에도 active run이 없다. 사용자가 재개하기 전까지 #31을 변경하거나 재실행하지 않는다.

### 2026-11-15 — #31 재실행 결과와 acceptance 불일치

- 사용자가 #31을 Todo로 되돌린 뒤 run `14f312e37d65`가 claim됐다. Ledger 이력은 `claimed → preparing → running → finalizing → waiting`; Gateway researcher session `local-1e2b93ae62fa8029351aa1ccd26e0f70b56783cd7d2dad3d8ee3492ec02f873f`는 최종 assistant 응답까지 도달했다. 138초 terminal probe는 exit 0이었다. Pod 재시작 없이 약 7분간 실행됐다.
- Langfuse Observations API v2에서 같은 session ID의 trace `2cc7f2fdc6a67cf28481b1b706a3f455`를 확인했다: Hermes turn 1, generation 14, tool 24, 총 observation 39. 따라서 Langfuse 추적도 이 실행에 대해 연결된다.
- 워커의 최종 outcome은 `review`이며 run ledger는 `waiting`이다. researcher 세션에는 Kubernetes 접근이 없어 작업이 배포 여부를 확인하지 못했다고 판단하고 human-action #33/#34를 만들었다. #31은 현재 `todo (waits on #33, #34)`, #29는 `todo (waits on #31)`, #32는 backlog 제안이다.
- Codex가 별도로 실제 클러스터를 읽어 #33 핵심 조건을 검증했다: Argo revision `69ee20d142db54996d1123663cde61c6a579079f`, live worker ConfigMap `kanzo-agent-worker-code-67th8f5m6b`, Dispatcher의 `/opt/kanzo-agent/worker.py`에 `_heartbeat_sandbox` 정의와 호출이 존재한다. Hermes `gateway_state.json.code_sha=3b421578...`는 Hermes 바이너리의 SHA이며 GitOps revision `711a1fe`와 비교하는 #31/#34 조건은 서로 다른 artifact identity를 혼동한다.
- 따라서 워커 실행/heartbeat 생존은 검증됐지만 #31 업무는 자동 완료되지 않았다. 사람 게이트 #33/#34를 확인하고 stale acceptance를 수정/완료해야 #31과 선행 작업 #29가 진행 가능하다. 이 실행에서는 새 중복 분석이나 소스 변경을 하지 않았다.

### 2026-11-15 — #31 gates resolved and #29 canary accepted

- With user authorization, Codex updated #31 to distinguish the GitOps Worker ConfigMap from Hermes binary code_sha, recorded live deployment evidence, completed #33 and #34, and marked #31 done. The Dispatcher ConfigMap and heartbeat method/call sites were verified directly in the cluster.
- Dependency release automatically started #29 as run `aa7d46eea6c0`. It used researcher / iq_3s via openai-api, completed a read-only turn, passed a 45-second in-run wait without interruption, and published its result comment. Langfuse trace `9f7bf9ddae7f66a82b28b7b1a6dae622` has 17 observations: 7 generations, 9 tools, and 1 Hermes turn. The worker routes read-only runs to review/human=true; Codex reviewed the evidence and marked #29 done.
- The resulting backlog proposal #35 was declined and archived as done with the `declined` tag: default and researcher Hermes configs already specify model.context_length=65536, so repeating that setting would not fix the metadata autodetection warning. #32 remains in backlog for later review because ambiguous submissions still merit investigation.
- Current Kanzo states: #29, #31, #33, #34 done; #35 declined/done; #32 backlog. No active run remains for these tasks.

## 2026-11-15 telemetry canary and #31 follow-up

- Kanzo `hermes-harness-lab#31` is already `done` with human-action ownership; its blocked cards `#33`/`#34` and dependent read-only canary `#29` are also `done`. No rerun was needed after the user updated the card.
- Fixed Langfuse attribution to read `KANZO_TASK_ID` and `KANZO_RUN_ID` from Hermes `agent.session_client_env.bound_client_env()`, and configured the two non-secret names in all four profile allowlists. The boot-time patcher tolerates existing helper variants and fails closed on unknown code.
- Added `kanzo_worker_phase_duration_seconds` histograms from durable run-ledger state transitions. Labels are bounded to lifecycle phase and outcome.
- Read-only canary `hermes-harness-lab#37`, run `f239fceaf654`, session `local-043d8ac7101e8d074dec99805e91ce8b71b9aee8786471a78d242a1808a19e0d`, trace `c68e83bdc95fa51675b53ffd53201e34`: direct Langfuse v2 observation query returned 27 observations (10 generations, 16 tools, 1 root `CHAIN`), all carrying matching `kanban_task_id=hermes-harness-lab#37` and `kanban_run_id=f239fceaf654`.
- Dispatcher metrics showed one completed sample each for `claimed`, `preparing`, `running`, and `finalizing`; Prometheus independently reported `up{job="kanzo-agent-worker"}=1` and `kanzo_worker_phase_duration_seconds_count{phase="running",outcome="review"}=1`.
- The worker's sandbox could not access Prometheus or the worker service directly. This is expected network isolation, not a scrape failure. Its generated duplicate proposal `#38` was marked `done` with `declined` and a short reason after independent verification.
- Current Kanzo states: #37 is `review`/human=true as expected for a read-only run; #38 is `done` with `declined`; #31/#29/#33/#34 remain `done`; #32 remains in backlog for later review. No active run remains.
- GitOps commits `8958075` and `f179516` are pushed. Argo Application `kanzo-agent-worker` is Synced/Healthy at `f179516e5331d7fef17a5142cf0098a7754338c6`; the executor and dispatcher are Ready. The first rollout exposed a patcher compatibility mismatch, which was corrected before the final healthy rollout.
- Targeted checks: 10 Langfuse context and worker phase-metric tests passed; Python compilation, `git diff --check`, and Kustomize rendering passed.

## 2026-11-15 Phase 6 findings: canary cost and ambiguous outcomes

- #37 is a **mis-scoped verification canary, not a normal one-turn latency baseline**: its prompt simultaneously forbade tool use and asked Hermes to verify Ledger, Langfuse, and Prometheus. Hermes spent 131.531 seconds in the running phase; Langfuse counted 10 generations, 16 tool observations, about 152,836 cumulative approximate input tokens, and 126.903 seconds summed API duration. It attempted cluster telemetry discovery from the isolated sandbox. The telemetry was verified externally by Codex. This measures the cost of the contradictory task shape and broad toolset, not steady-state Kanzo task performance.
- The next efficiency improvement should separate agent work from operator verification and provide an explicit task-scoped execution policy for genuinely bounded/no-tool work, while preserving normal Hermes memory, skill, delegation, and review behavior for ordinary tasks. Do not open sandbox network access just to let an agent query Prometheus.
- Executor DB `ambiguous` rows were compared with the authoritative Dispatcher run ledger. #31 run `90ca4cd1f3c2` has no durable result and `running → orphaned → review`, consistent with the Dispatcher rollout at 03:20Z; the later drain/1800-second grace addresses that path. #29 run `70e4105000c1` has a durable result and an `AttributeError` for missing `_heartbeat_sandbox`; the worker heartbeat fix addresses that path. Thus `submit_state=ambiguous` combines materially different outcomes and does not by itself prove a lost result. Added this evidence to backlog #32 without changing its human-reviewed `backlog` state or retrying either run.

- Explicit `tool-free` Kanzo tag now selects a response-only Hermes session: empty Gateway toolset policy, compact prompt, and ignored backlog markers. Untagged work retains all standard tools and memory/skills behavior. This does not disable the profile or Langfuse lifecycle.
- Rolled out GitOps commit `0d69a6a07440bd2dc026f53e1763f85c9e971d65`; Argo `kanzo-agent-worker` is Synced/Healthy, dispatcher and executor are Ready, and the worker deployment remains scaled to zero as configured for the resident-executor architecture. `py_compile`, `git diff --check`, and Kustomize rendering passed before rollout. A tagged end-to-end canary is still needed to measure token/time savings against #37; no new Kanzo card was created in this change.


### 2026-11-15 — #39 Langfuse 귀속 검증 통과와 API 조회 오류 정정

- GitOps commit `29cf385`는 Argo에서 Synced/Healthy이며 Dispatcher/Executor는 각각 1/1 Ready다. #39 run `ac261e85586d`는 기대한 응답으로 정상 종료했다. Executor DB는 `create_state=created`, `submit_state=completed`; researcher Gateway session DB의 `tool_call_count=0`이다.
- Executor ledger는 task `hermes-harness-lab#39`, run `ac261e85586d`, session `local-0c72db46ad431fe88db2266bbc864e9a1f1ad5cec3c3b00911ccb45fff71841b`를 연결한다. Langfuse trace `975c391d227442973fa4338d29136e94`의 session ID가 일치한다. Observations API v2를 `fields=core,basic,metadata`로 조회해 root `CHAIN`과 `GENERATION` 두 개 모두 `kanban_task_id=hermes-harness-lab#39`, `kanban_run_id=ac261e85586d`임을 확인했다. trace에는 `TOOL` observation이 없고 정확한 응답도 기록돼 있어 #39 acceptance를 통과 처리했다.
- `metadata=null`로 보였던 앞선 조회는 요청의 기본 field set에 metadata group을 포함하지 않은 결과였다. Langfuse API의 metadata field group을 추가해 다시 조회하자 값이 반환됐다. 따라서 이전에 적은 “전달 경계 추가 계측 필요” 판단은 철회한다. 공식 API 문서에 따르면 metadata 조회는 `fields=metadata`를 명시해야 한다.
- 과거 #37 trace `c68e83bdc95fa51675b53ffd53201e34`도 `fields=core,basic,metadata`로 조회했다. 27개 observation에서 task `hermes-harness-lab#37`, run `f239fceaf654`가 확인돼 기존 상관관계 기록과 일치했다.
- `29cf385`의 Kanzo 전용 프로세스 fallback은 배포돼 있다. 현재 #37/#39 trace만으로 이 fallback 경로 자체가 호출됐다고 증명할 수는 없으며, session-scoped lookup이 정상 동작한다는 것을 증명한다. fallback 유지 여부는 실제 필요성을 확인한 뒤 판단한다.
- #39는 `done`; #31과 #29도 기존 acceptance에 따라 `done`이다. #32와 #40은 backlog 검토 대기다. 현재 hermes-harness-lab에 실행 가능한 Todo가 없다.
- 다음은 roadmap Phase 6의 대조 가능한 성공 작업 표본과 보존·복구 시나리오다. #37을 일반 성능 baseline으로 재사용하지 않는다.
## 2026-11-15 — Phase 4 report audit

- Kanzo `hermes-harness-lab#21` run `a21ea5e26930` reached a final Hermes response at 08:26 UTC (20 API calls, 18,085 response characters; Executor ledger `submit_state=completed`). The Kanzo card is Done after Codex recorded the verified corrections and acceptance; no profile, skill, MCP or cluster settings changed.
- Independent live checks found two stale report facts: installed Hermes is v0.23.1, not v0.17.0; and the current shared 18-entry `platform_toolsets.cli` list is `clarify, code_execution, connections, cronjob, delegation, file, image_gen, kanban, memory, omh, omh-lsp, session_search, skills, terminal, todo, tts, vision, web`. The report had incorrectly substituted disabled `browser`/`computer_use` and an unavailable `search` entry.
- Fresh default CLI `hermes prompt-size --json` baseline: 191 skill index entries / 15,892 bytes; 30 tool schemas / 56,908 bytes. This is a CLI/default snapshot, not a profile-specific Gateway measurement; it supersedes the earlier 189/192 counts for this current baseline.
- Backlog proposals #42–#46 remain in Backlog. #43’s toolset evidence and #45’s skill-index baseline were corrected. Hermes Kanban shows #17-A run 13 as Running, but its last event is worker exit_code 130 and the recorded PID is absent from current processes; no terminal completion event exists. The worker Deployment is scaled to zero, #17-C remains Todo, and Kanzo parent #17 is Review. Evidence was added to backlog #46; no reclaim, cancellation, or retry was performed.
- Phase 4 is accepted with the corrections recorded on #21. The outstanding #17 Hermes Kanban state remains a separate follow-up tracked by backlog #46. Phase 6 inspection found the worker duration histogram has only kind/outcome labels, but `kanzo_worker_active_task_info` carries task_id/run_id while a run is active. Use that gauge plus the run ledger to find the run window without adding IDs to histograms. A #37 window query measured ~525 MiB gateway working set and 7.87 CPU-seconds; these are shared-container observations, not isolated run consumption. #47 was updated with this method and remains Backlog; no Todo task was promoted. Do not treat #21 as a performance baseline. Obsolete image-based proposal #30 was archived as declined after #31/#29 verified the ConfigMap fix and canary.
## 2026-11-15 — Phase 6 historical canary sample

- Read-only Grafana queries matched logged session windows to executor pod `kanzo-agent-executor-76f997cfc6-rh5q4`; Prometheus itself exposes only low-cardinality `kind`/`outcome` histogram labels, not run IDs.
- #31 run `14f312e37d65` (researcher/iq_3s): session 05:21:42–05:28:21 UTC, 138.03s terminal probe, 14 API calls. Window peak gateway CPU ~0.564 cores and gateway working set ~1.306 GB; whole-pod CPU ~0.606 cores and working set ~1.725 GB.
- #29 run `aa7d46eea6c0` (researcher/iq_3s): session 05:50:39–05:52:35 UTC, 45.95s terminal probe, 7 API calls. Window peak gateway CPU ~0.130 cores and gateway working set ~1.339 GB; whole-pod CPU ~0.130 cores and working set ~1.803 GB.
- These timestamp-correlated pod peaks are approximate and include the full session window; the probe scopes differ, so they are preliminary observations rather than a comparable baseline. Added them to backlog #47; #47 remains Backlog and no task was promoted.

## 2026-11-15 #47 execution and #50 preflight

- Kanzo still reports #31 as `done` (last updated 05:50 UTC); the user's reported change is not visible in the current Kanzo task response. #47 is `doing`, run `db69643f27a1`. Prometheus at 09:08:15 UTC returned `kanzo_worker_active_task_info=1` for #47 on dispatcher `kanzo-agent-dispatcher-5ff68b9f77-j5wkf`. Do not restart it or launch #50 until it ends.
- At 09:14 UTC the same run remained active; `litellm_in_flight_requests=1` globally and the executor gateway CPU rate was ~0.033 cores over 5 minutes, with ~655 MB working set. The in-flight metric has no task/run labels, so this is correlated with the sole active Kanzo run rather than direct attribution. It suggests the worker is waiting on a model response, not that the task has completed; do not stop it.
- Static verification against the Hermes v0.23.1 upstream tag found a real preflight gap for #50: `gateway_session.py` binds `KANZO_AUTOMATED_RUN`, `KANZO_TASK_ID`, and `KANZO_RUN_ID` into `session.create`'s `client_env`; however, `gateway.session_context.get_session_env()` only checks its registered `HERMES_SESSION_*` ContextVars and then process `os.environ`. The `kanzo_terminal_provider.py` currently reads Kanzo identity through that function. Because those Kanzo values are per-session `client_env` values rather than gateway process variables, the broker provider may fail closed before the sandbox is reached. No local filesystem fallback is granted by this failure.
- Recorded this caveat on #50: a provider identity error is not evidence that `/workspace` write-denial worked; stop and report the tool-path failure. Read-only Hermes `agent.session_client_env.bound_client_env()` exposes the session-scoped map and is a likely safe source for the provider, but any code change/rollout must wait until #47 is terminal and must retain fail-closed checks.
- This Codex shell has no current Kubernetes context (`kubectl config current-context` is unset), so direct pod/ledger inspection and rollout are unavailable from this environment. Kanzo and Grafana MCP remain reachable. No source, cluster, or task status changes were made; only the #50 evidence comment was added.

## 2026-11-15 #47/#50 canary results — supersedes preflight expectation

- #47 run `db69643f27a1` ended at 09:18 UTC and is in `review`, not active. Its final outcome was blocked by `KANZO_HUMAN_ACTION requires a title of 1 to 180 characters`; the requested 3-run Phase 6 baseline is not complete. #50 then started after #47's executor ledger showed `submit_state=completed`.
- #50 run `2ae7dfb560d5` ended at 09:27 UTC and is in `review`. Runtime evidence disproved the earlier fail-closed expectation: Gateway `HERMES_SESSION_SOURCE` and `KANZO_AUTOMATED_RUN` were empty, so `kanzo_terminal_provider` selected Hermes' local terminal fallback. The terminal wrote and read back a marker under `/workspace` in executor pod `kanzo-agent-executor-549f75ffd5-c8jf9`; `read_file` and `execute_code` observed the same filesystem. The canary stopped further writes immediately after the first unexpected success, per acceptance.
- `/scratch` did not exist and could not be created because the reachable root filesystem was read-only. `/workspace` had a read-write ext4 mount. The workspace was empty and had no Git metadata, so no checkout SHA could be recorded; the marker was removed and cleanup was confirmed. No persistent Hermes home, memory, skill, or deployment was changed.
- This is a confirmed automated-session broker-routing/isolation defect: the `read_only` flag does not currently guarantee broker sandbox execution. Do not use read-only Kanzo runs for untrusted repository content until fixed. Backlog #51 tracks routing automated sessions to the broker sandbox; #52 tracks recording the resolved backend so future fallback is visible. #47 and #50 are both in Review, and no active run was reported at 09:47 UTC.
- Corrective implementation and rollout are still pending. Current Windows and WSL environments have no kubeconfig/current context; Kanzo and Grafana MCP can read board/metrics but cannot provide direct `kubectl` pod operations or perform/verify the rollout here.

## 2026-11-15 K3s access correction

- Follow-up confirmed the cluster is directly accessible from WSL with `wsl.exe -u root -- k3s kubectl`; the K3s server's embedded admin kubeconfig is present but unreadable by the default WSL user. No separate user kubeconfig is required when invoking the installed `k3s kubectl` as root.
- Live check: node `home-server-wsl` Ready on K3s `v1.37.2+k3s1`; executor, dispatcher and sandbox broker each 1/1 Ready. Argo `kanzo-agent-worker` is Synced/Healthy at `a5096ecb4c8dafd68c58cb024e41841de26d3741`. The earlier statement that rollout could not be verified due to missing kubeconfig was too broad and is superseded.

## 2026-11-15 sandbox execute_code follow-up

- K3s management is available without a separate kubeconfig through `wsl.exe -u root -- k3s kubectl`. The node is Ready; Argo applications `kanzo-agent-worker` and `kanzo-sandbox` are Synced/Healthy at `04a1299449a70d8d38b45d13f1ac61e0af1e0cda`; executor, dispatcher, and broker Deployments are 1/1 available.
- `6455f7a` changes the broker's advertised `temp_dir` from `/tmp` to the sandbox's writable `/scratch`, which is an accepted execute cwd. The broker rollout alone did not clear the `execute_code` HTTP 400.
- `20adf40` and `04a1299` make Hermes retain only a fixed, validated broker error code in its logs and reject a missing temp-dir mapping instead of silently defaulting it. These changes are deployed. No response body, task content, or credential is logged.
- Canary `hermes-harness-lab#56` run `60ef79967673` created a separate sandbox pod, then `execute_code` still returned `{"status":"error","error":"broker returned HTTP 400","tool_calls_made":0,"duration_seconds":0.01}`. The snippet did not run; no marker or workspace change occurred; the run pod was cleaned up. The task is Todo waiting on human-action card #57. Its acceptance requires a broker `execute_code` call to write/read/delete a marker in `/scratch` and confirm no `/workspace` write.
- Therefore the broker terminal is available for a basic terminal probe, but `execute_code` remains unverified and broken. The exact 400 code is not yet observed through the newly deployed diagnostic; do not mark sandbox-tool isolation complete or run untrusted repository tasks.

## 2026-11-15 execute_code root-cwd and kernel findings

- Source inspection identified the first HTTP 400 cause: Hermes `code_execution_rpc._execute_checked()` and `code_kernel_remote._sh()` issue terminal commands with `cwd="/"`, while the Kanzo runner only accepts `/workspace` or `/scratch`. Commit `02825c0` maps root-cwd calls to the broker-provided `/scratch` and validates the expected `/workspace` + `/scratch` mapping. Argo deployed it at `02825c0df49b36ca7ed16bb9e3a763e2fa324ff3`; all four persistent profile plugin copies match the mounted provider source hash.
- After #57 was completed, #56 resumed once as designed (run `23bdb041c88a`). The initial HTTP 400 was cleared. Runtime then showed Hermes' remote session kernel cannot persist on this runner: `sandbox_runner` terminates the entire process group after each command; the detached kernel became a zombie and left `cell_req_000001.json` without a result. `execute_code` returned a 300-second cell timeout, so the `/scratch` marker was never verified. No second code-cell attempt occurred.
- Commit `1ba52f0` deployed a broker-only bypass for Hermes' detached remote session kernel. Canary #60 confirmed the per-call sandbox path succeeds; a follow-up source review found the bypass wrapper's fixed first positional parameter caused a caught `TypeError`, after which Hermes fell back to the per-call path. The canary passed, but the wrapper has now been corrected to preserve Hermes' actual call signature (`code` positional, `env` keyword) and still return `None` only for `kanzo_broker`.

## 2026-11-15 one-shot execute_code canary and wrapper correction

- Kanzo canary `hermes-harness-lab#60`, run `f1d1441bb2a5`, completed in Review at 11:20 UTC. It issued exactly one `execute_code` probe: `os.getcwd()` returned `/scratch/hermes_exec_6ba7092ae91a`; a unique marker under `/scratch` was written, read byte-for-byte, removed, and absence confirmed. Exit code 0, 0.98 seconds, 0 nested calls. No `/workspace` writes or persistent remote kernel were used.
- The same run's executor log showed `TypeError: ... _execute_in_remote_kernel() got multiple values for argument 'env'`; Hermes caught it and fell back to the per-call route. Thus #60 proves the sandboxed per-call `execute_code` route works, while the intended clean kernel bypass still needed the source correction made immediately after this canary.
- The correction changes only the wrapper signature to accept `*args, **kwargs`, extracts `env` by keyword, returns `None` for the `kanzo_broker` backend, and forwards the original call shape unchanged for other backends. Commit `c7c9491` is deployed and Argo is Synced/Healthy at `c7c9491f245c8140d92b5bd669851e6348094d91`.
- Live SHA-256 matched across `/opt/kanzo-agent/kanzo_terminal_provider.py` and the default, researcher, builder and reviewer plugin copies: `77e456220103e97cf44d3f436b636d25c9e32a6e8ee08caaaa9c82c67017eaba`.
- Canary #61, run `69e5943e8ace`, completed its single `execute_code` call successfully in 0.98s with exit code 0. It printed `/scratch/hermes_exec_d1f5c5936585`, wrote/read/removed `/scratch/khl61-canary-601f774a1821.txt`, and confirmed absence. Executor logs for the run window contained neither `_execute_in_remote_kernel` TypeError nor session-kernel fallback. This verifies the corrected direct per-call broker path.
- The ephemeral `kanzo-sandboxes` run pod was cleaned up; dispatcher/executor remained ready; no `doing` task was active after the canary. #61 remains in Review (`human=true`) for operator acceptance. The worker proposed #62 to expose the execution path in tool results; it remains Backlog and was not promoted.
- This closes the execute_code cwd/kernel startup blocker for the one-shot `/scratch` path. It does not yet prove the complete read-only tool boundary: terminal, read_file and write_file must be verified against the same leased workspace, especially that a write to read-only `/workspace` fails while scratch writes succeed. The other live backlog areas include run outcome reconciliation (#48), Langfuse generation/delegation trace verification (#49), resolved backend observability (#52), sandbox task/run identity (#55), stale Hermes board reconciliation (#46), profile-specific toolsets and MCP annotation evidence (#43/#44), and the comparable three-run resource baseline (#47). No such backlog item was promoted as part of this canary.

### 2026-11-15 — Langfuse reconciliation deployed and canary verified

- Commit `fad42c7` adds read-only Langfuse Observation API reconciliation to the dispatcher ledger. Every terminal outcome (success, review, waiting, failure, cancellation, or restart-orphan) gets an initial check. `not_found`, `root_missing`, `incomplete`, and transient API errors schedule up to five persisted rechecks over about 18.5 minutes; this never reruns Hermes or changes the Kanzo outcome. Pending checks survive dispatcher restart.
- The Vault secret rotation target now includes the dispatcher. Dispatcher egress to Langfuse is restricted to the `langfuse-web` pod. The in-cluster Langfuse service remains HTTP: the cluster Tailscale HTTPS endpoint refused connections from pods, so this change does not claim transport encryption. Credentials remain limited to the dispatcher for reconciliation.
- Kustomize build, Python syntax compilation, and `git diff --check` passed. Argo `kanzo-agent-worker` is Synced/Healthy at `fad42c7096feadbc2d51a8ab41822f61f8d89a58`; dispatcher is 2/2 Ready. No active run existed before rollout.
- Canary `hermes-harness-lab#73`, run `a45e18e2124d`, produced Langfuse trace `79bd7348ecc333a027ec888ffcc20cb3`. First reconciliation at turn close found `root_missing`; the persisted retry 31 seconds later recorded `ended`, 25 observations, one trace, and root end time `2026-11-15T12:33:13.081Z`. The canary was operator-verified and moved to Done.
- Phase 5 remains open: generation duplication evidence/guard and delegate parent-child trace correlation still need implementation or explicit proof they are already handled. Phase 6 remains open: comparable resource/performance baseline, memory/skill reuse across runs and restarts, ReadOnly profile learning preservation, and lifecycle/cancellation scenarios still need evidence. Keep the roadmap as the design SSOT and this file as execution status.

### 2026-11-15 — Phase 5 trace behavior verified end to end

- The earlier Phase 5 “generation duplication / delegate linking” open note is resolved by live evidence and source inspection. It required no additional tracing writer or trace mutation.
- Canary `#73` / run `a45e18e2124d`: one Langfuse trace, 25 observations, 10 `GENERATION`, 14 `TOOL`, one `CHAIN`; all 25 observation IDs were unique and there were no duplicate IDs. The Langfuse plugin's live source also has an explicit built-in-plugin guard (`_core_ships_langfuse`) and replaces a stale open generation when the same API request is retried.
- Canary `#75` / run `df2a02fa31ef`: one trace, two generation observations, and one `Subagent: leaf` span. The span's `parentObservationId` points to the parent `Hermes turn`; metadata includes the child session/subagent IDs, completed status, duration, and matching Kanzo task/run IDs. The trace root ended and the ledger reconciled on attempt 2.
- Both canaries are operator-verified and Done. This closes Phase 5 (task/session attribution, single-writer trace behavior, bounded terminal reconciliation, and parent-child subagent linkage). No separate LiteLLM trace writer was enabled.
- Remaining roadmap work is Phase 6: establish comparable run/token/latency/resource baselines; verify memory and skill reuse across runs and restarts; audit ReadOnly profile learning preservation; and exercise cancellation, restart, concurrency, and long-running lifecycle cases. Do not mark overall roadmap complete until those checks and any necessary fixes are complete.

### 2026-11-15 — Phase 6 profile durability and initial resource sample

- ReadOnly profile audit: the mounted Hermes home and its `memories/` directory persist on the shared PVC, but `sync_profile()` previously deleted and recopied each ReadOnly `skills`, `plugins`, `scripts`, and `.omh` tree on every run. That could discard ReadOnly-local skill edits. Commit `3e6f407` replaces that destructive copy with a source-hash three-way sync. Unchanged local copies receive source updates; local-only additions and locally modified files survive; source deletions remove only files that still match the last synced source hash; conflicts are logged and preserve local data. `MEMORY.md` is included in that policy. Profile config and user settings continue to follow the main profile. Python syntax and Kustomize build passed; Argo is Synced/Healthy at `3e6f4073d5ab46e36909790ef010d62d8b3d1726`, dispatcher/executor are 1/1, and the ledger has no active run. Functional persistence across successive ReadOnly launches still needs a canary.
- Grafana/Prometheus sample across canaries #73 and #75 (12:31–12:39 UTC) is component-only, not yet a comparable baseline: Hermes gateway peaked around 0.103 CPU cores and 555 MB working set; dashboard around 0.011 cores / 379 MB; executor API around 0.044 cores / 60 MB; dispatcher worker around 0.012 cores / 26 MB. These short runs used different prompts, so the values cannot be compared as workload performance. The query found no GPU/DCGM/NVIDIA metric series in Prometheus; it did not observe GPU or the inference host.
- Still needed for Phase 6: three matched runs with task/model/prompt fixed and Langfuse tokens/latency plus all relevant pod resources captured; a ReadOnly skill+memory persistence canary across a second launch and executor restart; and focused lifecycle evidence for cancellation, child-process cleanup, concurrent claims, and long-running/restart handoff. Roadmap is not complete.
- Cluster access note: all checks and the rollout used `wsl.exe -u root -- k3s kubectl`; no standalone kubeconfig was needed.

### 2026-11-15 — Matched baseline and post-restart memory canary

- Matched tool-free canaries #76–#78 ran sequentially on the same active catalog route (researcher / `openai-api` / `iq_3s` / high), each returning the required marker with one generation and zero tool observations. Langfuse estimated 3,220 input tokens per run and reported API durations 6.89s, 5.36s, and 5.93s (mean 6.06s). Each trace reached `ended` on reconciliation attempt 2. They are operator-verified and Done.
- Prometheus peak values over their shared 12:48–12:49 UTC window: Hermes gateway 0.089 CPU cores / 498 MiB working set; Hermes dashboard 0.0085 cores / 339 MiB; executor API 0.052 cores / 58 MiB; dispatcher worker 0.0066 cores / 29 MiB. These are component-window maxima, not per-run peaks. No GPU/DCGM/NVIDIA metric series exists in the configured Prometheus datasource. The inference host/GPU is outside this measurement.
- Canary #79 ran after the Executor restart and successfully retrieved the previously saved Hermes memory note about broker sandbox network and mount boundaries. Trace `95c92302a32a4aa0b8ef57a22361e55a` ended with 8 observations. This confirms Hermes memory was available after restart. It also surfaced a conflicting stale OMH recall record (`mem_27310135`) about `write_file` refusing `/scratch`; the verified post-#70 behavior is `/workspace` denied by its read-only mount and `/scratch` writable. The agent proposed #80 to reconcile it; #80 remains Backlog, and #79 remains Review because the agent created that proposal despite the canary instruction not to.
- Architecture clarification: current broker-backed tasks execute through `gateway_execution.py` using profile homes directly under the persistent Hermes PVC. `readonly_executor.py::sync_profile()` is the separate legacy worker Deployment path, currently at zero replicas; commit `3e6f407` makes that path preserve local learning, but it is not the code path exercised by the current dispatcher. The active memory retrieval canary validates the persistent profile path after restart.
- Remaining Phase 6: measure end-to-end run pods and inference-host/GPU resources (the current query only covered persistent dispatcher/executor containers); verify skill updates survive a ReadOnly launch/restart; compare memory/skills behavior over more than one executor restart; and finish cancellation/process-group, concurrent-claim, and long-running handoff evidence. Overall roadmap remains open.

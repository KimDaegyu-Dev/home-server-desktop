# Kanzo and Hermes detailed evidence archive

This file preserves the dated implementation notes and operational evidence formerly kept in kanzo-hermes-status.md. Use kanzo-hermes-status.md for the current snapshot; use kanzo-hermes-roadmap.md as the design SSOT.

---

## 아카이브 읽기 안내

이 파일의 날짜별 기록은 당시의 코드·클러스터·카드 상태를 보존한다. 이후 변경으로 대체된 커밋·상태·미검증 항목을 현재 상태로 해석하지 않는다. 현재 배포와 남은 작업은 [Kanzo / Hermes 진행 상태](kanzo-hermes-status.md), 목표와 acceptance는 [로드맵 SSOT](kanzo-hermes-roadmap.md)를 기준으로 한다.

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

### 2026-11-15 — Phase 6 ephemeral runner resource attribution

- Matched runs #76–#78 each created a distinct `kanzo-run-*` sandbox pod. Prometheus `container_memory_working_set_bytes` samples captured during each pod lifetime peaked at 13.3 MiB (`kanzo-run-4f4dda163ab62459a1b8`, #76), 13.2 MiB (`kanzo-run-f15a712aa7681c695994`, #77), and 13.9 MiB (`kanzo-run-ab0ca695351b4b196627`, #78). These are runner pod measurements; they do not include the Hermes Gateway or external inference host.
- Each runner lived for less than the 15-second Prometheus scrape interval, so only one raw CPU counter sample was available per pod. Per-run CPU consumption cannot be derived reliably from these samples. The component-window CPU figures above remain shared-window maxima, not per-run attribution.
- The host was observed once after the canaries at 0% GPU utilization with about 15.7/16.4 GiB VRAM allocated. This is an instantaneous post-run observation, not historical canary telemetry. Prometheus has no GPU exporter series, so GPU demand during #76–#78 remains unknown.
- Lifecycle source audit found a concrete shutdown mismatch: task deadlines are 2,700 seconds while the Dispatcher pod termination grace is 1,800 seconds; its SIGTERM handler drains the active task instead of cancelling it. A rollout during a long task can therefore be forcibly terminated before the task deadline. Executor pod grace is 60 seconds. This is a code/config risk, not yet a reproduced failure; graceful and forced shutdown need isolated canaries before changing the protocol.
- The active deployment is singleton and processes tasks sequentially. Its claim safety depends on `replicas: 1`; ledger admission occurs after Kanzo claim, so two simultaneous dispatchers could claim competing tasks before one is rejected. Multi-replica safety is not established. Executor API restart also marks ambiguous submissions to prevent duplicate LLM calls, but source audit did not find proof that a still-running Gateway turn is found and cancelled after an API crash.
- Architecture audit confirms `3e6f407` protects the inactive legacy ReadOnly worker path only. Active broker-backed work executes in persistent Hermes profile homes; `read_only: true` protects the repository sandbox and does not prevent profile memory/skill writes. The post-restart #79 canary proved memory recall, but skill-learning preservation across a ReadOnly run and Executor restart remains unverified.
- ReadOnly isolation audit: normal broker-backed terminal, file, and `execute_code` paths are wired to the run lease, and dispatcher configuration rejects the no-lease legacy route. However, a delegated child session has a different Hermes session ID while the broker authorizes one bound session ID; no inheritance/registration path or fail-closed local-fallback evidence was found. Existing #60/#61/#70/#72 canaries cover individual tools, not delegate descendants. Phase 6 isolation scenario 14 therefore remains open pending a delegated ReadOnly canary that records task/run/lease identity and proves both permitted scratch access and denied repository writes without local fallback.
- Lifecycle review also found unresolved recovery edges: an Executor API restart prevents duplicate submission by marking `creating/submitting` requests ambiguous, but the audit did not establish that an already-running Gateway turn is found and cancelled after a crash; `/healthz` can remain healthy for a stale active run; and claim safety currently relies on a single dispatcher because the ledger rejects a second run only after Kanzo has returned a claimed task. These require separate restart, stuck-heartbeat, and duplicate-event/reconnect evidence before the lifecycle phase can close.

### 2026-11-15 — Dispatcher shutdown grace aligned with task deadline

- Changed `kanzo-agent-dispatcher` termination grace from 1,800s to 3,300s. This exceeds the 2,700s Hermes turn deadline by 600s for result publication and ledger finalization, avoiding a rollout forcibly terminating a task that is still within its configured deadline.
- Rendered the Kustomize directory with `k3s kubectl kustomize`, committed as `0153b37`, and pushed to Git. Argo's state-migration hook completed successfully; application `kanzo-agent-worker` is `Synced/Healthy` at `0153b37e00da769520573d8e2303a28ca015ae25`, Dispatcher is 1/1, and the live Deployment reports `terminationGracePeriodSeconds: 3300`.
- This fixes the configuration mismatch but does not prove graceful termination under a long active task. A controlled lifecycle canary is still needed; Executor API crash recovery, Gateway turn reconciliation, stale-heartbeat health, claim/reconnect idempotency, cancellation process cleanup, and inference-host GPU telemetry also remain open.

### 2026-11-15 — ReadOnly delegate sandbox canary #81

- Run `aa8daa5da406` used one delegate child. Dispatcher ledger follow-up completed Langfuse reconciliation on attempt 2: trace `bf0e87ef6c426d88999625b9d9544e37`, 17 observations, root ended, no further retry scheduled. The first `root_missing` was ingestion delay; no task/LLM rerun was issued. Task #81 was operator-verified and moved from Review to Done.
- Child terminal returned cwd `/workspace` and hostname `kanzo-run-278afcc3a2ce36c3ebaa`, matching the parent terminal hostname. Child `execute_code` succeeded in a per-session `/scratch/hermes_exec_*` cwd; the parent-side `execute_code` probe reported the same run hostname. This supports broker-local execution with no observed host fallback. `/workspace` was independently observed `ro`, `/scratch` `rw`.
- The child's only `/workspace` `write_file` call was denied with `HERMES_WRITE_SAFE_ROOT=/opt/data`, zero bytes written, and no directory created. Parent checks found no marker. The denial occurred before the broker mount, so the canary proves fail-closed behavior but does not prove the delegated file tool routes to the broker or that the mount itself rejected that file call. ReadOnly delegation boundary is therefore only partially evidenced; keep Phase 6 scenario 14 open.
- The task also confirmed `terminal` and `execute_code` use different working directories: `/workspace` versus a session-specific `/scratch/hermes_exec_*`. Future checks must inspect the resolved backend identity and avoid assuming shared cwd. OMH record `mem_27310135` was not edited; user-controlled backlog #80 remains untouched.

### 2026-11-15 — Executor restart reconciliation deployed and verified

- `36ca7f0` persists a stable Gateway `session.create` request ID and its frozen create parameters before calling the Gateway. An interrupted create can retry that exact idempotent request; prompt submission is never replayed.
- `9748984` reconciles a completed admission by querying its exact `prompt.receipt` when `session.resume` no longer lists it under `pending`. It verifies session, admission, terminal state, and execution generation before changing the ledger.
- The first live rollout exposed a startup-order gap: the Executor API attempted recovery before the Gateway container was ready. Two old rows (`hermes-harness-lab#29` and `#31`) remained ambiguous even though their exact Gateway receipts were already terminal/interrupted. `813b25c` moves recovery to a background reconciliation loop, so the API becomes available immediately and retries safely after Gateway startup.
- After the second rollout, both exact receipts were reconciled to `cancelled`; the Executor DB now has 0 ambiguous rows (30 completed, 2 cancelled). The helper confirmed both receipts were `terminal/interrupted`, generation 1, before the ledger update. No prompt was resubmitted and no live model response was stopped during this verification.
- Argo CD is `Synced/Healthy` at `813b25c167ef2f8293a9b84534b1f54a1d95f842`; Executor is 3/3 and Dispatcher is 2/2. There were no live Gateway sessions immediately before the rollouts. Kustomize render and `git diff --check` passed; no test suite or crash fault-injection was run.
- Remaining lifecycle evidence: a controlled crash/restart case while a turn is active is still unverified; it must prove exact cancellation and terminal reconciliation without replaying the prompt. Rows without an admission ID remain human-reviewable, and an active turn without an execution generation cannot be interrupted automatically.


### 2026-11-16 — Restart recovery follow-up, canary cleanup, and GPU telemetry

- ReadOnly canary #82 created the researcher memory marker and temporary skill; both survived an Executor restart. The memory store was already 2,076/2,200 characters, so the agent shortened two unrelated Prometheus/Langfuse phrases to fit its 166-character marker, despite the task explicitly forbidding unrelated edits, and created backlog #83. I restored both exact original phrases, removed the temporary marker and skill, and left #83 in Backlog for the user's review. #82 remains in Review. This proves on-disk profile persistence across one restart, not actual retrieval by a second task.
- A parallel code review found a restart gap: rows already in `admitted` were not changed to `ambiguous`, so a Gateway-accepted turn could escape recovery. The pending patch now reconciles both `submitting` and `admitted`, requires an explicit matching admission ID in terminal receipts, and checks ambiguous rows every 60 seconds rather than every 15 seconds. These code changes are local only and await a safe Executor rollout.
- Repeat Argo syncs recreated the state-migration hook and always slept 65 seconds even though `.kanzo-dispatcher-state-migrated-v1` already existed. The pending manifest keeps the original 65-second wait for first migration and skips it on later syncs; the migration script still validates the source and marked destination. This removes up to 65 seconds of avoidable wait per repeat sync.
- GPU observability app was Synced/Healthy at `d59efec`. Live Prometheus confirmed `up{job="monitoring/wsl-gpu-metrics"}=1`, GPU utilization 100%, and `strata.exe` SM utilization 98% at 2026-11-16 02:18 UTC. Active Kanzo run `hermes-harness-lab#42` owns a live `kanzo-run` pod, so the shared Executor/Gateway rollout is deferred until it finishes. The GPU PodMonitor label fix did not restart Hermes.
- At 02:27 UTC, the suspected idle/orphan TUI state was checked against the live run. Kanzo runner `kanzo-run-a3c1b3db62821be15282` is Running; Executor ledger maps `hermes-harness-lab#42` / `eeb8fea88668` to Gateway session `local-5ece481fceb4d8f85f1c7ed602f6e1e9c4ea689e05cdc2079a3d8c9c257d17d5`, admission `b1e471b16228420d808fe6c5f440b289`, generation 1, state `admitted`. The live `gateway_execution.py` process uses that exact session, while WSL GPU utilization is 99–100% and VRAM is about 15.7/16.4 GiB. WSL shows the resident Gateway plus this one execution helper and no separate Hermes TUI client. The low runner CPU (1m) is expected because inference runs on the host GPU. This is active work, not evidence of an orphan TUI; do not terminate it or roll the shared Executor/Gateway while it remains active.
- At 02:34 UTC, #42's helper exited normally; the executor row is `completed`, the runner pod is gone, and GPU utilization is 0% (15.7 GiB remains resident). The task is now `todo (waits on #87)`, not done: the run found the bare-host URL in the user's comment also lacks a scheme, and created human-owned #87 to set a valid `SEARXNG_URL`. We left #87 untouched. `session.list(scope="live")` returned an empty list before and after deployment. After confirming zero active Gateway sessions and zero Executor rows in `submitting/admitted/ambiguous`, pushed `bca64ac`; Argo required a hard refresh, then synced and became Healthy at `4408a5b70e7596bfbfcb6a7c6c684e6ca3a7ba0f`. The state migration hook validated the existing migration marker and finished in 5s without the old 65s wait. New Executor is 3/3 and Dispatcher 2/2. The live Executor code contains the new `submitting`/`admitted` restart mapping, explicit admission ID receipt check, and 60s reconciliation interval; DB counts are 33 completed, 2 cancelled, and zero unfinished/ambiguous. This verifies deployment and recovery reconciliation, but not an active-turn crash fault injection or #42's SearXNG acceptance check.
- At 02:42 UTC, the delegated ReadOnly terminal canary #88 reached Review with run `d3befb6461c5`; its immutable run artifact's SHA-256 matches the manifest, and the Dispatcher ledger records the exact run as `review`. The recorded probe shows `/workspace` mounted `ro` and `/scratch` `rw` on the same device, a direct shell redirection under `/workspace` returning `Read-only file system` twice, no marker created, and a scratch write/read/remove succeeding. This closes the prior mount-level enforcement gap from #81 for a delegated terminal call. It did not prove that a child session ID can be distinguished in-run or that the parent can read the child's raw transcript; new backlog proposals #89 and #90 cover those observability gaps and remain for user review. The canary used `repo: none`, so it proves the sandbox workspace mount boundary rather than protection of a checked-out repository. Grafana historical metrics for the 4m32s run show runner memory peaking at 17,944,576 bytes (~17.1 MiB), runner CPU peaking at 0.0022 cores, WSL GPU utilization at 100%, and `strata.exe` SM utilization up to 99%. This supports the current finding that the sandbox runner is lightweight while local inference dominates GPU use; it is a single-run measurement, not a cold/warm performance baseline. No repository, memory, skill, profile, Kanzo configuration, or host state was modified.
- At 02:48 UTC, matched-output canary #91 (`1353114ad9e7`) returned the fixed token and reached Review. Independent ledger and Langfuse checks found one ended trace (`e8bf5a619e82acb6aa702f7e81088401`), one generation plus the root chain, zero tool observations, and 16.09s model-call latency. Grafana saw runner working set 13,819,904 bytes (~13.2 MiB); the CPU counter remained 0.054421s across six samples, so no short-run CPU peak/rate can be inferred. GPU samples reached 99% and `strata.exe` SM 98% at 02:48:15–20 UTC. The task added telemetry instructions to the original matched prompt, so this run is a GPU correlation check, not a directly comparable fourth latency sample. The worker omitted the requested trace/telemetry evidence; Codex added an independently verified Kanzo comment. This confirms GPU exporter correlation but still does not provide a statistically useful cold/warm baseline or short-run CPU attribution.

### 2026-11-16 — Capture per-run sandbox cgroup counters before cleanup

- Commits `30bfb22` and `8df0c07` add an authenticated runner endpoint and worker-to-broker path that records sandbox cgroup usage into the Dispatcher run ledger before the ephemeral Pod is deleted. Telemetry failure is best effort and cannot fail task completion. The ledger labels cumulative CPU time and memory peak separately; it does not claim instantaneous CPU peak.
- Canary #92 exposed an error-isolation bug: an unavailable memory counter could invalidate a successfully read CPU counter. Canary #93 still returned the old response shape, so it was not treated as evidence for the fix. Commit `4409ba7` added a collector version fingerprint; the sandbox ConfigMap hash was checked against the repository before repeating the canary.
- Canary #94 (run `f39ff6590c88`) completed with the expected output. The live Dispatcher ledger reports collector `independent-cgroup-read-v2`, cgroup v2, `available=true`, cumulative CPU `0.059802s`, current memory `14,254,080 bytes`, and CPU limit `2.0` cores. `memory.peak` is unavailable in the runner's cgroup view and is explicitly listed in `unavailable_metrics`; this does not discard the CPU/current-memory measurements. The task remains in Review for the user.
- Argo CD `kanzo-sandbox` is Synced/Healthy at `4409ba71eb003c98f9730cd58ebbad8b26aaa600`; `kanzo-agent-worker` is Synced/Healthy at `8df0c075f1bb63c53f151c00f0e2d2e8e27f1534`. The broker and Dispatcher rolled out, and no sandbox Pod remained after the canary. Python compile, `git diff --check`, and both Kustomize renders passed; no test suite was run.
- Remaining telemetry gap: memory peak is not available via this cgroup view, and no instantaneous CPU peak is collected. If memory peak becomes important, inspect the node/kernel cgroup interface separately; current CPU accounting no longer depends on Prometheus scrape timing.

### 2026-11-16 — Sampled memory high-water mark for WSL cgroup v2

- The K3s host runs WSL2 kernel `5.15.167.4-microsoft-standard-WSL2`; this kernel does not expose `memory.peak` on the host cgroup view. The sandbox does expose `memory.current`, so commit `82fa9e5` adds a 100ms in-run sampler as a fallback. The emitted field is explicitly labeled `memory_peak_source=sampled_memory_current` with `memory_peak_sample_interval_ms=100`; it is approximate and may miss shorter transients. Kernel `memory.peak` remains preferred whenever available.
- Canary #95 (run `4f0dfbdd1ed9`) completed with the expected output. Dispatcher ledger verification found collector `sampled-memory-peak-v3`, `available=true`, cumulative CPU `0.123465s`, current memory `14,512,128 bytes`, sampled high-water mark `14,520,320 bytes`, and no unavailable metrics. The task remains in Review for the user.
- Argo CD `kanzo-sandbox` is Synced/Healthy at `82fa9e5e0864d30d3f26117cac8e7e8ed0b9817e`; the broker rolled out and no sandbox Pod remained after the run. The fallback closes the memory high-water visibility gap for the current WSL kernel without changing task completion behavior.
- The collector uses one daemon thread per ephemeral runner and performs a read-only cgroup file sample every 100ms only when the kernel peak counter is absent. Canary #95 exercised this fallback successfully; it remains an estimate that can miss sub-100ms transients. The task stays in Review, and no synthetic task was marked Done.

### 2026-11-16 — Prometheus and Grafana per-run sandbox resource visibility

- Commit `87ba2d8` exports cumulative sandbox CPU and memory high-water histograms from the Dispatcher using only bounded `kind` and `source` labels. Task IDs and run IDs are deliberately excluded. The existing 30-second `kanzo-agent-worker` ServiceMonitor already scrapes this endpoint.
- Canary #96 (run `c0d5ef5b6f2d`) completed with `KANZO_METRICS_OK`. Dispatcher `/metrics` reported one observation for each histogram. The Grafana Prometheus datasource independently returned both histogram `_count` series at `1`, tagged only with normal scrape labels plus bounded metric labels.
- GitOps ConfigMap `kanzo-sandbox-dashboard` is visible in Grafana as dashboard UID `kanzo-sandbox-resources`, with four panels for CPU p50/p95, memory high-water p50/p95, and measured-run counts. Dashboard queries use rate over histogram buckets, so the quantiles describe per-run distributions over the selected range.
- Argo CD `kanzo-agent-worker` is Synced/Healthy at `87ba2d8c2c6308f75652143d3f1d00591c201d81`; Dispatcher is 1/1. The task remains in Review. No quality judgments are derived from these operational metrics; Langfuse remains the model-trace source.

### 2026-11-16 — Skills persistence, environment dedupe, and Hermes venv drift

- The existing researcher skills `graphify-graph-investigation` and `hermes-profile-toolset-narrowing` retained identical SHA-256 hashes across two Executor restarts. After both restarts, Hermes CLI listed 157 enabled skills including both entries. This verifies persisted profile files and CLI discovery; it does not claim the skill instructions were invoked by a model turn.
- Removed nine duplicate environment-variable entries from the Executor and legacy Worker Gateway manifests. The previous live rollout had emitted Kubernetes warnings that duplicate names hide earlier values. Commit `a2929f5` is Argo Synced/Healthy; live Deployment now has one entry for each affected name.
- Investigated the recurring `venv: out of sync with uv.lock` warning. The image installed OMH 4.0.7 into Hermes' own locked venv, changing its installed distribution set. The image build check also used `activate(allow_incomplete=True)`, which intentionally skips the venv verdict. Runtime plain `activate()` returned `['venv: out of sync with uv.lock']`; OMH itself ran, but the warning was real.
- Updated the worker image to install OMH in `/opt/omh/.venv` with system site packages, leaving Hermes' locked venv untouched. Changed the build check to call plain `activate()` and require an empty result. Buildah successfully built and imported image `localhost/home-server/kanzo-agent-worker:0.2.1-hermes-v0.23.1-omh-4.0.7-pnpmfix2`; during build, OMH reported version 4.0.7 and Hermes PM returned `[]`.
- Argo CD applied commit `8b57b0c` and is Synced/Healthy. Executor is 3/3 Ready and Dispatcher 2/2 Ready. Bootstrap and migration init containers completed successfully.
- The first live check correctly exposed a second issue: the PVC retained an older PM-managed Python environment whose recorded stamp included the former OMH-in-Hermes installation and did not match the current profile plugin set. Backed up the prior runtime facts file as `facts.json.pre-pnpmfix2-20261116`, then ran Hermes' supported explicit `sync_venv` against the current persistent profile configuration. It resolved and installed dependencies successfully. Runtime `activate()` now returns `[]`, `venv_is_current()` is `True`, `omh --version` reports 4.0.7, and `hermes skills list` runs without the out-of-sync warning and lists 198 enabled skills (40 builtin, 158 local). The original memory/skill data was not modified.
- No user-owned canary cards were changed. #92–#96 remain in Review. Immediately before rollout the Dispatcher ledger contained no active runs; it still contains only terminal `review` and `waiting` rows.

### 2026-11-16 — Drain accepted Gateway turns during pod shutdown

- Source audit found that Executor SIGTERM previously stopped the API and immediately interrupted every accepted Gateway admission; the 60-second Executor pod grace was also shorter than the 2,700-second helper deadline. Dispatcher already had a 3,300-second grace. A prior status note describing a 1,800-second Dispatcher grace was stale.
- Commit `301242b` makes Executor shutdown reject new submissions but wait for accepted submissions to unregister, up to 2,760 seconds. A child attached after SIGTERM remains part of the accepted drain, and the submission loop no longer kills it just because shutdown began. At the drain deadline, the existing exact-admission interrupt and process-group cleanup remain the bounded fallback.
- The Executor pod grace is now 3,300 seconds. Executor API drains for 2,760 seconds; Gateway receives `HERMES_RESTART_DRAIN_TIMEOUT=2820` so it remains available for 60 seconds after API's bounded fallback to settle/cancel the exact turn, leaving further reserve before Kubernetes' forced-kill deadline. Dispatcher grace remains 3,300 seconds.
- Follow-up commit b83852e sets the Gateway drain to 2,820 seconds, 60 seconds after the Executor API fallback. Argo CD is Synced/Healthy at b83852eb4b5b8e5206ee0661917d1155989410f8; Executor is 3/3 and Dispatcher 2/2 Ready. Live spec reports grace 3,300 and Gateway env 2,820.
- No active run existed before rollout, and none is active now. Hermes PM remains clean (`activate() == []`, `venv_is_current() == True`). This deployment evidence does not prove the drain with a live in-flight turn; that canary remains open, and no response was deliberately interrupted to simulate it.

### 2026-11-16 — Delegated child broker cache regression and repair

- Canaries #98, #101, and #103 reproduced a parent-side `session_mapping_not_found` after a delegated child completed. The child's terminal calls succeeded, but the parent terminal and `search_files` calls failed. Broker fingerprints in #103 matched the revoked child session, proving Hermes had retained/reused the child's terminal environment object.
- Commits `ad83fd1` and `038607d` split missing vs. ambiguous session lookup errors and add privacy-preserving session fingerprints plus lifecycle mapping-count logs. Commit `ce46e3f` added a task-local parent session restoration guard. Canary #101 showed that guard alone was insufficient because the stale child environment remained cached.
- Commit `ce37dba` fixes the defect: `subagent_stop` evicts and cleans the cached Kanzo terminal object bound to that exact child before revoking its broker alias. It leaves parent and sibling environments alone. Provider tests pass 5/5; broker tests pass 8/8, including missing/ambiguous mapping and lifecycle mapping counts.
- Canary #101 rerun `80ac0f6e713b` passed both child and parent terminal calls plus parent `search_files`. Sequential canary #106 `e3c6a145fbd1` passed two child delegations and parent terminal/search after each, plus parent `find`. All saw the same empty `/workspace`; no broker mapping errors or workspace writes occurred. One unnecessary child `read_file` attempt against `/opt/hermes` failed because it is outside the sandbox; it did not affect the canary.
- Plugin Doctor passed for default, researcher, builder, and reviewer. Argo CD apps are Synced/Healthy at `63b4b23`; Executor 3/3, Dispatcher 2/2, and broker 1/1 are Ready. Cards #99/#100/#101/#102/#104/#106 were resolved; #105 was declined because no existing memory claim needed correction. The incident and retry-cost finding are saved in ai-memory.
- The worker's terminal tool wrapper retries all exceptions three times with 2/4/8-second backoffs. A deterministic HTTP 404 therefore incurs wasted latency; one worker summary reported one attempt despite internal retries shown in Gateway logs. A follow-up to prevent retries on deterministic broker 4xx and avoid replay after uncertain execution remains open.

### 2026-11-16 — Live Gateway drain canary exposed overlapping-owner race

- Canary `test-1#107`, run `b39bdf7b5763`, was admitted before the executor Pod received SIGTERM with its 3,300-second grace. The old executor API and `gateway_execution.py` process remained alive after deletion began; Prometheus showed RTX 4080 utilization at 99% during the run and 0% after it stopped. No prompt replay was observed.
- Kubernetes created a replacement Pod while the old Pod was still terminating. The replacement API opened the same persistent SQLite ledger and changed the accepted run from `admitted` to `ambiguous`; the worker consequently moved #107 to Review with `executor reported an incomplete or ambiguous execution`. A later reconciliation recorded `reconciled_terminal`, but the worker had already lost the response and the canary is not a pass.
- During overlap, the replacement Gateway repeatedly exited while the previous Gateway still owned `/opt/data/home/gateway.lock`. Its API `startupProbe` checked `/readyz`, so it restarted while the Gateway was unavailable. After the old Pod exited, the replacement eventually recovered to 3/3 Running, `/readyz` returned ready, and Argo CD was Synced/Healthy at `63b4b23`.
- LiteLLM returned successful IQ3S requests from the old Pod during the canary. Its own span exporter also logged HTTP 404 responses; this is separate from the Hermes worker's incomplete execution and remains a telemetry configuration item to inspect.
- Local fix in progress: a shared-volume runtime ownership wait before any replacement containers start; an exclusive process lock around the executor ledger; and an API startup probe against `/livez` while `/readyz` remains the readiness gate. Syntax, lock contention/reacquisition, and Kustomize rendering passed locally. Deployment and a second live canary are still required.

### 2026-11-16 — Drain ownership fix passed its guard, completion receipt race found

- Canary `test-1#108`, run `84be68a399da`, was accepted before the executor Pod was deleted with a 3,300-second grace period. The replacement init container visibly waited on the shared Gateway and executor API locks; it did not start replacement containers while the old runtime held either lock. The old Gateway drained the turn successfully (`active_at_start=1`, `active_now=0`, `timed_out=false`) after about 103 seconds. This verifies the ownership-serialization fix deployed at `e0f8cc7` prevented the #107 ledger-overlap failure mode.
- #108 still ended in Review with `executor reported an incomplete or ambiguous execution`; the card was not replayed. Code inspection found the next race: after receiving `message.complete`, `gateway_execution.py` called `prompt.receipt` a second time. Gateway's completion event already contains the committed `admission_id`, terminal `outcome`, `status`, and response text. During shutdown the redundant RPC could lose the result even though the turn had settled and drained successfully. The executor API later reconciled the ledger row to `reconciled_terminal`, but no successful Kanzo result was posted.
- Follow-up change removes the post-completion RPC and trusts only a `message.complete` payload with the expected admission ID, `status=complete`, `outcome=completed|success`, and a nonempty response. Mismatched, interrupted/error, or empty events remain failures; the prompt is never resubmitted. Focused tests cover these validations and assert the successful execution makes no receipt RPC. Local verification: 5/5 tests, Python compilation, `git diff --check`, and Kustomize render pass. Rollout and a fresh canary are pending.
- GPU utilization returned to 9% at 05:36 UTC before the next canary. The LiteLLM span-exporter 404 seen during prior canaries remains a separate telemetry issue and does not explain the worker result loss.

### 2026-11-16 — Committed completion handling deployed; result publication passes active Pod drain

- Commit `934cdf6` removes the post-`message.complete` `prompt.receipt` RPC. The executor accepts only the completion event carrying the expected admission ID, a successful outcome, `status=complete`, and nonempty text. Focused tests pass 5/5; Python compilation, Kustomize render, and `git diff --check` pass. Argo CD is Synced/Healthy at `934cdf6c8ac1842a0b31a5c8ca3be0af6c28b600`; Executor is 3/3 Ready with zero restarts.
- Canary `test-1#109`, run `59dc2e88751e`, completed under the new code without a Pod interruption. Executor API row recorded `submit_state=completed`; Kanzo received the run-tagged final result. This confirms normal completion through the deployed event path, but is not a drain test. RTX 4080 utilization returned from 99% to 1% after the run.
- Canary `test-1#110`, run `9f23ddeaa142`, was confirmed admitted in the executor SQLite row (`admission_id=0f49e64a2aca4ae59d0930bf19a4d7f5`, `execution_generation=1`, `submit_state=admitted`) while RTX 4080 utilization was 100%. The old Executor Pod received a 3,300-second-grace deletion request. Its replacement init container waited for the previous runtime ownership lock and exited successfully about 71 seconds later; the replacement reached 3/3 Ready with zero restarts. The durable row then showed `submit_state=completed`, GPU was 0%, and Kanzo posted the final result tagged with the same run ID at 05:43 UTC. No replay occurred. This is a pass for result publication across active Pod drain and replacement ownership serialization.
- Limitation: Kubernetes garbage-collected the old Pod before its Gateway shutdown log could be collected, so this canary does not retain a direct `active_at_start/active_now/timed_out` Gateway log line. The 71-second lock wait plus the same-run completed ledger and Kanzo result provide indirect drain evidence. Future tests should stream or export shutdown evidence before Pod deletion completes.
- Content quality was not a pass: #110's response reversed the required timeout ordering and claimed a script verified formatting despite instructions to use no tools. Treat the lifecycle signal separately from answer accuracy. Cards #107–#110 remain in Review for human inspection; no user-owned card was marked done. Temporary catalog card `kanzo-project-catalog#23` was restored to `done` after these checks.
- A local process survey also found no active WSL TUI process or tmux/screen session. Two WSL login shells (`root` on `pts/1` and `eorbr` on `pts/3`) were idle for about 29 minutes with only `-bash` running. Windows `C:\Users\eorbr\AppData\Local\hermes\bin\hermes.exe` PID 19500 had an exited parent PID 15492 and a Python child PID 19548 since Nov 14; it has no visible window and is a likely orphaned Hermes CLI/session, but the read-only inspection did not terminate it. `strata.exe` and `llama-swap.exe` remained as expected background inference services while Prometheus reported GPU idle outside runs.
- Running the complete worker suite after this rollout uncovered two failing Langfuse context tests: `langfuse_session_context.NEW` fell back to process-wide `HERMES_KANBAN_*` when the active Gateway session context was missing or unavailable. This contradicts per-session isolation and can attribute a trace to a stale run. The patch now returns the default for missing session context and never reads process-wide Kanzo identity. The existing stale-environment tests now pass; the complete worker suite passes 42/42. Python compilation and Kustomize rendering pass. Commit `923ba3c` is deployed; Argo CD is Synced/Healthy and Executor is 3/3 Ready. The installed researcher Langfuse plugin uses `bound_client_env` with no process-ID fallback. A post-fix Langfuse trace canary remains to be run.

## 2026-11-16 continuation

- Added the `human-agent-report` skill from the supplied ZIP to the worker ConfigMap and installs it into the persistent default, researcher, builder, and reviewer Hermes homes at startup. The final Kanzo result prompt now directs Hermes to consult it for human-facing reports, while routine progress notices and machine markers skip it. No second model call is added.
- Bound pseudonymous Langfuse user ID `kanzo-owner` to each Gateway session and added `KANZO_USER_ID` to the explicit client-environment allowlist. The Langfuse plugin's root trace calls now prefer this session-bound value. This uses no personal identity because Kanzo's actor identity is not present in the worker task contract.
- Validation: worker suite 45/45; report-skill tests 6/6; Python compilation and WSL `k3s kubectl kustomize` render passed. Deployment and Langfuse verification are pending.

- Canary `test-1#112` (run `c19740fe3a57`) loaded the registered `human-agent-report` skill and published a result comment. The attempt first made one failed literal-path `read_file` call (`/scratch/.hermes/...`); the subsequent `skill_view` calls succeeded. Langfuse confirmed 27 observations, 11 model generations, including 3 `skill_view` calls, 2 `read_file` calls, temporary report/facts creation, lint execution, and terminal/patch calls. This was excessive for a simple comment. The published text was long and repeated its preamble; it also mislabeled supplied test results as unverified. A prompt correction now invokes the skill once by its Hermes name, requests inline compose only, forbids report artifacts/lint for routine comments, and separates supplied test results from this-run checks. Worker tests pass 46/46 after the prompt guard. The correction is not deployed yet.
- The same canary verified the Langfuse fix: executor ledger run `c19740fe3a57` completed with session `local-78be08785aa861a63a6b2ecf2112ee6898c8ba6550661f2323add4684a287ee9`; the session query returned 27 observations across one trace (`424efcf503c8624bd199aeac51e06ee6`), and each observation had `userId=kanzo-owner`. This closes the user-attribution verification for the deployed `5754dd1` build.
- Canary output proposed `test-1#113` for fixing the literal skill path. The fix is in the current in-scope patch; treat the proposal as duplicate and decline it with a reason after rollout verification. Keep `test-1#112` available for human review until the lower-overhead prompt version is deployed and checked.
- Second canary `test-1#114` (run `4c96916f2af8`) produced a fact-separated Korean comment, but expanded to a long live-health investigation because the canary explicitly asked it to query operational state. Langfuse showed 15 generations and 44 observations before result publication. To avoid turning every ordinary Kanzo comment into a skill/tool workflow, the worker now applies the skill's concise reporting contract directly in its prompt and reserves loading the full skill for substantial reports/audits. The prompt targets 1,200 characters, disallows redundant preambles/artifacts, and separates supplied test results from live checks. The Markdown formatter also strips standalone separator artifacts observed in both canary comments. The latest worker suite passes 47/47; this final prompt/formatter change is not yet deployed.
- Final deployed prompt revision `5d2cf6e` is Synced/Healthy with Executor 3/3 Ready. The skill remains installed and enabled in the researcher Hermes home after rollout. The compact prompt/formatter passed 47/47 worker tests. No post-final-change LLM canary was run: prior simple report canaries consumed 11 and 15 model generations, so we avoided another expensive trial. Canary cards #112/#114 are archived done; duplicate proposal #113 is archived with `declined` plus reason; temporary catalog activation #23 is restored to done.

### Process cleanup validation

- Added Linux tests for both executor process cleanup paths. The Executor API timeout test spawns a child that ignores SIGTERM and confirms the timeout path escalates to SIGKILL and leaves no running descendant. The ReadOnly executor test confirms its termination path stops both parent and child.
- Those tests exposed an actual leak: `_run_helper` closed its selector but left stdout/stderr pipe objects open on exceptions (and success). It now closes both streams in `finally` after process-group cleanup.
- Verification: full worker suite 49/49 passes in WSL/Linux and Windows (2 POSIX-only cases skip on Windows); Linux cleanup tests pass with ResourceWarning treated as error. This is code-level validation; a forced-termination canary against a live Gateway session is still open.
- Expanded process cleanup coverage to the dispatcher worker itself: all three termination paths (executor API timeout, ReadOnly executor termination, dispatcher worker cancellation) stopped a SIGTERM-ignoring child in Linux tests. Full suite is now 50/50 in WSL/Linux; Windows passes 47 and skips the three POSIX-only tests. The executor stdout/stderr leak fix is already deployed at runtime revision d37921f; this follow-up adds coverage only, so no rollout is needed.


### 2026-11-16 — broker command automatic replay blocked

- Hermes' bundled terminal wrapper retries generic exceptions. If the Kanzo broker admitted `/exec/start` but its response was lost, raising that exception could make Hermes execute the same remote command again. The custom terminal provider now returns a structured, non-retryable result for broker exceptions and malformed start responses. It reports `unknown` if admission or cancellation cannot be confirmed, cancels the exact execution after poll failure where possible, and returns the result if the broker confirms completion. No second start is issued by the provider.
- Commit `16b52af` is pushed to `main`. Argo CD is Synced/Healthy at `16b52af048c53f8c80cfe8d7dd95bd2ef19f6413`; Executor is 3/3 Ready and Dispatcher is 2/2 Ready. The live worker homes contain the updated provider module and `human-agent-report/SKILL.md`.
- Verification: worker suite 54/54 in WSL/Linux; Windows 51 passed and skipped the 3 POSIX-only tests; Python compilation and `git diff --check` passed. Executor ledger had 51 completed, 2 cancelled, and 2 reconciled-terminal rows, with no active submit state at inspection.
- No model canary was run: the retry behavior is covered with fake broker tests for start response loss, poll failure with confirmed cancellation, completion recovered after poll failure, and unconfirmed cancellation. This avoids spending tokens to test a transport condition that can be tested deterministically. A live broker fault-injection canary remains optional if later operational evidence indicates a gap.
- The status document was reconciled to this deployed revision. Roadmap items still open are direct forced-Gateway-termination reconciliation evidence, ReadOnly learning/memory persistence and delegated lease boundaries, full cross-system terminal-outcome/generation/delegate trace reconciliation, and matched-run performance baselines. Backlog #48/#49 remain for user review.

- Follow-up deterministic runtime probe: invoked the running Gateway’s actual `tools.terminal_tool._run_foreground` with a fake Kanzo environment returning the provider’s structured non-retryable error. The wrapper called `env.execute` exactly once and returned the failure payload. No model call or remote command was made. This closes the wrapper-integration gap for returned errors; it does not simulate a real broker network fault.


### 2026-11-16 — deterministic preflight failures no longer enter Hermes retries

- The running Hermes terminal wrapper retries exceptions three times with 2/4/8-second backoff. The previous provider change handled broker start/poll failures, but deterministic refusals before a broker request (invalid command/options, unsupported local background handoff, cleaned environment, or missing interrupt API) could still be thrown into that retry loop.
- Commit `f05edac` converts those preflight failures to structured non-retryable results with `execution_state=not_started`; it computes and validates command argv and run authority before invoking `/exec/start`. Uncertain broker admission remains `unknown`, and exact post-start cancellation/result recovery remains unchanged.
- Added tests for preflight refusal and invalid command. Full worker suite: WSL/Linux 56/56; Windows 53 pass and 3 POSIX-only skips. Python compilation and `git diff --check` pass.
- Argo CD is Synced/Healthy at `f05edac94ce8782011f828e3873e772109ffc46e`; Executor 3/3, Dispatcher 2/2. Live profile code contains both preflight and broker non-retryable paths, and the `human-agent-report` skill remains installed. Executor ledger had no active submit state at inspection.


### 2026-11-16 — ReadOnly delegated boundary evidence reconciled from Kanzo cards

- Read-only Kanzo reads of `hermes-harness-lab#88` and `#106` confirm that the delegated boundary is not wholly unverified. In #88 a delegated child terminal write under `/workspace` failed with kernel `Read-only file system`, the marker was absent, and a same-child `/scratch` write/read/remove succeeded. The platform tool trace contained terminal calls only. This directly proves mount-level enforcement for a delegated terminal call on an empty sandbox (`repo: none`).
- In #106 two sequential child delegations and five parent follow-up calls succeeded; parent and children saw the same empty `/workspace`, with no broker mapping errors. This is live evidence for child registration/revocation and parent-session restoration across sequential delegation.
- Limits remain: #88 did not use a checked-out repository, and child session identity/raw transcript could not be independently read from inside the sandbox. A combined repository-backed check across terminal, file, execute_code, and delegates plus trusted child trace attribution is still open. Proposals #89/#90 remain backlog for user review and were not moved.
- The status document now distinguishes these proven mount/delegation properties from the remaining full-path and observability checks.


### 2026-11-16 — SSE event wake smoke and exact-response protocol correction

- Live dispatcher logs showed the event adapter connected to Kanzo SSE; dispatcher and executor were Ready with no active executor admission. A temporary `tool-free` task `hermes-harness-lab#115` was created in the already active project to verify an actual event wake at low token cost.
- The task changed to Doing within the event cycle, run `fe8c9c250a2c` was admitted and completed, and the worker posted exactly `EVENT_WAKE_OK`. Kanzo moved it to Review because the model followed the exact-body instruction but omitted the worker outcome marker. This is not recorded as a fully successful task completion, and it was not automatically retried.
- The worker's tool-free prompt now explicitly separates exact human-facing body text from the required outcome/summary protocol footer. A unit test checks that `parse_worker_outcome` removes the footer and preserves the exact body. Event-adapter tests also cover a failed local wake followed by reconnect reconciliation.
- Validation: full suite 58/58 WSL/Linux; Windows 55 passed with 3 POSIX-only skips. Commit `47fe509` is Argo Synced/Healthy; Executor 3/3 and Dispatcher 2/2. The live dispatcher code contains the prompt correction, event adapter logs a current SSE connection, and the executor ledger has no active submissions. #115 remains Review as evidence of the missing-marker outcome; no retry or status override was performed.


### 2026-11-16 — Persistent Hermes PM environment reconciliation

- The live Gateway again reported `venv: out of sync with uv.lock`. Its recorded dependency stamp was `ed216e...`, while current Hermes PM calculated `e6700e...`; `activate()` returned the drift and `drift()` reported the same warning. The persistent `facts.json` and resolved lock were last written at 03:37 UTC.
- The resolved lock contained six packages absent from the image lock: four profile-specific Langfuse plugin entries plus `langfuse` and `backoff`. The four enabled profile copies of the Langfuse plugin were present. Their `__init__.py` files were modified at 07:17 UTC, after the PM facts/lock snapshot, explaining the changed plugin stamp. Startup patched/configured plugin code but did not reconcile the PM environment afterward.
- Commit `af7f087` adds a ConfigMap-mounted PM reconciliation init container after `wait-for-previous-hermes-owner`. It invokes Hermes' explicit `sync_venv` only for the known venv lock drift and then requires `activate()` to be clean. It fails closed on unrelated or persistent PM errors. The current matching environment is a no-op; no recurring dependency install or LLM call is added.
- Validation: 62 worker tests passed on Windows (3 POSIX-only skips); Kustomize rendered successfully. Argo CD is Synced/Healthy at `af7f08705ca4b990111c57f410c70439b7883147`. On rollout the new init container installed the selected Python dependencies and completed. Live Gateway checks returned `activate_problems=[]` and `pm_drift={}`; `hermes --help` no longer prints the warning. No Kanzo task was active during rollout.
- Remaining: observe the no-op path on a later ordinary restart and continue validating other roadmap items. OMH's 155-skill / ~54,250-token count is a directory-count heuristic, not measured prompt usage; actual Hermes prompt assembly still needs inspection before changing skill availability.


### 2026-11-16 - Kanban schema deferral and prompt-size measurement

- Worker profiles keep Kanban available through Hermes tool search, but defer its 15 full schemas from every model request. Offline inspection of the actual Hermes prompt builder measured researcher/builder/reviewer at 21 direct tools and 41,232-41,235 tool-schema bytes, down from the 65,955-byte baseline (about 24.7 KB / 37.5% less). Skill indexes remain present at 15,708-15,892 bytes. These are assembled byte sizes, not provider token counts; no model call or skill removal was involved.
- Commit `7bf53db` deployed the profile deferral. The first helper variant imported `model_tools`, which ran global plugin discovery and emitted an unnecessary Langfuse dependency-install notice. Commit `c222f96` imports only `tools.kanban_tools` to register the needed names. The deployed helper reports `already configured` without the plugin notice, and PM reconciliation reports `Hermes PM environment is current`.
- Argo CD is Synced/Healthy at `c222f960e026bf11e3a90785ea277f35e8b46922`; Executor 3/3 and Dispatcher 2/2 are Ready with zero restarts. Post-rollout prompt-builder inspection returned the same 21-tool / about 41.2 KB measurements for all three worker profiles. This is structural validation only; a normal task is still needed to confirm model-side tool retrieval behavior. No canary task was created to avoid model-token cost.


### 2026-11-16 - Reconcile ambiguous submissions created during runtime

- Inspection found that the executor reconciliation thread exited after startup when no ambiguous rows existed. A later Gateway disconnect could mark a live submission ambiguous, but that same API process would not revisit it unless the Pod restarted.
- Commit `d3f47c5` keeps the reconciler dormant on an event when the ledger is clear, wakes it when a runtime create/submit becomes ambiguous, and retries unresolved exact-identity reconciliation every 60 seconds. It never resubmits a prompt. Confirmed terminal rows do not wake the loop. Short-lived SQLite connections now close deterministically; this also fixed Windows test cleanup leaving the ledger file locked.
- Added focused tests for ambiguous-state wakeup, terminal receipt reconciliation without prompt replay, and no wake on confirmed completion. Full worker suite: 69 run, 66 passed, 3 platform-specific skips. Python compilation, `git diff --check`, and Kustomize rendering passed.
- Argo CD is Synced/Healthy at `d3f47c5c2e6c1f87c830bf033fe0a038c207319b`; Executor 3/3 and Dispatcher 2/2 are Ready with zero restarts. The init PM check is current and the live Executor API process runs the new ConfigMap-mounted code. Ledger inspection before rollout showed no active submissions; the two `reconciled_terminal` rows were historical. This improves runtime recovery but does not yet prove a forced Gateway termination and final Kanzo/Langfuse publication end to end.


### 2026-11-16 - Hermes memory path after Executor Pod replacement

- Runtime inspection confirmed the active Hermes built-in memory store reads `<HERMES_HOME>/memories/MEMORY.md`. On the live researcher profile, the persistent source file exists under `/opt/data/home/profiles/researcher/memories/MEMORY.md`. After Executor rollout replaced the Pod, the new Gateway's actual offline prompt builder loaded 2,499 memory bytes for researcher (builder/reviewer currently have none). This is direct evidence that the PVC-backed profile memory reaches a fresh prompt after Pod replacement, without a model call. It does not yet prove a subsequent task used a particular remembered fact.
- The dormant `readonly_executor.py` sync path only copied top-level `MEMORY.md`, so it missed Hermes' nested `memories/MEMORY.md` and `memories/USER.md`. Commit `9563e7e` adds those paths with the existing three-way merge so local ReadOnly learning survives repeated profile sync; focused tests cover initial copy, retained local learning, and changed source updates. Full worker suite: 71 run, 68 passed, 3 platform-specific skips; Kustomize render passed.
- Scope note: the legacy `kanzo-agent-worker` Deployment is currently at replicas=0. Active ReadOnly tasks use the broker and resident Executor profile HOME, so the legacy sync fix is compatibility code, not proof of active-path behavior. Argo CD is Synced/Healthy at `9563e7e4c1161e34362f96353c3f11dbc884fba0`; Executor 3/3 and Dispatcher 2/2 are Ready with zero restarts.


### 2026-11-16 - Load human-agent-report for Kanzo result comments

- The `human-agent-report.zip` supplied by the user is byte-identical to the eight files already committed under `platform/kanzo-agent-worker/skills/human-agent-report`; no skill content replacement was needed. The skill is mounted in the Hermes homes and worker skill ConfigMap.
- The worker prompt now explicitly requires `skill_view(name="productivity/human-agent-report")` immediately before composing each final Kanzo result comment, then applies the skill's Compose and evidence-contract guidance in the same generation. It does not create routine report artifacts or make a second model call. Loading the skill text adds input context and may increase input-token use; the impact should be checked in a natural Langfuse trace.
- The ReadOnly #116 report exposed an appended Hermes footer that labels expected `/workspace` EROFS write denials as failed edits. The worker now removes that suffix from the human-facing comment only when every listed mutation is under `/workspace` and explicitly reports EROFS/read-only evidence. The raw run artifact and ledger retain the original output, while other paths and real failures retain the footer.
- Focused reporting tests: 5/5 passed; Python compilation and `git diff --check` passed. Commit `a37ea89a9d9464a577d95ce880f8f166ae2455ff` is deployed; Argo CD is Synced/Healthy, Executor and Dispatcher are each 1/1 Ready with zero restarts, and the live Dispatcher `worker.py` contains the `skill_view` instruction and footer filter. No model canary was run, so actual tool invocation and comment quality await a natural result comment.
- Follow-up runtime check: the live Hermes registry exposes `skill_view` under the enabled `skills` toolset, and invoking the same lookup name `productivity/human-agent-report` resolves the installed skill and returns its content. This validates tool availability and skill-name resolution without a model call; a future natural Kanzo run must still confirm the model actually invokes it.
- Token-efficiency follow-up: because `skill_view` supplies the full instructions, the worker prompt no longer repeats the skill's detailed style checklist. This removes 418 characters (about 100 tokens by rough character-count estimate) from the prompt while preserving the explicit tool call and no-extra-model-call rule. The full skill content still adds input context; actual provider token usage remains to be measured from a natural Langfuse trace.
- Current readiness snapshot: executor ledger contains 57 rows (53 completed, 2 cancelled, 2 reconciled-terminal) and zero in-flight/ambiguous rows; `hermes-harness-lab` and `test-1` have no Doing tasks. The Gateway pod's network namespace has an established connection to the Hermes Dashboard listener on port 9119 (plus other established sockets whose peers are not attributable from this snapshot). I did not force-terminate the shared Gateway because the live dashboard connection could be a user's active session. A direct crash canary needs an isolated Gateway or a confirmed idle window; this does not prevent read-only roadmap work.


### 2026-11-16 - SearXNG provider wiring resumed

- GitOps commit `d8601b5f3c5c60e58c0ab15a649ca52708be3a57` adds the cluster service URL `SEARXNG_URL` to the resident Hermes Gateway, bootstrap env sync, and profile env sync. This is a non-secret in-cluster endpoint reference. Argo CD is Synced/Healthy at this revision; Executor is 3/3 Ready and Dispatcher 2/2 Ready with zero restarts. Runtime resolution was checked for the default, researcher, builder, and reviewer Hermes homes.
- Human-action prerequisite `hermes-harness-lab#87` is done, which resumed `hermes-harness-lab#42` as run `4a4499fdc001`. At 2026-11-16 11:31 UTC, Kanzo still showed the task `doing`, the executor ledger showed `running`, and the run had no result yet. Do not restart it. Acceptance (two consecutive built-in `web_search` calls with `served_by=searxng` and no `backend_error`) remains pending.
- At this entry's initial snapshot, run `4a4499fdc001` was still active; its final state and actual reporting-skill use are recorded in the follow-up entry below.


### 2026-11-16 - SearXNG run completed; reporting token waste found

- Live Executor inspection confirmed the default, researcher, builder, and reviewer profiles each use `search_backend: searxng` and `SEARXNG_URL=http://searxng.search.svc.cluster.local:8080`. From the Gateway container the JSON endpoint returned HTTP 200 with 31 results; invoking the installed researcher `SearXNGWebSearchProvider.search` twice with the same query returned 3 results each time, with no `backend_error` or `rescued_from`. The Langfuse trace for run `4a4499fdc001` contains two successful `web_search` tool observations. Gateway logs show no keyless-rescue warning in the run window.
- Source inspection of installed Hermes `plugins/web/keyless_mcp.py` confirms `data.served_by` is set only when keyless failover serves a vendor different from the configured provider. A successful primary SearXNG call therefore has no `served_by=searxng` field; provider config + direct primary calls + absence of rescue markers are the correct success evidence. Kanzo #42 and prerequisite human action #118 were marked done with these grounds; the worker's original handoff incorrectly asked for an external tailnet URL despite the validated in-cluster endpoint.
- The natural run's Langfuse trace `9aed988f6dcd2988f25e8075da3c7d60` contains 54 observations and 21 generations. Hermes metadata sums to 566,076 approximate input tokens across those generations (estimate, not billed token usage). Tool observations include 3 skill loads, 3 MCPProxy discovery calls, 2 tool-description calls, 9 read calls, and 2 built-in web searches. The worker's long handoff comment was 3,538 characters and asked for already-satisfied human work. A concise corrective comment was added with the verified result.
- To reduce repeated context, `task_prompt` now directs Hermes to load `human-agent-report` once at finalization, reuse it within the run, keep routine comments under 1,200 characters, avoid repeating prior comments, check whether human actions are already satisfied, and use narrow single-pass MCPProxy discovery. The focused reporting tests and full worker suite pass (74 run, 71 passed, 3 platform skips); Python compilation and `git diff --check` pass.
- The live Kanzo card is done but the execution row correctly remains `waiting`: the Hermes process stopped at the human-action boundary, then the operator independently verified configuration and provider behavior. The row's `external_resolution` field records the done card, verified GitOps revision, and manual verification without rewriting the original run outcome or replaying it.
- GitOps revision `70fdae47c54657acea6b2460b107428421afa3c9` deploys distinct generated ConfigMaps for Dispatcher and Executor/Gateway Python. Argo CD is Synced/Healthy; Executor and Dispatcher are each Ready, and the live Dispatcher contains all prompt guards. The new Executor restarted once for this migration, while future `worker.py`-only changes will roll Dispatcher without rolling the shared Gateway. During the sync, the old Dispatcher briefly reported `FailedMount` for its pruned ConfigMap; Argo then replaced it with the new ReplicaSet, which reached 2/2 Ready with zero container restarts. After Gateway startup, all four profiles again resolved SearXNG config, and the direct provider returned 3 results in each of two consecutive calls with no fallback metadata. The human-agent-report skill file remains present in the persistent researcher home.

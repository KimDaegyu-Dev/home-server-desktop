# Kanzo / Hermes 진행 상태

설계 SSOT는 [Kanzo / Hermes 로드맵](kanzo-hermes-roadmap.md)이다. 이 문서는 현재 배포 상태와 아직 필요한 검증만 요약하며, 근거와 실행 이력은 [증거 로그](kanzo-hermes-evidence-log.md)에 기록한다.

## 현재 상태 — 2026-11-16

- GitOps `main` worker revision `c222f96` is Argo CD Synced/Healthy. Executor 3/3 and Dispatcher 2/2 are Ready with zero restarts. The latest Kanban setup init completed without global plugin discovery; Hermes PM reconciliation reported current.
- Hermes가 Kanzo broker terminal 명령의 응답을 받지 못했을 때, provider가 일반 예외를 Hermes에 던져 같은 명령이 자동 재실행될 수 있는 경로를 막았다. 원격 실행 전의 입력 거부와 설정 오류도 `not_started` 결과로 반환해 불필요한 재시도를 막는다. broker start 응답이 유실된 경우는 실행 상태를 `unknown`으로 알리고, poll 오류는 가능한 경우 해당 실행만 취소하거나 이미 완료된 결과를 회수한다. 자동 재시도는 하지 않으며, 상태를 확인한 뒤 사람이 판단하도록 결과에 안내한다.
- 변경 commit `f05edac`. 전체 worker 테스트는 WSL/Linux 58/58 통과, Windows 58개 중 55개 통과와 POSIX 전용 3개 skip이다. 실행 중 Hermes Gateway의 실제 `_run_foreground` 래퍼 프로브에서 모의 broker 실패 결과와 `env.execute` 1회 호출을 확인했다. 최신 배포 Pod에는 broker 오류와 사전 거부의 비재시도 코드가 있고 report skill도 설치돼 있다. Python compile과 `git diff --check`도 통과했다. 짧은 이벤트 wake canary `hermes-harness-lab#115`는 SSE 뒤 실제 run `fe8c9c250a2c`을 시작해 `EVENT_WAKE_OK`를 반환했고 ledger는 completed였다. 카드가 Review로 남은 원인은 outcome marker 누락이며, 재실행하지 않았다. 그 응답 형식을 구분하도록 prompt/test를 보완했다. report 품질 측정용 모델 canary는 실행하지 않았다.
- 사용자가 제공한 `human-agent-report` 스킬은 persistent default/researcher/builder/reviewer Hermes 홈에 설치되어 있다. 일반 Kanzo 결과 댓글은 가독성 규칙을 worker prompt에서 직접 적용하므로 스킬 재로딩이나 별도 모델 호출을 추가하지 않는다. 자세한 감사·보고에만 스킬 전문을 사용한다. 배포된 Pod에서 스킬 파일과 provider 코드를 확인했다.
- `af7f087`에서 Hermes PM의 지속 환경을 시작 시 검사·조정하도록 했다. Langfuse 플러그인 소스가 PM stamp 생성 후 바뀌었는데 다음 Gateway 시작 때 환경을 다시 맞추지 않아 `venv: out of sync with uv.lock` 경고가 재발했다. 새 init 단계는 이전 Gateway 소유권이 해제된 뒤 실행되며, 이 잠금 불일치에 한해서만 명시적 동기화를 하고 재검사한다. 라이브 rollout에서 동기화가 수행됐고 `activate()`와 `drift()`가 깨끗하며 `hermes --help`에서도 경고가 사라졌다. 전체 worker 테스트 62개 통과(Windows POSIX 전용 3개 skip), Argo CD Synced/Healthy다.
- 보고 규칙 변경 뒤 별도의 품질 canary는 비용을 고려해 수행하지 않았다. 앞선 단순 보고 canary 두 건이 각각 11회/27 observations, 15회/44 observations를 썼다. 그러므로 비용 절감은 설계상 줄였지만 운영 평균 감소량을 측정했다고 주장하지 않는다.
- Langfuse 사용자 연결은 canary `test-1#112`에서 확인됐다. 단일 trace의 27개 observation 모두 pseudonymous `userId=kanzo-owner`를 기록했다. 보고 canary #112/#114는 보관 완료, 중복 제안 #113은 `declined` 이유와 함께 보관했다.
- report canary 결과의 사람 검토, #107–#110 등 기존 Review 카드의 판단은 사용자 몫으로 남겨 두었다. backlog #48/#49는 사용자 승인 전 Todo로 옮기지 않았다.

## 남은 작업

1. Gateway가 실제 turn을 처리하는 중 강제 종료되는 상황에서 child process 정리, admission/ledger 복구, 최종 Kanzo 게시를 함께 검증한다. #110에서 drain 중 결과 게시와 no-replay는 확인됐지만, Gateway 종료 로그를 직접 보존하지 못했다.
2. ReadOnly 메모리·회고가 Pod 교체 뒤 후속 작업에서 실제로 검색·사용되는지 검증한다. #82는 저장 파일의 1회 재시작 생존은 보였지만 두 번째 작업의 회수는 입증하지 않았다.
3. ReadOnly 위임 경계의 남은 부분을 검증한다. #88은 위임 자식의 `/workspace` 쓰기에서 커널 `EROFS`와 `/scratch` 쓰기 성공을 확인했고, #106은 자식 두 명을 순차 위임한 뒤 부모 terminal/file 검색이 계속 성공했다. 아직 repo checkout을 둔 전체 terminal/file/execute_code/delegate 경로 대조와 trusted telemetry의 child identity/transcript 상관관계가 남아 있다. #89/#90은 backlog에 있으며 사용자가 직접 검토한다.
4. 성공·실패·취소·timeout의 Kanzo 카드, executor ledger, 산출물, Langfuse를 대조하고 generation 중복 방지 및 delegate trace 연결을 확인한다. backlog #48/#49는 사용자가 직접 검토한다.
5. 같은 작업 범위의 one-shot/resident 실행을 비교해 cold/warm 지연, 입력·출력 토큰, CPU·메모리·GPU, 회고 재사용 효과를 측정한다.
6. compact report 규칙의 실제 댓글 품질은 이후 자연스러운 작업에서 관찰한다. 운영 동작이 충분하지 않으면 별도 LLM canary보다 기존 댓글을 우선 검토한다.
7. Hermes offline prompt-builder measurements for researcher/builder/reviewer after Kanban schema deferral: 21 direct tools and 41,232-41,235 schema bytes, versus the 65,955-byte baseline. This removes about 24.7 KB (37.5%) of tool schemas while keeping all 15 Kanban tools searchable. Skill indexes are 15.7-15.9 KB. These are assembled byte counts, not provider token accounting. No skills were removed and no model canary was used; real token attribution needs a Langfuse request with input usage.

완료 표시는 로드맵 각 acceptance와 배포 증거를 확인한 뒤 갱신한다.

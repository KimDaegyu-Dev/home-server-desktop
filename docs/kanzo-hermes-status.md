# Kanzo / Hermes 진행 상태

설계와 완료 기준의 SSOT는 [Kanzo / Hermes 로드맵](kanzo-hermes-roadmap.md)이다. 날짜별 근거는 [상세 증거 로그](kanzo-hermes-evidence-log.md)에 보존한다. 이 문서는 최신 확인 상태와 미완료 작업만 요약한다.

## 현재 확인 상태 — 2026-11-16

- GitOps 저장소 main에 graceful-shutdown 수정이 반영됐다. Argo CD kanzo-agent-worker는 Synced/Healthy이며, Executor 3/3과 Dispatcher 2/2 Ready다.
- Dispatcher는 실행 중 작업을 비우고 종료한다. Executor API는 SIGTERM 이후 새 제출을 막고, 이미 받아들인 제출은 최대 2,760초 drain한다. Gateway drain은 2,820초, Executor Pod 종료 유예는 3,300초다. 실행 중인 작업을 직접 끊지 않는 설정으로 배포됐지만, active-turn 중 drain은 아직 실증하지 않았다.
- 현재 Dispatcher ledger에는 실행 중인 run이 없다. 상태는 Review 37건, Waiting 6건이다. #92–#96은 사용자가 판단할 수 있도록 Review에 그대로 둔다.
- Hermes PM 검사는 깨끗하다: activate()는 빈 목록, venv_is_current()는 True다. OMH 4.0.7은 Hermes venv와 분리되어 있고 Hermes CLI는 198개 스킬을 표시한다.
- 기존 researcher 스킬 파일은 Executor 두 차례 재시작 전후 해시가 일치했다. 이는 디스크 보존과 CLI 검색 가능성의 증거이며 모델이 실제 작업에서 지침을 적용했음을 증명하지는 않는다.

## Phase 6에서 남은 검증

## Progress in this turn

- Added Hermes `subagent_start`/`subagent_stop` hooks and broker child-session aliases, then verified Plugin Doctor passes for default, researcher, builder, and reviewer. The claim parser now accepts only known Kanzo statuses; bracketed canary titles no longer masquerade as status tokens.
- Live canaries #98, #101, and #103 reproduced the parent broker failure. Fingerprint comparison proved that Hermes reused the child's terminal environment after `subagent_stop` revoked that child alias.
- The final fix evicts and cleans only the cached child terminal object before revoking its alias; parent and sibling environments stay untouched. Broker lookup errors now distinguish missing from ambiguous mappings and log only short session fingerprints.
- Verification passed: broker tests 8/8; provider lifecycle tests 5/5; Python compile checks; Kustomize render; Argo CD apps Synced/Healthy at `63b4b23`; Plugin Doctor passed for default, researcher, builder, and reviewer. Canary #101 passed all child/parent terminal checks and parent `search_files`. Sequential two-child canary #106 also passed both child calls, parent terminal/search after each child, and parent `find`; all saw the same empty workspace and no broker mapping error appeared. One extra child `read_file` attempt against `/opt/hermes` failed because that path is outside the sandbox; no workspace write occurred.
- Human action #102 was completed from the passing canary. Backlog proposals #99/#100/#104 were completed with the deployed fix, missing/ambiguous lookup diagnostics, lifecycle mapping-count logs, and passing terminal/search_files canaries; #105 was declined because no old memory page existed to correct. A durable ai-memory incident note records the verified cause, fix, evidence, and token-cost pattern.
- Token-cost finding: Hermes' generic terminal wrapper retries deterministic broker 4xx errors three times (2/4/8 second backoffs). One earlier worker result said a call ran once while gateway logs showed these internal retries. Future reviews must distinguish model-visible tool calls from backend attempts; a follow-up to classify broker 4xx responses as non-retryable remains open.
- Drain canary #107 failed on replacement ownership overlap; #108 verified ownership serialization and Gateway drain but exposed a post-completion receipt race. Canary #110 (`9f23ddeaa142`) passed end-to-end result publication across an active Pod drain: ledger `admitted → completed`, RTX 4080 `100% → 0%`, replacement init waited 71s for the previous runtime lock, and Kanzo posted the same run's final result. The previous Pod logs were garbage-collected before the Gateway drain summary could be captured, so the Gateway-specific drain state is inferred from lock release and successful publication rather than directly retained. #107/#108 remain Review; #109/#110 are also in Review for content review.
- The completion-event fix is deployed at `934cdf6`; Argo CD is Synced/Healthy and Executor 3/3 Ready with zero restarts. Focused tests pass 5/5. Canary #109 completed without a drain and confirmed ordinary result publication on this code. Canary #110 verified publication during drain, but its answer contained incorrect timeout ordering and an unsupported claim that a script had verified constraints despite the no-tools instruction; lifecycle success does not establish answer-quality success.
- The full worker suite exposed two Langfuse identity-isolation failures: the plugin's missing-session path fell back to process-wide Kanzo IDs. The fallback is removed; all 42 worker tests pass. Commit `923ba3c` is deployed and Argo CD is Synced/Healthy, Executor 3/3 Ready; the installed researcher plugin reads `bound_client_env` and contains no process-ID fallback. A post-fix Langfuse trace canary is still needed. Process inspection found one likely orphaned Windows Hermes CLI process (PID 19500, exited parent PID 15492) and two idle WSL bash shells; no live WSL TUI/tmux/screen process was found and none was terminated.

- 활성 모델 응답 중 정상 drain, restart 후 단일 terminal outcome, prompt 재실행 없음 확인
- 강제 종료 상황에서 child process 정리와 Gateway admission reconciliation 확인
- 동시 claim, reconnect/idempotency, 장기 작업 handoff 확인
- ReadOnly 실행 중 skill/memory 학습 보존 및 delegated child의 lease 경계 확인
- 같은 task/model/prompt 조건으로 end-to-end latency, token, CPU/memory/GPU 비교 기준 수립

활성 모델 응답을 일부러 중단하는 canary는 하지 않았다. 사용자 소유 Kanzo 카드는 임의로 완료 처리하지 않는다. 확인된 token 낭비 패턴은 과거 상세 로그를 매번 읽는 것이므로, 상태 요약과 날짜별 증거를 분리했다.

## Progress in this turn

- Integrated `human-agent-report` into the Gateway executor's persistent Hermes homes and final-comment prompt. It applies only to human-facing final Kanzo reports; it adds no model rewrite call. Worker suite passed 45/45, skill tests 6/6, Python compilation and Kustomize rendering passed.
- Completed the session-bound Langfuse user attribution change with the pseudonymous ID `kanzo-owner`; live trace verification and rollout are pending.
- At the usage-limit interruption, work had already reached the post-drain Langfuse audit: run `test-1#111` proved trace/session/run attribution, but the Langfuse observations had an empty `userId`. The immediate task was to bind a user ID per Gateway session, then verify a new trace. Remaining roadmap work is summarized in `kanzo-hermes-evidence-log.md` and the design SSOT `kanzo-hermes-roadmap.md`; the live rollout and user-ID canary remain immediate.

## Latest canary findings

- `test-1#112` verified the skill is registered and can be loaded, but exposed a bad literal file path and unnecessary report-file/lint work. Langfuse counted 11 generations for a simple comment (27 total observations, 3 skill views, 2 file reads, plus temporary artifact and lint operations). The comment was still too verbose and confused earlier test results with checks performed during the run.
- A prompt-only correction now calls the skill once by its Hermes name, requests inline compose, skips files/lint for a normal Kanzo comment, and marks prior results as prior evidence. It passed the worker suite 46/46 but has not been rolled out.
- Langfuse user tracking now passed live: 27 observations in one trace for run `c19740fe3a57`, all with `userId=kanzo-owner`.

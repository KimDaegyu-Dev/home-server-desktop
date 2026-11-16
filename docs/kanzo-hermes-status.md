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
- Live canaries #98, #101, and #103 consistently ran the child terminal command(s) successfully, then failed on the parent broker call with a missing mapping. The broker now distinguishes `session_mapping_not_found` from `session_mapping_ambiguous` and logs short SHA-256 fingerprints instead of raw session IDs.
- Fingerprint comparison in #103 proves the post-child parent lookup used the **child's session ID** after its alias was revoked; it did not use the bound parent session ID. This confirms stale task-local `HERMES_SESSION_ID` after `subagent_stop` as the cause.
- The worker generated backlog proposals #99/#100 and human action card #102; #102 requires the parent mapping to be restored and a passing canary. A task result also understated retries: the Hermes terminal wrapper retried each deterministic 404 three times internally even though the worker described one tool attempt. Account for wrapper-level retries when reviewing worker reports.
- A fix is now prepared in the `subagent_stop` hook: after broker revocation succeeds, restore only the task-local Hermes session ContextVar to the parent ID. It deliberately avoids the public setter because that also mutates process-wide `os.environ`. Four focused plugin tests pass. Rollout and a fresh delegated-child/parent canary are still required.

- 활성 모델 응답 중 정상 drain, restart 후 단일 terminal outcome, prompt 재실행 없음 확인
- 강제 종료 상황에서 child process 정리와 Gateway admission reconciliation 확인
- 동시 claim, reconnect/idempotency, 장기 작업 handoff 확인
- ReadOnly 실행 중 skill/memory 학습 보존 및 delegated child의 lease 경계 확인
- 같은 task/model/prompt 조건으로 end-to-end latency, token, CPU/memory/GPU 비교 기준 수립

활성 모델 응답을 일부러 중단하는 canary는 하지 않았다. 사용자 소유 Kanzo 카드는 임의로 완료 처리하지 않는다. 확인된 token 낭비 패턴은 과거 상세 로그를 매번 읽는 것이므로, 상태 요약과 날짜별 증거를 분리했다.

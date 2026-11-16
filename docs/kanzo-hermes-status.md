# Kanzo / Hermes 진행 상태

설계와 완료 기준의 SSOT는 [Kanzo / Hermes 로드맵](kanzo-hermes-roadmap.md)이다. 날짜별 근거는 [상세 증거 로그](kanzo-hermes-evidence-log.md)에 보존한다. 이 문서는 최신 확인 상태와 미완료 작업만 요약한다.

## 현재 확인 상태 — 2026-11-16

- GitOps 저장소 main에 graceful-shutdown 수정이 반영됐다. Argo CD kanzo-agent-worker는 Synced/Healthy이며, Executor 3/3과 Dispatcher 2/2 Ready다.
- Dispatcher는 실행 중 작업을 비우고 종료한다. Executor API는 SIGTERM 이후 새 제출을 막고, 이미 받아들인 제출은 최대 2,760초 drain한다. Gateway drain은 2,820초, Executor Pod 종료 유예는 3,300초다. 실행 중인 작업을 직접 끊지 않는 설정으로 배포됐지만, active-turn 중 drain은 아직 실증하지 않았다.
- 현재 Dispatcher ledger에는 실행 중인 run이 없다. 상태는 Review 37건, Waiting 6건이다. #92–#96은 사용자가 판단할 수 있도록 Review에 그대로 둔다.
- Hermes PM 검사는 깨끗하다: activate()는 빈 목록, venv_is_current()는 True다. OMH 4.0.7은 Hermes venv와 분리되어 있고 Hermes CLI는 198개 스킬을 표시한다.
- 기존 researcher 스킬 파일은 Executor 두 차례 재시작 전후 해시가 일치했다. 이는 디스크 보존과 CLI 검색 가능성의 증거이며 모델이 실제 작업에서 지침을 적용했음을 증명하지는 않는다.

## Phase 6에서 남은 검증

## Current verified state   2026-11-16

- Worker runtime revision 5d2cf6e is Argo Synced/Healthy; Executor 3/3 Ready. The following docs-only GitOps commit fcaaedb does not alter runtime.
- The human-agent-report ZIP is installed in the persistent default, researcher, builder, and reviewer Hermes homes. After rollout, Hermes lists it as an enabled local skill in researcher.
- Ordinary final Kanzo comments use the skill's evidence and readability rules embedded in the worker prompt; the full skill is reserved for substantial reports/audits. Routine output targets 1,200 characters and skips report files/lint.
- Worker suite: 47/47; report-skill suite: 6/6. Python compile and cluster Kustomize render passed. The final prompt change is code/test/rollout verified, not live-output-canary verified.
- Langfuse user attribution passed live in run c19740fe3a57: 27 observations in one trace and each had userId=kanzo-owner. This is a stable pseudonymous owner ID because Kanzo actor identity is absent from the worker task contract.
- Report canaries #112 and #114 are archived done; duplicate proposal #113 is archived with declined and its reason; temporary test-1 catalog activation #23 is restored to done. #107/#108 and #109/#110 remain Review as recorded in the evidence log.

## Verified token-cost findings

- The first simple report canary generated 11 LLM calls and 27 observations; it also made two read_file calls, three skill views, wrote temporary report/facts files, and ran the linter.
- The next report canary generated 15 LLM calls and 44 observations because its task explicitly requested live cluster verification and triggered repeated MCP discovery. These were test canaries, not production report averages.
- We removed routine skill reload, artifact generation, and lint from the common comment path. No third LLM canary was run to avoid more test cost. A matched production-run baseline is still needed before claiming a measured reduction.

## Roadmap work still open

- Exercise active-generation graceful drain/restart, terminal outcome publication, and no prompt replay together.
- Verify child-process cleanup and Gateway admission reconciliation under forced termination.
- Verify ReadOnly learning/memory persistence across Pod replacement and delegated child lease boundaries.
- Reconcile Kanzo, executor ledger, artifacts, and Langfuse on success/failure/cancel/timeout; check generation deduplication and delegated trace links (#48/#49 remain backlog).
- Establish matched-run latency, input/output token, CPU/memory/GPU baselines and verify learning reuse. See the design SSOT in kanzo-hermes-roadmap.md and dated evidence in kanzo-hermes-evidence-log.md.

## Process cleanup follow-up

- Added Linux tests for actual Executor API timeout cleanup with a SIGTERM-ignoring descendant and ReadOnly executor termination with a child process. Both pass; Windows skips the two POSIX-only cases.
- Tests caught stdout/stderr pipe handles leaking from executor helper execution. `executor_api._run_helper` now closes both streams in `finally`. Tests with `ResourceWarning` elevated to errors pass.
- Full suite: 49/49 on WSL/Linux and Windows (2 skipped on Windows). Runtime forced-termination and Gateway-admission reconciliation are still outstanding.

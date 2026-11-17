# Kanzo / Hermes 설계 구현 상태

SSOT는 [작업 공간 설계 로드맵](kanzo-hermes-roadmap.md)이다. 이 문서는 구현·검증·배포 진행만 기록한다. 배포된 워커 동작은 [워커 안내](kanzo-agent-worker.md)를 참조한다.

## 현재 상태 — 2026-11-17

- 새 로드맵으로 기존 로드맵을 대체했다. 최신 합의는 태스크마다 worktree를 만들지 않고, 하나의 지속형 Workspace를 여러 연결 태스크가 순차 공유하는 것이다. Workspace는 절대경로와 runner/OS를 기록하고, PR 병합 및 연결 태스크 종료가 검증되면 작업 파일과 브랜치를 자동 삭제한다.
- Kanzo 변경은 private downstream 저장소에서 유지하고 공식 Kanzo 저장소를 `upstream` remote로 연결한다. 병합된 Workspace의 원격 브랜치와 로컬 파일은 정리 조건이 충족되면 삭제하며, 별도 workspace archive 없이 tombstone metadata만 남긴다.
- 현재 배포는 GitOps revision `6891d36ee9a1edf3322eb645923fbf5113929744` 기준으로 Argo CD `kanzo`와 `kanzo-agent-worker`가 모두 Synced/Healthy다. Kanzo 이미지는 `localhost/home-server/kanzo:0.4.2-agent.4`이며 dispatcher는 ConfigMap 코드와 `/workspace`의 persistent PVC를 사용한다.
- task-level repo/ref override와 pinned checkout SHA 기록이 배포됐다. task 본문 structured `agent_config`의 `repo`, `ref`, `workspace_id`를 프로젝트 기본값에 병합한다.
- dispatcher의 공유 Workspace registry는 `/opt/worker-state/state/workspace-registry.sqlite3`에 생성됐고 `/workspace` PVC `kanzo-agent-workspaces`는 32Gi RWO Bound다. Worker Pod는 2/2 Running, `kanzo_worker_task_active`는 0이다.
- 현재 dispatcher 한 개가 task를 순차 claim하고 Workspace별 SQLite lease도 적용한다. 재시작 뒤 이전 run이 소유하던 lease만 `conflict`로 풀며 checkout 파일은 보존한다.
- 로컬 호스트 경로는 Kanzo에 alias가 아닌 사용자가 지정한 절대경로로 저장·표시한다. Windows와 WSL은 별도 runner namespace이며, 실행 전에 경로와 Git root를 검증한다. `C:\` 전체를 Pod에 mount하지 않고 host runner를 사용한다.

## 단계 진행

| 단계 | 상태 | 근거/다음 작업 |
|---|---|---|
| 0. 소스 라우팅 정확성 | 구현·배포·local-source canary 완료 | task-level repo/ref override, source SHA ledger 기록, Kanzo `AgentConfig` 필드를 배포했고 Babmukdang WSL local-source canary에서 저장소·고정 SHA·runner를 대조했다. |
| 1. 공유 가능한 지속형 Git Workspace | 구현·배포 완료, task canary 대기 | SQLite registry, 여러 task 연결, 단일 writer lease와 안정 checkout 재사용을 배포했다. `/workspace` PVC가 Bound이고 DB 파일이 생성됐다. 여러 task의 실제 순차 공유/Pod 재시작 뒤 파일 유지 acceptance는 live canary로 확인해야 한다. |
| 2. 정지·재개·병합 후 정리 | 구현·배포 완료, merge lifecycle canary 대기 | waiting/review/취소 중 Workspace 보존, same branch 재개, stale lease 복구, verified merge state 기록 후 branch/file 삭제 코드를 배포했다. 실제 merged PR·완료 task를 통한 자동 정리 acceptance는 아직 실행하지 않았다. |
| 3. Windows/WSL 로컬 저장소 | 구현·배포, read-only local-source canary 완료 | 절대경로 + OS/runner 종류를 Kanzo에 저장하고 host runner가 workspace별 worktree를 관리한다. WSL route, 이벤트 claim, source SHA 전달, read-only snapshot 검증 완료. checkpoint/resume와 runner 중단 복구는 남았다. |
| 4. 다중 저장소·운영 검증 | 구현 태스크 준비, 재개 대기 | source manifest 구현을 Kanzo #140으로 시작했으나 `coder`의 unsupported reasoning-effort 오류로 코드 실행 전에 review로 종료됐다. 프로젝트 기본 coding model은 `iq_3s/high`로 수정했다. 자동 재시도 없이 사람의 재개 결정이 필요하다. |

## 이번 구현 세션

- 시작 revision: `6a22398` (`main`, 작업 트리 clean).
- 새 로드맵과 상태 문서를 작성했다.
- 구현: `worker.py`에서 구조화 task `agent_config` 또는 legacy `repo:`/`ref:` 줄의 override를 허용하고 프로젝트 설정과 병합한다. 저장소 식별자/ref는 검증하며, checkout된 기준 SHA와 source repo/ref를 RunLedger에 기록한다. 요청한 ref가 저장소에서 commit으로 해석되지 않으면 Hermes 실행 전에 실패한다.
- Kanzo routing patch의 카탈로그 폼과 `AgentConfig` schema에 source ref를 추가했다. Kanzo task-level structured config에서도 `repo`/`source_ref`를 유지할 수 있다.
- 설계 결정(2026-11-17): 절대경로를 Kanzo에 직접 저장·표시하고 OS/runner namespace를 함께 둔다. Workspace는 여러 태스크가 공유할 수 있으며, 같은 workspace의 writer는 순차 실행한다. 병합 전/미완료 연결 task가 있으면 유지하고, PR merge와 clean 조건이 모두 검증된 뒤 파일·브랜치를 자동 삭제하며 메타데이터만 남긴다.
- 구현: persistent workspace cleanup은 PR merge와 모든 linked task의 `done`, workspace lease 해제, 로컬 HEAD가 PR head와 동일, clean worktree를 요구한다. 검증된 merge state를 branch/file 삭제 전에 저장하여 중단 뒤 멱등적으로 재개한다. 이미 원격 branch가 사라진 경우도 완료할 수 있다. Dispatcher 재시작 시 살아남은 lease는 중단된 run ID와 일치하는 경우에만 `conflict`로 풀고 checkout을 보존한다.
- 검증: `python -m unittest discover -s . -p 'test_*.py'` (`platform/kanzo-agent-worker`) — 88 tests passed, 3 skipped. Worker와 Kanzo Kustomize 렌더링, pinned Kanzo v0.4.2 routing/MCP patches 적용, Rust release build, K3s image import 성공. Kanzo `0.4.2-agent.4` 이미지가 배포됐고, Argo 앱 둘 다 revision `6891d36`에서 Healthy다.
- live 검증: worker Pod 2/2 Running, workspace registry SQLite 파일 4096 bytes 생성, PVC 32Gi RWO Bound, `kanzo_worker_task_active=0`. Kanzo catalog의 Doing 프로젝트는 `#22` Hermes Harness Lab과 `#123` Babmukdang뿐이다. `test-1`에는 Todo task가 없어서 기존 사용자 업무를 건드리지 않으려고 실행 canary를 만들지 않았다. 따라서 actual linked-task reuse와 merged-PR cleanup은 아직 end-to-end 검증하지 않았다.
- 배포 중 발견·수정: `local-path`는 `WaitForFirstConsumer`이므로 PVC와 dispatcher Deployment를 같은 Argo sync wave `1`에 두어야 한다. 추가된 `workspace_registry.py`도 ConfigMap뿐 아니라 개별 `subPath` mount에 등록해야 한다. routing patch의 CRLF와 중복 workspace UI/schema hunks는 WSL `git apply`/Rust build를 깨뜨렸고, patch를 pinned upstream diff에서 재생성하고 LF로 고친 뒤 build가 통과했다. `.gitattributes`에 `*.patch text eol=lf`를 추가했고 반복 방지 절차를 ai-memory profile에 저장했다.
- GitOps changes are pushed to public `home-server-desktop` main. Created private downstream `https://github.com/KimDaegyu-Dev/kanzo-downstream`; configured its local clone with private `origin` and official `emanuele-em/kanzo` as `upstream`; pushed custom changes as `2c4bb60`. The live Kanzo image remains `0.4.2-agent.4`, built from pinned public v0.4.2 plus tracked GitOps patches. The 32Gi workspace PVC is a request, not a local-path hard quota. Existing worker-state metrics report backing filesystem capacity (~1007 GiB / used 128 GiB / available 827 GiB).
- 작업 전 live 상태를 확인해 기존 태스크나 실행을 임의로 재시작하지 않는다. 실행 중인 run이 있으면 source routing 변경은 적용하지 않고 기록한다.

## 검증 기록

| 날짜 | 검증 | 결과 |
|---|---|---|
| 2026-11-17 | worker route 전달 경로 정적 확인 | task-level repo override 미지원, project route만 전달됨을 확인 |
| 2026-11-17 | worker workspace 배포 설정 정적 확인 | `/workspace`는 `emptyDir`; task 완료 경로에서 checkout 제거 확인 |
| 2026-11-17 | source route override/ref regression suite | 78 passed, 3 skipped |
| 2026-11-17 | Kanzo routing patch parser / whitespace check | valid; `git diff --check` clean |
| 2026-11-17 | Kanzo v0.4.2 pinned source patch application and build | both patches applied; Rust release image `0.4.2-agent.4` built and imported |
| 2026-11-17 | Persistent Workspace registry and cleanup recovery | 87 passed, 3 skipped; stale lease and merge-before-delete recovery covered; Kustomize render successful |
| 2026-11-17 | GitOps rollout | revision `6891d36` Synced/Healthy; Kanzo and worker apps healthy; 32Gi workspace PVC Bound; worker 2/2 Running; registry DB created |
| 2026-11-17 | Prometheus disk/PVC metric | node filesystem available ~827 GiB; local-path PVC metric has same backing-filesystem headroom and is not treated as a PVC quota |
| 2026-11-17 | Kanzo doing cards and worker activity | catalog projects `#22` and `#123` are doing; no other doing cards were returned. Worker active gauge was `0`; execution ledger rows were not queried. |

## 변경 기록 규칙

- 단계 경계가 바뀌거나 검증 결과/배포 revision이 생길 때만 이 문서를 갱신한다.
- 구현되지 않은 설계는 완료로 표시하지 않는다.
- 테스트는 실행한 정확한 명령과 결과를, rollout은 GitOps revision 및 Argo CD 상태를 기록한다.

## Follow-up evidence (2026-11-17)

- Worker suite: 88 passed, 3 skipped.
- A local bare-Git integration test confirms linked tasks reuse the same dirty checkout and branch, preserving tracked edits and an untracked checkpoint. This does not replace a live PVC/Pod restart canary.
- Private Kanzo downstream created and pushed at commit `2c4bb60`; remote topology verified.
- Live PVC restart canary: wrote a temporary marker in the mounted workspace PVC, restarted the dispatcher, read the marker from the replacement Pod (`kanzo-agent-dispatcher-55d4c69dc4-22w78`, 2/2 Running), and removed it. This proves PVC file survival across Pod replacement; no Kanzo task was claimed or changed.

## Follow-up evidence (2026-11-18)

- Phase 3 host-runner routing is implemented in GitOps: catalog routes now carry `source_kind`, absolute `source_path`, `runner_id`, and `runner_os`; the K3s worker only claims routes for its own runner ID. The WSL launcher uses the same event-driven worker and requires credentials through its environment; no secrets were copied into Git.
- Kanzo v0.4.2 image `0.4.2-agent.5` was built from pinned upstream revision `14247076a3e976178f3a50094912d15d1a4f7462` after applying all three tracked patches in order, then imported to the K3s image store. The Kanzo Deployment is 1/1 Running on this image.
- GitOps commits: `a466137` adds WSL host-runner routing and tailnet ingresses; `68eb8d0` allows only Tailscale-managed pods to reach the Executor API port through the Executor ingress policy. ArgoCD reports Kanzo, worker, and sandbox Synced/Healthy (worker revision `68eb8d0`).
- Verification: worker unit suite 94 passed / 3 skipped; worker and sandbox Kustomize renders succeeded; launcher `bash -n` passed; local executor service and external `https://hermes-executor.tail5389e9.ts.net/readyz` both returned `{"status":"ready"}`; broker HTTPS health endpoint returned `{"status":"ok","execution":"per-run-pod"}`.
- The first external Executor readiness request returned 502. Tailscale proxy logs showed connection refused because `kanzo-agent-executor-ingress` allowed only dispatcher traffic on port 8766 and Tailscale traffic on 9119. Adding port 8766 for Tailscale-managed pods fixed it; the external readiness request now returns HTTP 200.
- Patch byte check: `kanzo-local-runner-fields.patch` has 0 CRLF sequences and 0 NUL bytes. The new local-source linked-workspace integration verifies that repeated task runs reuse one dirty checkout/branch while leaving the configured source root clean.
- At the time of this rollout, WSL canary execution and event wake-up were still pending; see the later canary evidence below.

## Follow-up evidence (2026-11-18, operations metrics)

- Added bounded workspace registry metrics for lifecycle counts, active writer leases, dirty workspaces, registry readability, Executor readiness, and worker runner identity. Workspace IDs are not Prometheus labels. Added a `Kanzo Workspace Operations` Grafana dashboard. Host-runner metrics remain loopback-only and are intentionally not scraped remotely pending an authenticated metrics design.
- Babmukdang source check from WSL found `/mnt/c/Github/Babmukdang` has many modified and untracked files and its current branch is 31 commits behind origin. The runner's clean-source guard correctly makes this path ineligible for a local-source canary. No source files were changed.
- Worker suite after metrics changes: 95 passed, 3 skipped. Worker Kustomize render, launcher shell syntax, and `git diff --check` passed. Kanzo patch byte check remains CRLF=0 and NUL=0.
- The metrics rollout is live at revision `46c6f3f`: ArgoCD Synced/Healthy, dispatcher 2/2 Ready, `kanzo-workspace-dashboard` present in the monitoring namespace. Live `/metrics` showed runner `k3s/linux`, Executor ready=1, workspace registry ready=1, writer leases=0, and dirty workspaces=0. Active task gauge was 0 before rollout.
- Grafana's Prometheus datasource returned the live `kanzo_worker_runner_info`, `kanzo_worker_workspace_registry_ready`, and all bounded `kanzo_worker_workspaces{state}` series for the new dispatcher pod.
- Grafana API confirms dashboard UID `kanzo-workspace-operations` is discovered with all six expected workspace/runner panels.

## Follow-up evidence (2026-11-18, runner heartbeat)

- Added an authenticated runner heartbeat from each worker to the sandbox broker. The broker keeps a bounded runner registry, expires stale heartbeats after 90 seconds, and exposes `kanzo_runner_up` and last-seen metrics. Added a ServiceMonitor and changed the Grafana runner panel to use the broker's live runner state.
- Worker suite: 96 passed, 3 skipped; sandbox broker suite: 11 passed. Worker and sandbox Kustomize renders succeeded, and `git diff --check` passed. Changes were committed and pushed as `2e54302`.
- Live rollout verified: `kanzo-agent-worker` and `kanzo-sandbox` are Synced/Healthy; dispatcher is 2/2 Running. Broker `/metrics` reports `runner_id="k3s", runner_os="linux"` with `kanzo_runner_up=1`; a direct Prometheus query returns the same series with value `1` from the broker ServiceMonitor.
- During rollout the old dispatcher reported `kanzo_worker_task_active=0`. It drained and exited without force deletion; the replacement worker became healthy. This confirms safe rollout for an idle worker, not yet a rollout during an active task.
- At the time of this runner-heartbeat check, WSL was not launched and the main Babmukdang checkout was dirty. A clean isolated WSL worktree was later used for the local-source canary below. Multi-repository execution and end-to-end WSL checkpoint/resume remain open.

## Follow-up evidence (2026-11-18, WSL local-source canary)

- GitOps commit `849fab3` passes the source checkout's verified commit as non-secret `KANZO_SOURCE_GIT_SHA` to sandbox terminal commands. `/workspace` remains a read-only snapshot without `.git`; the sandbox Apps are Synced/Healthy at this revision.
- WSL runner claimed `babmukdang#136` through the event path (run `5b8f84b8f117`). It reported the expected SHA `a51a7a539d98bd17142bec752e61de8c58bf75ed`, confirmed the mount was read-only, and found identical pre/post SHA-256 manifests for all 245 files. No files or tests were changed/run. The source worktree remained clean.
- RunLedger recorded one ended Langfuse root trace (`a1e407ec4d0ee4029ba35a8964ab197a`). The report's test command was corrected from the checked-in CI workflow to `npm ci && npm test -- --passWithNoTests --forceExit`; this command was not run in the canary.
- Read-only tasks intentionally finish in `review`; the evidence was reviewed, the result was approved, and #136 moved to `done`. Human action #138 is also done. The temporary source worktree was removed after verifying it was clean, and project catalog #123 was restored to the normal Git source defaults.
- Still open: Phase 1 multi-task shared-workspace/Pod-restart canary; Phase 2 merged-PR cleanup canary; Phase 3 WSL checkpoint/resume and runner-offline recovery; Phase 4 multi-repository manifest and operational evidence.

## Follow-up evidence (2026-11-18, WSL source hygiene and model route)

- Windows Git `core.autocrlf=true` left the mounted `C:\Github\home-server-desktop` worktree looking CRLF-dirty to WSL (index LF, worktree CRLF across many files). It was not used as a local source. A clean WSL ext4 clone at `/home/eorbr/kanzo-sources/home-server-desktop` is pinned to `84e7150194691ff75b5afd1bcc69b5d1ec731e67`; `git status` is clean and the origin matches GitHub.
- Kanzo project #139 and Kanzo project slug `home-server-desktop` were created for the remaining implementation. Persistent workspace `kanzo-multirepo-manifest` was acquired by task #140 on the event path; its checkout is clean at the pinned SHA.
- Task #140 run `820b4b3c857b` failed before source work: Hermes sent `reasoning.effort=high` to LiteLLM model group `coder`, which returned HTTP 400 `UnsupportedParamsError` because `supports_reasoning` is not declared. No Langfuse root trace was produced. Project coding route changed to verified `iq_3s/high`; no automatic retry occurred. #140 is now Todo waiting on human approval card #141; completing #141 will unblock the event worker.

## Follow-up evidence (2026-11-18, merged workspace cleanup review)

- Review branch fix/conditional-merged-branch-cleanup adds compare-and-delete using Git force-with-lease, so a remote branch that moves after merge verification is preserved. Merged workspaces are also blocked from new leases while cleanup is pending; cleanup can resume after worker restart.
- Implementation commits: 9651a7f, 36134fa, 2515c53, 0ed1c7b, and offline-routing test commit 729a636. Worker suite: 111 tests passed; sandbox broker suite: 11 tests passed. git diff --check passed; changed Python files are LF in both index and worktree.
- Worker restart reconciliation now confirms cancellation or terminal state for the exact Gateway run before releasing its workspace lease or routing it to review. If confirmation fails, the run stays orphaned and blocks new claims. The Executor API atomically cancels not-yet-submitted runs so a prompt cannot race past recovery.
- Commit 729a636 adds an offline-routing regression: the K3s worker does not claim a project assigned to wsl-home and leaves its Todo card untouched. Broker heartbeat tests report wsl-home offline after 90 seconds without a heartbeat. These are local tests, not a live WSL shutdown canary.
- Draft PR #1 is open at https://github.com/KimDaegyu-Dev/home-server-desktop/pull/1. It has not been merged or deployed. The live cluster remains on the previously verified revision; a real merged-PR cleanup canary is still open.

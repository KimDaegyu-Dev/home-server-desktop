# Kanzo / Hermes 설계 구현 상태

SSOT는 [작업 공간 설계 로드맵](kanzo-hermes-roadmap.md)이다. 이 문서는 구현·검증·배포 진행만 기록한다. 배포된 워커 동작은 [워커 안내](kanzo-agent-worker.md)를 참조한다.

## 현재 상태 — 2026-11-17

- 새 로드맵으로 기존 로드맵을 대체했다. 최신 합의는 태스크마다 worktree를 만들지 않고, 하나의 지속형 Workspace를 여러 연결 태스크가 순차 공유하는 것이다. Workspace는 절대경로와 runner/OS를 기록하고, PR 병합 및 연결 태스크 종료가 검증되면 작업 파일과 브랜치를 자동 삭제한다.
- Kanzo 변경은 private downstream 저장소에서 유지하고 공식 Kanzo 저장소를 `upstream` remote로 연결한다. 병합된 Workspace의 원격 브랜치와 로컬 파일은 정리 조건이 충족되면 삭제하며, 별도 workspace archive 없이 tombstone metadata만 남긴다.
- 배포된 worker는 Kanzo task마다 `gh repo clone`으로 새 checkout을 만들고 run 종료 때 이를 지운다. Pod workspace는 `emptyDir`이며 dispatcher ledger만 별도 PVC에 저장된다. 사람 대기 후 재실행은 prompt와 Kanzo 댓글에서 시작하고 미커밋 파일은 보존되지 않는다.
- 배포된 worker의 확인된 버그: 프로젝트 카탈로그의 repo만 `Task.route`로 전달하고 태스크 본문의 repo override는 읽지 않는다. 따라서 task가 다른 repo를 명시해도 카탈로그 기본 repo를 checkout한다. 수정은 로컬 작업 트리에 있지만 아직 배포되지 않았다.
- 배포된 worker의 확인된 한계: 프로젝트 route에 source ref가 없고 clone의 기본 revision을 쓴다. 실제 checkout SHA는 ledger에 기록하지만 사용자가 지정한 ref와의 일치 여부는 보장하지 않는다. ref 지원 수정은 로컬 작업 트리에 있지만 아직 배포되지 않았다.
- 현재 워커는 단일 dispatcher 안에서 task를 순차 claim한다. persistent workspace로 전환할 때도 이를 이용해 기본 동시 writer를 1로 유지하되, 별도 lease/재시작 reconciliation은 추가해야 한다.
- 로컬 호스트 경로는 Kanzo에 alias가 아닌 사용자가 지정한 절대경로로 저장·표시한다. Windows와 WSL은 별도 runner namespace이며, 실행 전에 경로와 Git root를 검증한다. `C:\` 전체를 Pod에 mount하지 않고 host runner를 사용한다.

## 단계 진행

| 단계 | 상태 | 근거/다음 작업 |
|---|---|---|
| 0. 소스 라우팅 정확성 | 구현 완료, 배포 대기 | worker가 task-level repo/ref를 프로젝트 기본값과 병합하고 지정 ref를 checkout해 SHA를 기록한다. Kanzo 카탈로그 폼/MCP `AgentConfig`에 source ref 필드를 추가했다. 코드 검증은 통과했으며 GitOps 반영과 live rollout, Babmukdang source 정합 확인이 남았다. |
| 1. 공유 가능한 지속형 Git Workspace | 로컬 구현·검증 완료, 배포 대기 | Workspace ID/다중 task 연결/단일 writer lease를 SQLite registry에 기록하고 `/workspace` PVC에서 안정 checkout을 재사용하도록 구현했다. 재시작 시 장부에서 workspace_id를 찾아 중단된 run의 stale lease만 해제하고 파일은 보존한다. 새 `workspace_registry.py` ConfigMap과 32Gi 요청 PVC를 Kustomize에 연결했다. |
| 2. 정지·재개·병합 후 정리 | 로컬 구현 중, 배포 대기 | waiting/review/취소 중 Workspace를 보존하고 같은 브랜치에서 이어간다. merge와 연결 task 완료 및 clean head를 확인한 뒤 tombstone을 먼저 기록해, branch/file 정리 중 dispatcher 재시작 시 다음 주기에 이어서 정리하게 했다. 실클러스터 lifecycle 검증은 남았다. |
| 3. Windows/WSL 로컬 저장소 | 설계 확정 | 절대경로 + OS/runner 종류를 Kanzo에 저장하고, host runner가 workspace별 worktree를 관리한다. 인증 outbound 연결과 원본 dirty 보호가 필요하다. |
| 4. 다중 저장소·운영 검증 | 대기 | source manifest, 부분 성공 추적, 용량/lease/복구/자동 정리 관측을 추가한다. |

## 이번 구현 세션

- 시작 revision: `6a22398` (`main`, 작업 트리 clean).
- 새 로드맵과 상태 문서를 작성했다.
- 구현: `worker.py`에서 구조화 task `agent_config` 또는 legacy `repo:`/`ref:` 줄의 override를 허용하고 프로젝트 설정과 병합한다. 저장소 식별자/ref는 검증하며, checkout된 기준 SHA와 source repo/ref를 RunLedger에 기록한다. 요청한 ref가 저장소에서 commit으로 해석되지 않으면 Hermes 실행 전에 실패한다.
- Kanzo routing patch의 카탈로그 폼과 `AgentConfig` schema에 source ref를 추가했다. Kanzo task-level structured config에서도 `repo`/`source_ref`를 유지할 수 있다.
- 설계 결정(2026-11-17): 절대경로를 Kanzo에 직접 저장·표시하고 OS/runner namespace를 함께 둔다. Workspace는 여러 태스크가 공유할 수 있으며, 같은 workspace의 writer는 순차 실행한다. 병합 전/미완료 연결 task가 있으면 유지하고, PR merge와 clean 조건이 모두 검증된 뒤 파일·브랜치를 자동 삭제하며 메타데이터만 남긴다.
- 구현: persistent workspace cleanup은 PR merge와 모든 linked task의 `done`, workspace lease 해제, 로컬 HEAD가 PR head와 동일, clean worktree를 요구한다. 검증된 merge state를 branch/file 삭제 전에 저장하여 중단 뒤 멱등적으로 재개한다. 이미 원격 branch가 사라진 경우도 완료할 수 있다. Dispatcher 재시작 시 살아남은 lease는 중단된 run ID와 일치하는 경우에만 `conflict`로 풀고 checkout을 보존한다.
- 검증: `python -m unittest discover -s . -p 'test_*.py'` (`platform/kanzo-agent-worker`) — 87 tests passed, 3 skipped. `kubectl kustomize platform/kanzo-agent-worker` 렌더링 성공. Kanzo v0.4.2 pinned commit(`14247076a3e976178f3a50094912d15d1a4f7462`)에 routing patch와 MCP annotations patch를 순서대로 실제 적용해 성공했다. `git diff --check` — content errors 없음(Windows working-copy LF/CRLF 경고 출력).
- 아직 GitOps에 push하거나 클러스터에 배포하지 않았다. 현재 `home-server-desktop`의 `origin`은 public GitHub repository여서 이 미검토 변경을 push하지 않았다. WSL의 현재 사용자로 `k3s kubectl`을 호출하면 `/etc/rancher/k3s/k3s.yaml` 권한 거부가 나서 Argo CD/PVC live 상태를 확인하지 못했다. 2026-11-17 Prometheus 조회에서 노드 `/` 여유 공간은 약 827 GiB, `kanzo-agent-worker-state` kubelet volume metric은 capacity 약 1007 GiB / used 128 GiB / available 827 GiB였다. 단 PVC manifest 요청은 1 GiB이고 `local-path`의 metric은 실제 PVC quota가 아니라 backing filesystem 통계를 반영하는 것으로 보이므로, 이 수치를 PVC 보장 용량으로 간주하지 않는다.
- 작업 전 live 상태를 확인해 기존 태스크나 실행을 임의로 재시작하지 않는다. 실행 중인 run이 있으면 source routing 변경은 적용하지 않고 기록한다.

## 검증 기록

| 날짜 | 검증 | 결과 |
|---|---|---|
| 2026-11-17 | worker route 전달 경로 정적 확인 | task-level repo override 미지원, project route만 전달됨을 확인 |
| 2026-11-17 | worker workspace 배포 설정 정적 확인 | `/workspace`는 `emptyDir`; task 완료 경로에서 checkout 제거 확인 |
| 2026-11-17 | source route override/ref regression suite | 78 passed, 3 skipped |
| 2026-11-17 | Kanzo routing patch parser / whitespace check | valid; `git diff --check` clean |
| 2026-11-17 | Kanzo v0.4.2 pinned source patch application | routing patch applied; following MCP annotations patch applies cleanly |
| 2026-11-17 | Persistent Workspace registry and cleanup recovery | 87 passed, 3 skipped; stale lease and merge-before-delete recovery covered; Kustomize render successful |
| 2026-11-17 | Prometheus disk/PVC metric | node filesystem available ~827 GiB; local-path PVC metric has same backing-filesystem headroom and is not treated as a PVC quota |
| 2026-11-17 | Kanzo doing cards | catalog projects `#22` and `#123` are doing; no other doing cards were returned. Active executor ledger was not inspected. |

## 변경 기록 규칙

- 단계 경계가 바뀌거나 검증 결과/배포 revision이 생길 때만 이 문서를 갱신한다.
- 구현되지 않은 설계는 완료로 표시하지 않는다.
- 테스트는 실행한 정확한 명령과 결과를, rollout은 GitOps revision 및 Argo CD 상태를 기록한다.

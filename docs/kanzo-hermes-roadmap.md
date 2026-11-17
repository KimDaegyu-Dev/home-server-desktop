# Kanzo / Hermes 작업 공간 설계 로드맵

이 문서는 Kanzo 태스크가 원격 Git 저장소 또는 허용된 로컬 저장소를 안전하고 재개 가능하게 사용하는 방식의 SSOT다. 실행 진척과 배포 revision은 [진행 상태](kanzo-hermes-status.md)에 기록한다. 현재 배포 동작은 [워커 안내](kanzo-agent-worker.md)에 기록한다.

## 목표

작업 공간은 태스크와 독립된 지속형 자원이다. 하나의 Workspace에 같은 변경 흐름에 속한 여러 Kanzo 태스크를 연결하고, 순서대로 같은 브랜치와 파일을 이어서 사용한다. 사용자가 태스크를 멈추거나 사람 결정을 기다리게 해도 작업 파일과 맥락을 잃지 않고 재개해야 한다. PR 병합과 연결된 태스크 종료가 검증되면 파일과 브랜치를 자동 정리하고, Kanzo에는 작은 이력만 남긴다.

Git은 코드 변경의 기본 계약으로 유지한다. 기본 소스 공급자는 GitHub 저장소다. 로컬 Windows/WSL 저장소도 명시적으로 등록한 경로를 대상으로 같은 태스크 소유 작업 공간 계약을 제공한다. 로컬 작업을 위해 Kubernetes Pod에 컴퓨터 전체 파일 시스템을 마운트하지 않는다.

## 설계 결정

- Kanzo 프로젝트 카탈로그는 활성화와 기본 실행 정책을 소유한다. 프로젝트별 배포는 만들지 않는다.
- Kanzo 커스터마이징은 사용자가 관리하는 private downstream 저장소에서 유지하고, 공식 저장소는 `upstream` remote로 연결한다. 변경은 downstream에서 추적하고 필요한 upstream 변경만 선택적으로 가져와 충돌과 업데이트를 관리한다.
- 각 Workspace는 `source kind + repository 또는 절대경로 + runner/OS + ref + resolved SHA`를 시작 전에 기록하고, 출처가 검증되지 않으면 모델을 호출하지 않는다.
- 샌드박스 `/workspace`에는 검증된 소스 스냅샷만 전달하고 `.git` 메타데이터는 노출하지 않는다. 실행 직전 checkout에서 확인한 commit SHA는 `KANZO_SOURCE_GIT_SHA`로 runner 명령에 전달해 근거를 남긴다.
- 태스크의 소스 설정은 프로젝트 기본값을 상속하고 명시된 `repo`/`ref`만 덮어쓴다. 여러 저장소가 필요한 작업은 나중에 태스크에 저장소 목록과 역할을 지정하는 명시적 manifest로 확장한다.
- Workspace는 안정적인 ID와 절대경로를 가진다. 여러 태스크가 하나의 Workspace에 연결될 수 있고, Workspace 하나에는 writer lease 하나만 허용한다. 실행 ID마다 새 checkout을 만들지 않는다.
- 같은 변경 흐름에 속한 태스크만 Workspace를 명시적으로 공유하고 순차 실행한다. 독립 병렬 작업은 별도 Workspace와 브랜치를 사용한다.
- 중단·대기·실패·리뷰 상태에서는 작업 디렉터리와 브랜치를 보존한다. 재개 시 저장소, 브랜치, 커밋, 변경 파일, 마지막 실행과 checkpoint를 먼저 확인한다. 예상하지 못한 변경은 덮어쓰지 않고 review로 멈춘다.
- PR 병합, 연결 태스크 종료, lease 해제, clean worktree를 모두 확인한 뒤 Workspace worktree와 브랜치를 자동 삭제한다. 시간 기반 삭제는 하지 않는다. Kanzo와 RunLedger에는 source, SHA, PR, 종료 결과를 포함한 작은 tombstone만 남긴다.
- Workspace PR이 병합되어 정리 조건을 충족하면 해당 원격 브랜치와 로컬 Workspace 파일을 삭제한다. 별도 보관 사본은 만들지 않는다. 추적에 필요한 종료 metadata만 보존한다.
- 로컬 소스 경로는 alias 대신 사용자가 지정한 절대경로를 그대로 Kanzo에 저장·표시한다. OS/runner 종류도 함께 기록해 Windows 경로와 WSL 경로를 혼동하지 않는다. 경로 allowlist·사용자 권한·명시적 잠금으로 접근을 제한하며, 원본 working tree와 미커밋 변경은 보존한다.
- 로컬 호스트 저장소에 접근하기 위해 Kubernetes Pod에 `C:\` 전체를 mount하지 않는다. 호스트 runner가 절대경로 아래에서 Workspace worktree를 관리한다.
- read-only 실행도 기존 프로젝트 및 Hermes의 메모리·회고·스킬 기능을 유지한다. 코드 저장소 mount와 write 권한만 격리한다.

## 작업 상태 흐름

```text
Kanzo Todo -> workspace 연결/생성 -> task claim + workspace lease
           -> source/ref 검증 -> workspace 생성 또는 재개
           -> Hermes 실행 -> checkpoint + commit/diff/PR
           -> Review 또는 사람 선행 작업 대기 -> 같은 workspace에서 재개
           -> PR 병합 + 모든 연결 task 종료 확인 -> worktree/branch 삭제
           -> Kanzo/RunLedger에 작은 종료 이력 유지
```

Kanzo 이벤트는 깨우기 신호이고 Kanzo 카드와 workspace registry가 진실의 원천이다. 이벤트가 중복되거나 누락되어도 같은 태스크를 두 번 쓰지 않도록 claim과 workspace lease를 원자적으로 조정한다. 실패 자동 재시도는 하지 않는다. 사람이 문제를 해결해 Todo로 돌리면 같은 task workspace에서 명시적으로 재개한다.

## 단계

### 0. 소스 라우팅의 정확성 — 구현·배포·local-source canary 완료

- 프로젝트 카드의 기본 저장소와 ref를 파싱하고 검증한다.
- 태스크 카드의 저장소/ref override를 프로젝트 기본값과 병합한다.
- checkout 전에 원격 저장소와 ref를 확인하고 resolved SHA를 ledger와 Kanzo 댓글에 남긴다.
- Babmukdang처럼 여러 저장소가 섞여 잘못된 저장소를 검사하던 태스크는 올바른 source/ref가 확인되기 전까지 실행하지 않는다.

완료 조건: task-level repo/ref가 실제 checkout과 동일하고, 잘못된 입력이나 존재하지 않는 ref는 모델 호출 전에 명확히 중단된다.

### 1. 공유 가능한 지속형 Git Workspace

- 태스크와 별도인 Workspace ID/레코드를 두고, 여러 Kanzo task가 같은 Workspace에 연결되도록 Kanzo UI와 MCP를 확장한다.
- 현재 dispatcher의 `emptyDir` workspace를 PVC 기반 저장 공간으로 전환하고 용량·inode·가용성 지표를 추가한다.
- source identity, 절대경로 또는 repo, runner, base SHA, branch, worktree path, linked task IDs, lease/run ID, last-known Git status를 가진 원자적 registry를 둔다.
- Workspace별 안정 경로를 재사용하고, 같은 Workspace의 태스크는 순차적으로 같은 브랜치에서 진행한다. 독립 태스크는 별도 Workspace를 사용한다.
- claim에서 workspace lease를 획득하고 종료 시 해제한다. 비정상 종료 뒤에는 lease 만료만으로 dirty workspace를 덮지 못하게 process 및 Git 상태를 reconciliation한다.
- 태스크 상태와 commit/PR 링크를 Kanzo에 기록한다. waiting/review로 가도 workspace는 유지한다.
- 배포 시 `local-path`의 `WaitForFirstConsumer` PVC와 첫 consumer Deployment를 같은 Argo CD sync wave에 둔다. Worker ConfigMap에 추가한 Python 모듈은 개별 `subPath` volume mount에도 등록한다.

완료 조건: 연결된 태스크들이 같은 Workspace를 순차 공유하고, 별도 태스크는 같은 checkout을 동시에 수정할 수 없다. Pod 재시작 후에도 파일·Git 상태가 유지된다.

### 2. 정지·재개·검토 수명주기

- 사람 선행 카드가 완료되면 원 태스크는 새 run ID로 재개하지만 같은 Workspace와 branch를 사용한다.
- run별 로그와 Kanzo 요약을 Workspace에 연결한다. 다음 실행은 마지막 checkpoint와 dirty diff를 확인한다.
- dirty state, stale lease, 원격 branch 이동, 사용자가 수동 수정한 Workspace를 감지해 덮어쓰기 대신 review에 보낸다.
- PR 병합 여부는 GitHub의 실제 상태로 확인한다. Kanzo 카드의 Done만으로 merge를 추정하지 않는다.
- 병합된 PR의 head SHA, 모든 연결 task의 종료 상태, lease 해제, clean worktree를 확인한 뒤 worktree와 브랜치를 삭제하고 tombstone을 기록한다.

완료 조건: 취소, 사람 대기, 워커 재시작, review 전환에서 같은 Workspace로 안전하게 재개되고, 병합 전에는 삭제되지 않으며 완료 후에는 작업 파일과 브랜치가 정리된다.

### 3. 로컬 Windows/WSL 소스 공급자

- Kanzo Workspace 설정에 사용자가 선택한 저장소의 절대경로, OS/WSL 종류, runner ID, 허용 작업을 저장한다. 등록 경로가 없거나 Git root가 바뀌면 실행 전에 중단한다.
- 호스트 runner가 Workspace lease를 받고, 지정 경로에서 Workspace별 Git worktree를 만들며 연결된 태스크가 순차 사용한다.
- 실행 채널은 인증된 outbound 연결을 사용한다. runner는 Kanzo/MCP 작업 권한과 코드 실행 권한을 분리하고 최소 권한으로 동작한다.
- 원본 worktree가 dirty면 자동 stash/reset하지 않는다. 필요하면 별도의 명시적 snapshot/import 흐름을 제공한다.
- 전원 종료나 호스트 오프라인 상태는 큐 상태로 보이며 자동 반복 실행하지 않는다.

완료 조건: 등록된 로컬 repo에서만 실행되고, host offline/권한 거부/충돌이 Kanzo에 명확히 보이며 사용자 원본 파일이 변경되지 않는다.

### 4. 여러 저장소, 로컬·원격 통합과 운영 검증

- 태스크 source manifest로 API/클라이언트 등 여러 repo를 각각 절대경로 또는 repo/ref/SHA로 고정한다.
- 각 저장소의 commit/PR과 Workspace에 연결된 Kanzo 태스크 결과를 연결한다. 부분 성공은 완료로 표시하지 않는다.
- workspace 용량, lease, runner 연결, dirty worktree, merge 검증, 자동 정리 결과를 Grafana/Kanzo에서 확인한다.
- 백업·복구와 dispatcher 장애 복구 절차를 검증하고 GitOps를 통해 점진 배포한다.

완료 조건: 원격 단일 repo, 로컬 단일 repo, 다중 repo 각각의 중단·재개·리뷰·병합·복구 흐름이 증거와 함께 통과한다.

## 명시적으로 하지 않는 것

- 에이전트가 컴퓨터의 임의 파일에 접근하도록 허용하지 않는다.
- 태스크마다 클러스터 서비스나 Hermes 배포를 만들지 않는다.
- dirty workspace, 미병합 브랜치, 미완료 연결 task는 자동 삭제하지 않는다.
- PR 병합과 연결 태스크 종료가 확인된 workspace의 파일과 브랜치는 자동 정리한다. 완료 이력만 보존한다.
- 테스트되지 않은 완료를 선언하거나 실패한 태스크를 자동 재시도하지 않는다.

## 관련 근거

- 현재 배포/실행 계약: [Kanzo Hermes worker](kanzo-agent-worker.md)
- 구현 및 배포 진행: [Kanzo Hermes status](kanzo-hermes-status.md)

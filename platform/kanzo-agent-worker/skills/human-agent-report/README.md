# human-agent-report

Hermes Agent용 인간 친화적 상태 보고서 Skill입니다. `SKILL.md`는 생성/검토/수정 절차를 정의하며, `scripts/report_lint.py`는 정규식과 `facts.json`을 이용한 보조 품질 검사를 수행합니다.

## 설치

압축 파일을 풀어 `human-agent-report/` 폴더 전체를 Hermes Skill 디렉터리로 복사합니다.

```bash
mkdir -p ~/.hermes/skills/productivity
cp -r human-agent-report ~/.hermes/skills/productivity/
hermes skills list
```

그다음 새 세션에서 다음처럼 요청합니다.

```text
/human-agent-report compose 다음 Kanban 실행 기록을 사람이 읽을 보고서로 변환해줘.
/human-agent-report audit 이 보고서의 참조 관계, ID, 검증 상태를 검사해줘.
```

설치 경로와 특정 Hermes 버전의 Skills 인식 방식은 로컬 환경에서 확인해야 합니다. 프로필별 스킬 디렉터리를 별도로 사용하는 경우 해당 프로필에 설치하세요.

## 독립 정적 검사

```bash
python3 human-agent-report/scripts/report_lint.py --report human-agent-report/examples/report.md --facts human-agent-report/examples/facts.json
python3 human-agent-report/scripts/report_lint.py --report human-agent-report/examples/report.md --format json
python3 -m unittest discover -s human-agent-report/tests -v
```

`--strict`를 붙이면 lint 경고/오류가 있을 경우 프로세스가 실패 코드(1)를 반환합니다. 휴리스틱 경고는 오탐 가능성이 있습니다.

## 자동 실행에 대한 주의

Skill은 온디맨드 문서와 스크립트 묶음입니다. 설치만으로 Hermes가 모든 최종 메시지를 가로채 재작성하지 않습니다. 작업 종료 시 자동 보고를 원한다면 사용 중인 Hermes 디스패처나 결과 전달 경로에 명시적으로 연동해야 합니다. 그 단계는 본 패키지에 포함하지 않습니다.

검사 스크립트는 보고서를 수정하지 않고 읽기만 합니다. 민감한 실행 로그를 외부로 전송하지 않습니다. 근거가 정확한지, 실행 성공 여부가 참인지, 의미가 보존되었는지는 별도 확인이 필요합니다.

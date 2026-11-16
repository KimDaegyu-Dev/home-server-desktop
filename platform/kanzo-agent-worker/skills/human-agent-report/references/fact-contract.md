# Fact contract (optional JSON sidecar)

보고서의 중요한 사실을 모델이 임의로 만들어 내지 않게 하는 메모 형식이다. **원본 로그를 대체하는 증거가 아니라 원본을 가리키는 색인**이다.

```json
{
  "report_subject": "Hermes profile narrowing",
  "facts": [
    {
      "id": "F01",
      "summary": "역할별 프로필은 18개에서 13개로 축소하기로 설계했다.",
      "status": "design_decided",
      "critical": true,
      "evidence": ["operator-note:2026-11-16"],
      "required_terms": ["18개", "13개"]
    },
    {
      "id": "F02",
      "summary": "프롬프트 크기 감소량은 재측정하지 않았다.",
      "status": "unverified",
      "critical": true,
      "evidence": [],
      "required_terms": ["재측정"]
    }
  ]
}
```

`status` 값: `proposed`, `design_decided`, `scheduled`, `running`, `completed_unverified`, `verified`, `failed`, `unverified`, `blocked`.

- `verified`: 실제 측정·독립 확인에 대한 `evidence` 항목이 한 개 이상 있어야 한다. 증거 문자열은 참조할 수 있는 위치여야 한다. 스크립트는 그 참조가 진짜인지 확인하지 않는다.
- `critical`: 독자가 반드시 알아야 할 사실의 여부이다.
- `required_terms`: 보고서에 누락되면 안 되는 **정확한 문자열**이다. 다른 문장으로 동등하게 표현하면 오탐이 날 수 있으므로, 키워드가 아니라 실제 숫자·설정명·검증 불가 사유 등으로 제한한다.
- 식별자는 사실과 증거를 연결하는 데 사용하고, 자연어 본문은 사람이 읽기 쉽게 재배치한다.
- 민감 정보, 비밀번호, API 토큰을 sidecar에 저장하지 않는다.
- 동일 실행에서 시점이 다른 상태가 함께 나타나면 가장 최근의 검증 가능한 상태를 사용하고, 변화 내역은 별도 사건으로 남긴다.

## 검증 범위

정적 스크립트는 JSON 형식, 중복 ID, 허용된 상태, `verified`의 증거 필드 존재, `required_terms`의 본문 포함, opaque ID·모호한 지시어의 사용 흔적을 점검한다. **'실제 실행에 성공했는가', '문장의 의미가 원본과 같은가'는 자동으로 증명하지 않는다.**

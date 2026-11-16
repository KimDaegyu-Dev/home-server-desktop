#!/usr/bin/env python3
"""Heuristic, read-only lint for human-facing agent reports.

No network calls; no modifications; no claims of semantic truth verification.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

VALID_STATUSES = {
    "proposed", "design_decided", "scheduled", "running",
    "completed_unverified", "verified", "failed", "unverified", "blocked",
}
OPAQUE = re.compile(r"(?<![A-Za-z0-9_])t_[0-9a-z]{7,}(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![A-Za-z0-9_])", re.I)
DISTANT = re.compile(r"\b(?:those|the above|aforementioned|as mentioned earlier)\b|앞서 (?:말한|언급한)|위에서 (?:말한|언급한)|상기(?:한|의)? (?:내용|항목|작업)|그것들", re.I)
TRACKING_HEADINGS = re.compile(r"(?:추적|식별자|증거|appendix|references|trace|raw ids|evidence)", re.I)
STATUS_WORDS = re.compile(r"완료|진행|실행|검증|실패|미확인|미검증|예정|확정|대기|차단|제안|승인|변경|running|verified|blocked|failed|complete|pending", re.I)
FENCE = re.compile(r"^\s*(```|~~~)")


def issue(code: str, severity: str, message: str, line: int | None = None, fact_id: str | None = None) -> dict[str, Any]:
    obj = {"code": code, "severity": severity, "message": message}
    if line is not None:
        obj["line"] = line
    if fact_id is not None:
        obj["fact_id"] = fact_id
    return obj


def visible_lines(raw: str):
    """Ignore code fences but preserve source line numbering."""
    in_code = False
    in_tracking = False
    for number, line in enumerate(raw.splitlines(), 1):
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        if heading:
            in_tracking = bool(TRACKING_HEADINGS.search(heading.group(1)))
        yield number, line, in_tracking


def lint_text(raw: str) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    if not raw.strip():
        return [issue("empty_report", "error", "보고서에 내용이 없습니다.")]

    body = list(visible_lines(raw))
    content = [(n, l, in_tracking) for n, l, in_tracking in body if l.strip() and not l.lstrip().startswith('#')]
    first = " ".join(l.strip() for _, l, _ in content[:3])
    if not STATUS_WORDS.search(first):
        problems.append(issue("status_not_in_opening", "review", "도입부에 현재 상태 또는 변화가 명시적인지 확인하세요."))
    for n, line, in_tracking in body:
        if in_tracking or not line.strip():
            continue
        if OPAQUE.search(line):
            problems.append(issue("opaque_id_in_body", "review", "불투명한 내부 ID는 자연어 이름을 먼저 제시하고 추적 정보 영역으로 이동할지 검토하세요.", n))
        if DISTANT.search(line):
            problems.append(issue("possible_distant_reference", "review", "지시 대상이 먼 문맥에만 존재하지 않는지 확인하세요.", n))
        if len(line) > 235 and not line.startswith('|') and not line.lstrip().startswith('http'):
            problems.append(issue("dense_line", "review", "한 줄에 많은 정보가 들어 있습니다. 복수의 관계를 풀어 쓸지 확인하세요.", n))
        if line.count(' + ') >= 2 or line.count(' / ') >= 3:
            problems.append(issue("compressed_relations", "review", "기호로 연결된 정보 사이의 관계가 명확한지 확인하세요.", n))
    return problems


def lint_facts(raw: str, facts_file: Path) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    try:
        data = json.loads(facts_file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [issue("facts_unreadable", "error", f"facts 파일을 읽을 수 없습니다: {exc}")]
    if not isinstance(data, dict) or not isinstance(data.get('facts'), list):
        return [issue("facts_shape", "error", "facts.json은 최상위 facts 배열을 가져야 합니다.")]
    seen: set[str] = set()
    for i, fact in enumerate(data['facts']):
        if not isinstance(fact, dict):
            problems.append(issue("fact_shape", "error", f"facts[{i}]는 객체여야 합니다."))
            continue
        fid = fact.get('id')
        if not isinstance(fid, str) or not fid.strip():
            problems.append(issue("fact_id_missing", "error", f"facts[{i}]에 id가 없습니다."))
            continue
        if fid in seen:
            problems.append(issue("fact_id_duplicate", "error", "중복된 fact ID입니다.", fact_id=fid))
        seen.add(fid)
        if not isinstance(fact.get('summary'), str) or not fact['summary'].strip():
            problems.append(issue("fact_summary_missing", "error", "fact 설명이 비어 있습니다.", fact_id=fid))
        status = fact.get('status')
        if status not in VALID_STATUSES:
            problems.append(issue("fact_status_invalid", "error", f"지원하지 않는 상태: {status!r}", fact_id=fid))
        evidence = fact.get('evidence', [])
        if not isinstance(evidence, list) or any(not isinstance(e, str) or not e.strip() for e in evidence):
            problems.append(issue("evidence_shape", "error", "evidence는 비어 있지 않은 문자열 배열이어야 합니다.", fact_id=fid))
        elif status == 'verified' and not evidence:
            problems.append(issue("verified_without_evidence", "error", "verified 상태에는 증거 참조가 필요합니다.", fact_id=fid))
        if fact.get('critical', False) not in (True, False):
            problems.append(issue("critical_shape", "error", "critical은 boolean이어야 합니다.", fact_id=fid))
        terms = fact.get('required_terms', [])
        if not isinstance(terms, list) or any(not isinstance(x, str) or not x for x in terms):
            problems.append(issue("required_terms_shape", "error", "required_terms는 비어 있지 않은 문자열의 배열이어야 합니다.", fact_id=fid))
            continue
        if fact.get('critical', False):
            for term in terms:
                if term not in raw:
                    problems.append(issue("critical_term_missing", "error", f"중요 사실의 지정 문자열이 보고서에 없습니다: {term!r}", fact_id=fid))
    return problems


def run(report_path: Path, facts_path: Path | None) -> dict[str, Any]:
    try:
        raw = report_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        return {"report": str(report_path), "issues": [issue("report_unreadable", "error", str(exc))]}
    issues = lint_text(raw)
    if facts_path is not None:
        issues.extend(lint_facts(raw, facts_path))
    return {"report": str(report_path), "facts": str(facts_path) if facts_path else None, "issues": issues,
            "summary": {k: sum(i['severity'] == k for i in issues) for k in ['error', 'review']},
            "limitations": "휴리스틱 스타일 검사 및 명시 문자열 포함 여부만 검사합니다. 실제 실행, 증거의 진위, 의미 보존은 확인하지 않습니다."}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--facts', type=Path)
    p.add_argument('--format', choices=['text','json'], default='text')
    p.add_argument('--strict', action='store_true', help='Exit 1 for any finding, including heuristic review warnings')
    args = p.parse_args()
    result = run(args.report, args.facts)
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for i in result['issues']:
            where = (f"L{i['line']} " if 'line' in i else '') + (f"[{i['fact_id']}] " if 'fact_id' in i else '')
            print(f"{i['severity'].upper():6s} {i['code']:29s} {where}{i['message']}")
        print(f"완료: 오류 {result.get('summary', {}).get('error', 1)}개, 점검 권고 {result.get('summary', {}).get('review', 0)}개")
        print(result.get('limitations',''))
    if any(i['severity'] == 'error' for i in result['issues']):
        return 1
    if args.strict and result['issues']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

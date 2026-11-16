import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'report_lint.py'
spec = importlib.util.spec_from_file_location('report_lint', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ReportLintTests(unittest.TestCase):
    def test_opaque_id_in_body_is_flagged(self):
        issues = module.lint_text('N1 작업은 실행 중이며 t_b7b0a2ad가 확인되었습니다.')
        self.assertIn('opaque_id_in_body', [x['code'] for x in issues])

    def test_opaque_id_in_tracking_section_is_allowed(self):
        issues = module.lint_text('작업은 검증 중입니다.\n\n## 추적 정보\nN1: t_b7b0a2ad')
        self.assertNotIn('opaque_id_in_body', [x['code'] for x in issues])

    def test_distant_reference_flagged(self):
        issues = module.lint_text('작업은 실행 중입니다.\n앞서 언급한 값들은 검증되지 않았습니다.')
        self.assertIn('possible_distant_reference', [x['code'] for x in issues])

    def test_fact_evidence_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'facts.json'
            path.write_text(json.dumps({'facts':[{'id':'F01','summary':'작업 성공','status':'verified','critical':True,'evidence':[],'required_terms':['성공']}]}), encoding='utf-8')
            codes = [x['code'] for x in module.lint_facts('작업 성공', path)]
            self.assertIn('verified_without_evidence', codes)

    def test_missing_critical_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'facts.json'
            path.write_text(json.dumps({'facts':[{'id':'F01','summary':'18개에서 13개로 변경 예정','status':'design_decided','critical':True,'evidence':['log:1'],'required_terms':['13개']}]}), encoding='utf-8')
            codes = [x['code'] for x in module.lint_facts('설계 확정', path)]
            self.assertIn('critical_term_missing', codes)

    def test_sample_has_no_issues(self):
        root = Path(__file__).resolve().parents[1]
        result = module.run(root/'examples'/'report.md', root/'examples'/'facts.json')
        self.assertFalse(result['issues'], result['issues'])

if __name__ == '__main__':
    unittest.main()

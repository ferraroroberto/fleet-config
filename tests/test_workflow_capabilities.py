"""Exercise the interactive cleanup bridge with synthetic worker results only."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'skills/_lib/cleanup_workflow.cjs'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


class WorkflowCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which('node')
        self.assertIsNotNone(self.node, 'node is required to verify the interactive bridge')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'state.json'
        self.state = {'issuesByBucket': {'bug': [{'repo': 'synthetic', 'number': 1, 'title': 'probe', 'body': 'synthetic'}]}, 'results': []}

    def invoke(self, success=True):
        self.file.write_text(json.dumps(self.state), encoding='utf-8')
        proc = subprocess.run([self.node, str(BRIDGE), str(self.file)], capture_output=True, text=True, encoding='utf-8', creationflags=NO_WINDOW, timeout=30)
        self.assertEqual(proc.returncode, 0 if success else 2, proc.stderr)
        return json.loads(proc.stdout) if success else proc.stderr

    def collect(self, result):
        pending = self.invoke()
        self.assertEqual(pending['status'], 'request')
        self.state['workflowHash'] = pending['workflowHash']
        self.state['results'].append({'id': pending['request']['id'], 'result': result})
        return pending['request']['phase']

    def built(self):
        return {'status': 'built', 'verification': 'PASS', 'branch': 'feat/1-probe', 'worktree': 'synthetic-wt'}

    def test_collects_all_roles_before_complete(self):
        self.assertEqual(self.collect(self.built()), 'Build')
        self.assertEqual(self.collect({'pass': True, 'feedback': 'independent pass', 'verification': 'PASS'}), 'Validate')
        self.assertEqual(self.collect({'result': 'MERGED', 'mergeSha': 'synthetic'}), 'Execute')
        self.assertEqual(self.collect({'residue': 'CLEAN', 'detail': 'synthetic clean'}), 'Teardown')
        final = self.invoke()
        self.assertEqual(final['status'], 'complete')
        self.assertIsNone(final['result']['halted'])
        self.assertEqual(final['result']['buckets'][0]['results'][0]['status'], 'merged')

    def test_timeout_or_unconfirmed_cancel_does_not_advance(self):
        pending = self.invoke()
        # No terminal output may be appended for either timeout or unconfirmed cancellation.
        self.assertEqual(self.invoke(), pending)
        self.assertEqual(len(self.state['results']), 0)
        self.assertEqual(pending['request']['phase'], 'Build')

    def test_failed_worker_tears_down_and_residue_halts_next_lane(self):
        self.state['issuesByBucket']['bug'].append({'repo': 'second', 'number': 2, 'title': 'probe', 'body': 'synthetic'})
        self.collect(None)
        self.assertEqual(self.collect({'residue': 'RESIDUE', 'detail': 'worker outcome unverified'}), 'Teardown')
        result = self.invoke()['result']
        self.assertEqual(result['halted']['remainingInBucket'], 1)
        self.assertEqual(len(result['buckets'][0]['results']), 1)

    def test_rejected_review_never_executes_and_retries_only_twice(self):
        for _ in range(2):
            self.assertEqual(self.collect(self.built()), 'Build')
            self.assertEqual(self.collect({'pass': False, 'feedback': 'acceptance missing', 'verification': 'PASS'}), 'Validate')
        self.assertEqual(self.collect({'residue': 'CLEAN', 'detail': 'preserved evidence'}), 'Teardown')
        lane = self.invoke()['result']['buckets'][0]['results'][0]
        self.assertEqual(lane['status'], 'escalated')
        self.assertEqual(lane['round'], 2)

    def test_replay_rejects_changed_issue_and_wrong_request(self):
        self.collect(self.built())
        self.state['issuesByBucket']['bug'][0]['body'] = 'changed acceptance'
        self.assertIn('does not match', self.invoke(False))
        self.state['issuesByBucket']['bug'][0]['body'] = 'synthetic'
        self.state['results'][0]['id'] = 'wrong'
        self.assertIn('does not match', self.invoke(False))

    def test_missing_or_changed_workflow_hash_rejected(self):
        self.collect(self.built())
        del self.state['workflowHash']
        self.assertIn('hash missing or changed', self.invoke(False))
        self.state['workflowHash'] = 'stale'
        self.assertIn('hash missing or changed', self.invoke(False))

    def test_malformed_and_contradictory_verdicts_rejected(self):
        self.collect(self.built())
        self.collect({'pass': 'true', 'feedback': 'wrong type', 'verification': 'PASS'})
        self.assertIn('expected boolean', self.invoke(False))
        self.state['results'][-1]['result'] = {'pass': True, 'feedback': 'contradiction', 'verification': 'FAIL'}
        self.assertIn('consistent verification', self.invoke(False))

    def test_extra_result_cannot_be_ignored(self):
        self.state['issuesByBucket'] = {}
        empty = self.invoke()
        self.state['workflowHash'] = empty['workflowHash']
        self.state['results'] = [{'id': 'unexpected', 'result': None}]
        self.assertIn('unused results', self.invoke(False))

    def test_consumers_load_contract_and_do_not_assume_wakeups(self):
        consumers = ['skills/issue-add', 'skills/issue-start', 'skills/issue-batch', 'skills/issue-finish-batch', 'skills/issue-yolo', 'skills/propagate-vendored', '.claude/skills/cleanup-fleet', '.claude/skills/cleanup-fleet-all', '.claude/skills/audit-fleet', '.claude/skills/design-sweep', '.claude/skills/learning-log', '.claude/skills/sota-watch']
        for consumer in consumers:
            text = (ROOT / consumer / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('workflow-capabilities.md', text, consumer)
            self.assertNotIn('The harness re-invokes you automatically', text, consumer)
            self.assertNotIn('Then stop — do not poll', text, consumer)
        yolo = (ROOT / 'skills/issue-yolo/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('serial self-review does not satisfy this gate', yolo)
        self.assertIn('On `pass: false` — stop and report', yolo)


if __name__ == '__main__':
    unittest.main()
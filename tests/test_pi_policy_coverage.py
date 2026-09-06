"""Pi normalization, shared hook protocol and real extension middleware fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hooks'))
import _lib


def payload(tool='powershell', event='tool_call', **args):
    data = {'fleet_harness': 'pi', 'type': event, 'toolName': tool,
            'toolCallId': 'test', 'input': args, 'cwd': str(ROOT), 'session_id': 'synthetic'}
    if tool in {'edit', 'write'}:
        # Fixture paths are ordinary relative/absolute forms. Native alias
        # resolution itself is exercised by probe_pi_policies in installed Pi.
        data['fleet_resolved_path'] = str(ROOT / args.get('path', ''))
    return data


class PiPolicies(unittest.TestCase):
    def test_provenance_shells_and_legacy_identity(self):
        for tool, expected in [('powershell', 'PowerShell'), ('bash', 'Bash')]:
            normalized = _lib.normalize_payload(payload(tool, command='echo harmless'))
            self.assertEqual(normalized['tool_name'], expected)
            self.assertFalse(_lib.shell_is_ambiguous(normalized))
            self.assertEqual(_lib.payload_agent(normalized), 'pi')
        for original in [{'hook_event_name': 'PreToolUse', 'tool_name': 'Bash'}, {'event': 'input'}]:
            self.assertIs(_lib.normalize_payload(original), original)

    def test_edit_targets_and_outcomes(self):
        for tool in ['edit', 'write']:
            for flag, outcome in [(False, 'success'), (True, 'failed'), (None, 'unknown')]:
                raw = payload(tool, 'tool_result', path='synthetic.py')
                raw['isError'] = flag
                event = _lib.edit_event(_lib.normalize_payload(raw))
                self.assertEqual(event.outcome, outcome)
                self.assertEqual(event.targets[0].path, ROOT / 'synthetic.py')
        with self.assertRaises(ValueError):
            _lib.normalize_payload(payload('powershell', command=None))
        with self.assertRaises(ValueError):
            _lib.normalize_payload(payload('unknown', command='echo harmless'))
        malformed = payload('write', path='bad\0path')
        with self.assertRaises(ValueError):
            _lib.normalize_payload(malformed)

    def test_native_target_evidence_is_required(self):
        raw = payload('write', path='@docs/2026-09-06-sentinel.md')
        raw['fleet_resolved_path'] = str(ROOT / 'docs/2026-09-06-sentinel.md')
        normalized = _lib.normalize_payload(raw)
        self.assertEqual(_lib.edit_event(normalized).targets[0].path, Path(raw['fleet_resolved_path']))
        for unknown in [None, '', 'relative.py']:
            raw['fleet_resolved_path'] = unknown
            with self.assertRaises(ValueError):
                _lib.normalize_payload(raw)
        malformed = payload(command='echo harmless'); malformed['cwd'] = 'relative'
        with self.assertRaises(ValueError):
            _lib.normalize_payload(malformed)

    def test_real_hooks_refusal_advice_and_failed_edit(self):
        with tempfile.TemporaryDirectory(prefix='pi_policy_') as tmp:
            directory = Path(tmp)
            config = directory / 'projects.toml'
            config.write_text('[global]\nnever_kill_ports=[]\n', encoding='utf-8')
            env = {**os.environ, 'PYTHONUTF8': '1', 'CLAUDE_HOOKS_STATE_DIR': tmp,
                   'CLAUDE_HOOKS_PROJECTS_TOML': str(config)}
            def hook(name, data):
                result = subprocess.run([sys.executable, str(ROOT / 'hooks' / (name+'.py'))],
                                        input=json.dumps(data), capture_output=True, text=True,
                                        encoding='utf-8', env=env, timeout=20, creationflags=_lib.NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            for tool in ['bash', 'powershell']:
                blocked = hook('pre_commit_no_ai_trailer', payload(tool, command='echo sentinel; git commit -m "Co-Authored-By: Claude"'))
                self.assertEqual(blocked['decision'], 'block')
                self.assertEqual(hook('venv_discipline', payload(tool, command='echo harmless'))['decision'], 'allow')
                self.assertEqual(hook('safe_kill_guard', payload(tool, command='git commit --no-verify'))['decision'], 'block')
                fake = 'sk' + '-' + 'AbCdEfGhIjKlMnOpQrStUvWxYz012345'
                self.assertEqual(hook('secret_scan_guard', payload(tool, command='git commit -m "'+fake+'"'))['decision'], 'block')
            self.assertEqual(hook('gh_body_file_guard', payload('bash', command="echo @'hi'@"))['decision'], 'warn')
            self.assertEqual(hook('docs_dated_filename_guard', payload('write', path='docs/2026-09-06-sentinel.md'))['decision'], 'block')
            bad = directory / 'bad.py'
            bad.write_text('def broken(:\n', encoding='utf-8')
            raw = payload('write', 'tool_result', path=str(bad)); raw['isError'] = False
            result = hook('py_syntax_check', raw)
            self.assertEqual(result['decision'], 'warn')
            self.assertIn('SyntaxError', result['message'])
            raw['isError'] = True
            result = hook('py_syntax_check', raw)
            self.assertIn('unverified', result['message'])
            self.assertNotIn('SyntaxError', result['message'])
            bad = directory / 'browser.py'
            raw['input']['path'] = str(bad)
            raw['fleet_resolved_path'] = str(bad)
            for name, content in [
                ('hub_bypass_warn', 'import subprocess\nsubprocess.run(["claude", "-p", "x"])\n'),
                ('browser_stealth_lint', 'ctx = p.chromium.launch_persistent_context(user_data_dir="x")\n'),
            ]:
                bad.write_text(content, encoding='utf-8')
                raw['isError'] = False
                self.assertEqual(hook(name, raw)['decision'], 'warn')
                raw['isError'] = True
                self.assertEqual(hook(name, raw)['decision'], 'allow')
            # Branch guard retains its launcher-only policy on a real isolated repo.
            subprocess.run(['git', 'init', '-q', '-b', 'main', tmp], check=True, creationflags=_lib.NO_WINDOW)
            subprocess.run(['git', '-C', tmp, '-c', 'core.hooksPath='+str(directory / 'synthetic-hooks'), '-c', 'user.name=Synthetic', '-c',
                            'user.email=synthetic@example.invalid', 'commit', '--allow-empty', '-qm', 'chore: fixture'], check=True, creationflags=_lib.NO_WINDOW)
            env['APP_LAUNCHER_SESSION_ID'] = 'synthetic'
            env.pop('CLAUDE_HOOKS_ALLOW_MAIN_EDIT', None)
            self.assertEqual(hook('branch_before_edit_guard', payload('edit', path=str(bad)))['decision'], 'block')

    def test_extension_middleware(self):
        result = subprocess.run(['node', str(ROOT / 'tests' / 'pi_policy_fixtures.mjs')],
                                capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=_lib.NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Pi middleware fixtures PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()

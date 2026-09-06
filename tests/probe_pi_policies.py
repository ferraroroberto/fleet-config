"""Opt-in installed Pi conformance; synthetic files only.

Run with --cli <installed dist/bundle/cli.js> --synthetic (no model network),
or --provider <id> --model <id> (normal saved auth). Add --policy-first to
reverse middleware order. No tools, hooks, or policy subprocesses are mocked.
Evidence stays in the printed temporary directory. No installed config is changed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--provider')
    parser.add_argument('--model')
    parser.add_argument('--synthetic', action='store_true', help='Deterministic responses in the real Pi loop; no network/auth')
    parser.add_argument('--policy-first', action='store_true', help='Exercise reversed middleware order')
    args = parser.parse_args()
    if not args.synthetic and not (args.provider and args.model):
        parser.error('provide --synthetic or both --provider and --model')
    root = Path(tempfile.mkdtemp(prefix='pi_policy_native_'))
    repo = root / 'synthetic'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True, creationflags=NO_WINDOW)
    state = root / 'state'
    state.mkdir()
    config = root / 'projects.toml'
    config.write_text('[global]\nnever_kill_ports=[]\n', encoding='utf-8')
    env = {**os.environ, 'PYTHONUTF8': '1', 'PI_OFFLINE': '1', 'PI_TELEMETRY': '0',
           'CLAUDE_HOOKS_STATE_DIR': str(state), 'CLAUDE_HOOKS_PROJECTS_TOML': str(config),
           'FLEET_CONTEXT_FILTER_DIR': str(root / 'filter'), 'FLEET_CONTEXT_FILTER_MODE': 'rewrite'}
    # An inherited launcher marker must not turn the positive control into
    # branch enforcement. The branch guard has its own isolated fixtures.
    env.pop('APP_LAUNCHER_SESSION_ID', None)
    base = ['node', str(args.cli), '--approve', '--no-session', '--no-context-files',
            '--no-skills', '--no-prompt-templates', '--no-extensions', '--offline']
    extensions = ['policy_hooks', 'context_filter'] if args.policy_first else ['context_filter', 'policy_hooks']
    for extension in [*extensions, 'session_state']:
        base += ['--extension', str(ROOT / 'pi' / 'extensions' / (extension+'.ts'))]
    if args.synthetic:
        provider = root / 'synthetic_provider.ts'
        provider.write_text((ROOT / 'tests/pi_synthetic_provider.ts').read_text(encoding='utf-8'), encoding='utf-8')
        base += ['--extension', str(provider)]
        env['PI_CODING_AGENT_DIR'] = str(root / 'pi-home')
        args.provider, args.model = 'fleet-synthetic', 'deterministic'
    base += ['--tools', 'bash,powershell,edit,write', '--mode', 'json',
             '--provider', args.provider, '--model', args.model, '--thinking', 'minimal',
             '--system-prompt', 'Execute only the exact synthetic tool call requested. '
             'Never inspect any other file or directory. Do not retry, repair, or substitute a refused call. '
             'After the tool result, quote any Fleet policy message and stop.']
    cases = [
        ('allowed', 'Use powershell exactly once with command: Set-Content -LiteralPath allowed.txt -Value allowed'),
        ('shell_block', "Use powershell exactly once with command: Write-Output 'git commit --no-verify'; Set-Content -LiteralPath shell_sentinel.txt -Value blocked"),
        ('edit_block', 'Use write exactly once with path docs/2026-09-06-sentinel.md and content sentinel'),
        ('post_edit', 'Use write exactly once with path invalid.py and content consisting of this exact line plus newline: def broken(:'),
    ]
    if args.synthetic:
        cases += [('post_replace', 'synthetic edit'), ('compression_warning', 'synthetic bash compression')]
    (repo / 'replace.py').write_text('VALUE = 1\n', encoding='utf-8')
    (repo / 'noisy.txt').write_text('synthetic row data repeated\n' * 5000, encoding='utf-8')
    calls = {
        'allowed': {'name': 'powershell', 'arguments': {'command': 'Set-Content -LiteralPath allowed.txt -Value allowed'}},
        'shell_block': {'name': 'powershell', 'arguments': {'command': "Write-Output 'git commit --no-verify'; Set-Content -LiteralPath shell_sentinel.txt -Value blocked"}},
        'edit_block': {'name': 'write', 'arguments': {'path': 'docs/2026-09-06-sentinel.md', 'content': 'sentinel'}},
        'post_edit': {'name': 'write', 'arguments': {'path': 'invalid.py', 'content': 'def broken(:\n'}},
        'post_replace': {'name': 'edit', 'arguments': {'path': 'replace.py', 'edits': [{'oldText': 'VALUE = 1', 'newText': 'def broken(:'}]}},
        'compression_warning': {'name': 'bash', 'arguments': {'command': "cat noisy.txt; echo \"@'hi'@\""}},
    }
    reports = []
    print('EVIDENCE=' + str(root), flush=True)
    for name, prompt in cases:
        if args.synthetic:
            call = root / (name+'.call.json')
            call.write_text(json.dumps(calls[name]), encoding='utf-8')
            env.update(PI_POLICY_CALL=str(call), PI_POLICY_RECEIVED=str(root / (name+'.received.jsonl')),
                       PI_POLICY_LIFECYCLE=str(root / (name+'.lifecycle.jsonl')))
        result = subprocess.run(base + ['--print', prompt], cwd=repo, env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=180, creationflags=NO_WINDOW)
        (root / (name+'.jsonl')).write_text(result.stdout, encoding='utf-8')
        (root / (name+'.stderr.txt')).write_text(result.stderr, encoding='utf-8')
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        ends = [event for event in events if event.get('type') == 'tool_execution_end']
        messages = [event.get('message', {}) for event in events if event.get('type') == 'message_end']
        tool_messages = [message for message in messages if message.get('role') == 'toolResult']
        tool_text = json.dumps([m.get('content', []) for m in tool_messages], ensure_ascii=False)
        assistant_text = json.dumps([m for m in messages if m.get('role') == 'assistant'], ensure_ascii=False)
        invoked = len(ends) == 1 and len(tool_messages) == 1
        if name == 'allowed':
            proven = invoked and (repo / 'allowed.txt').is_file() and not tool_messages[0].get('isError')
        elif name == 'shell_block':
            proven = invoked and not (repo / 'shell_sentinel.txt').exists() and 'git safety bypass' in tool_text
        elif name == 'edit_block':
            proven = invoked and not (repo / 'docs/2026-09-06-sentinel.md').exists() and 'Blocked' in tool_text
        elif name == 'compression_warning':
            proven = (invoked and '[Fleet policy]' in tool_text and 'here-string' in tool_text
                      and 'fleet-context-filter: raw_tokens=' in tool_text and len(tool_text) < 10000)
        else:
            target = 'replace.py' if name == 'post_replace' else 'invalid.py'
            proven = (invoked and (repo / target).is_file() and '[Fleet policy] py_compile:' in tool_text
                      and 'SyntaxError' in tool_text and 'py_compile' in assistant_text
                      and not tool_messages[0].get('isError'))
        if args.synthetic:
            received_path = root / (name+'.received.jsonl')
            received = json.loads(received_path.read_text(encoding='utf-8').splitlines()[-1]) if received_path.exists() else {}
            lifecycle_path = root / (name+'.lifecycle.jsonl')
            lifecycle = [json.loads(line) for line in lifecycle_path.read_text(encoding='utf-8').splitlines()] if lifecycle_path.exists() else []
            proven = proven and bool(tool_messages) and received.get('role') == 'toolResult' and received.get('content') == tool_messages[0].get('content') and len(lifecycle) == 3 and all(
                item.get('observed', 'unknown') == item['expected'] for item in lifecycle)
        report = {'case': name, 'status': 'verified' if result.returncode == 0 and proven else 'not confirmed',
                  'exit_code': result.returncode, 'tool_results': len(tool_messages),
                  'model_transport': 'deterministic synthetic' if args.synthetic else 'authenticated provider',
                  'extension_order': extensions}
        reports.append(report)
        print(json.dumps(report), flush=True)
        if report['status'] != 'verified':
            break
    (root / 'summary.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    if len(reports) != len(cases) or any(r['status'] != 'verified' for r in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

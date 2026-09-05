"""Opt-in replay of a harmless saved Codex rollout through capture + search.

Not in the offline gate. Does not launch a model, change hook trust/config, or
read other sessions. Run the disposable Codex conversation separately; pass
only that conversation's exact saved rollout and its expected native ID.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hooks'))
import conversation_capture as cc
import transcript_readers as tr
from _lib import NO_WINDOW, run_git


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollout', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    args = parser.parse_args()
    transcript = tr.read_transcript(args.rollout, harness='codex', session_id=args.session_id)
    if transcript.status != 'ok' or not transcript.messages:
        raise RuntimeError(f'Native source not confirmed: {transcript.status}: {transcript.detail}')
    if any('CAPTURE753' not in text or len(text) > 500 for _, text in transcript.messages):
        raise RuntimeError('Probe accepts only the short disposable CAPTURE753 marker conversation')
    root = Path(tempfile.mkdtemp(prefix='capture753_replay_'))
    project = root / 'project'
    project.mkdir()
    run_git(['init', '-q', str(project)], check=True)
    (project / '.gitignore').write_text('conversations/\n', encoding='utf-8')
    registry = root / 'projects.toml'
    registry.write_text('[probe]\ncwd_prefix = ' + json.dumps(project.as_posix()) +
                        '\ncapture = true\ncapture_harnesses = ["codex"]\n', encoding='utf-8')
    env = {**os.environ, 'CLAUDE_HOOKS_PROJECTS_TOML': str(registry),
           'CLAUDE_HOOKS_STATE_DIR': str(root / 'state'), 'PYTHONUTF8': '1'}
    payload = {'hook_event_name': 'Stop', 'cwd': str(project),
               'transcript_path': str(args.rollout), 'session_id': args.session_id}
    # Replay the real hook entry point/registry. Scheduling is separately tested
    # offline; suppress only the detached digest trigger so the native proof has
    # no uncollected children or additional model calls.
    with patch.dict(os.environ, env), patch.object(cc, '_trigger_delayed_index') as trigger:
        for _ in range(2):
            with patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))):
                if cc.main() != 0:
                    raise RuntimeError('Capture hook failed')
        if trigger.call_count != 1:
            raise RuntimeError('Repeated native capture was not idempotent')
    captures = list((project / 'conversations').glob('*.md'))
    if len(captures) != 1:
        raise RuntimeError('Expected one native capture')
    capture = captures[0]
    before = capture.read_bytes()
    text = before.decode('utf-8')
    header = cc.parse_capture_header(text)
    if header.get('sid') != args.session_id or header.get('agent') != 'codex':
        raise RuntimeError('Native identity lost')
    if header.get('parent_sid', '') != transcript.parent_session_id:
        raise RuntimeError('Native fork lineage lost')
    offset = 0
    for role, message in transcript.messages:
        rendered = ('**You**: ' if role == 'user' else '**Codex**: ') + message
        offset = text.index(rendered, offset) + len(rendered)
    command = [sys.executable, str(ROOT / 'hooks' / 'conversation_search.py'), '--project', 'probe']
    evidence = []
    for flags in [['--rebuild'], ['--query', 'CAPTURE753', '--json']]:
        result = subprocess.run([*command, *flags], env=env, text=True, encoding='utf-8',
                                capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr)
        evidence.append(result.stdout)
    hits = json.loads(evidence[-1])
    if len(hits) != 1 or hits[0]['resume'] != f'codex resume {args.session_id}':
        raise RuntimeError('Native capture not searchable/resumable')
    if capture.read_bytes() != before:
        raise RuntimeError('Derived rebuild modified the original capture')
    ignored = run_git(['-C', str(project), 'check-ignore', str(capture)])
    if ignored.returncode:
        raise RuntimeError('Private capture is not ignored')
    # The sibling is intentionally absent from the registry: no capture and no trigger.
    payload['cwd'] = str(root / 'unrelated')
    with patch.dict(os.environ, env), patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))), \
         patch.object(cc, '_trigger_delayed_index') as trigger:
        cc.main()
        if trigger.called or (root / 'unrelated').exists():
            raise RuntimeError('Unrelated project was captured')
    report = dict(status='PASS', root=str(root), session_id=args.session_id,
                  parent_session_id=transcript.parent_session_id, turns=len(transcript.messages),
                  capture=str(capture), search='PASS', ignored=True, idempotent=True,
                  scope='stored native rollout replay; native hook dispatch and digest model not exercised')
    (root / 'evidence.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (root / 'search.json').write_text(evidence[-1], encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

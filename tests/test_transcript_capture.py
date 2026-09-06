"""Regression boundary for shared native transcript capture (#753)."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import _lib
import conversation_capture as cc
import conversation_search as cs
import conversation_index as ci
import transcript_readers as tr

SID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
THIRD = '33333333-3333-4333-8333-333333333333'
PROMPT = 'Harmless capture marker about lunar gardens'


def claude(sid=SID):
    return [dict(type=role, sessionId=sid, timestamp=f'2026-09-05T12:00:0{i}Z',
                 message={'content': text})
            for i, (role, text) in enumerate([('user', PROMPT), ('assistant', 'Lunar gardens answer')])]


def codex(sid=SID, parent=None):
    meta = {'id': sid, 'cli_version': '0.153.3', 'originator': 'codex_exec'}
    if parent:
        meta['forked_from_id'] = parent
    return [{'type': 'session_meta', 'payload': meta},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
             'content': [{'type': 'input_text', 'text': 'Injected context: do not capture'}]}},
            *[{'type': 'event_msg', 'timestamp': f'2026-09-05T12:00:0{i}Z',
               'payload': {'type': 'item_completed', 'thread_id': sid, 'turn_id': 'turn-one',
                           'item': {'type': role, 'id': f'item-{i}',
                                    'content': [{'type': 'text', 'text': text}]}}}
              for i, (role, text) in enumerate([('UserMessage', PROMPT), ('AgentMessage', 'Lunar gardens answer')])]]


def stamped(records, when):
    """Re-stamp a fixture onto a real wall clock; marker provenance compares the two."""
    iso = datetime.fromtimestamp(when, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return [{**record, 'timestamp': iso} if 'timestamp' in record else record for record in records]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'transcript.jsonl'
        self.project = _lib.ProjectConfig(name='probe', cwd_prefix=str(self.root), webapp_port=None, tray_cmd=None, restart_cmd=None, api_version_path=None,
            extra={'capture': True, 'capture_harnesses': ['claude', 'codex']})

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self, records, agent=None, sid=SID):
        self.path.write_text('\n'.join(json.dumps(x) for x in records) + '\n', encoding='utf-8')
        payload = {'cwd': str(self.root), 'transcript_path': str(self.path), 'session_id': sid}
        if agent:
            payload[_lib.AGENT_HINT_KEY] = agent
        with patch.object(cc._lib, 'read_stdin_json', return_value=payload), \
             patch.object(cc._lib, 'detect_project', return_value=self.project), \
             patch.object(cc, '_trigger_delayed_index'):
            self.assertEqual(cc.main(), 0)
        return list((self.root / 'conversations').glob('*.md'))

    def test_native_codex_turns_and_identity(self):
        files = self.capture(codex(), 'codex')
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding='utf-8')
        self.assertEqual(cc.parse_capture_header(text)['agent'], 'codex')
        self.assertIn('**Codex**: Lunar gardens answer', text)
        self.assertNotIn('Injected context', text)
        self.assertLess(text.index('**You**:'), text.index('**Codex**:'))

    def test_same_prompt_unrelated_sessions_and_providers(self):
        self.capture(claude(), 'claude')
        self.capture(claude(OTHER), 'claude', OTHER)
        files = self.capture(codex(), 'codex')
        self.assertEqual(len(files), 3)
        before = {p.name: p.read_bytes() for p in files}
        self.capture(codex(), 'codex')
        self.assertEqual(before, {p.name: p.read_bytes() for p in files})

    def test_missing_agent_is_not_resumable(self):
        self.assertEqual(cs.resume_command('', SID), '')
        self.assertEqual(cs.resume_command('unknown', SID), '')
        self.assertIn('**Assistant**:', cc.render_markdown('unknown', [('assistant', 'hello')]))

    def test_unknown_shape_is_not_claude(self):
        files = self.capture([{'type': 'user', 'message': {'content': PROMPT}}], sid='')
        for file in files:
            self.assertNotEqual(cc.parse_capture_header(file.read_text(encoding='utf-8')).get('agent'), 'claude')

    def test_resume_update_and_fork_lineage(self):
        first = self.capture(codex(), 'codex')[0]
        resumed = codex()
        more = codex()[2:]
        for row in more:
            row['payload']['turn_id'] = 'turn-two'
            row['payload']['item']['content'][0]['text'] += ' second turn'
        resumed += more
        files = self.capture(resumed, 'codex')
        self.assertEqual(files, [first])
        text = first.read_text(encoding='utf-8')
        self.assertEqual(text.count('**You**:'), 2)
        files = self.capture(codex(OTHER, SID), 'codex', OTHER)
        self.assertEqual(len(files), 2)
        fork = next(p for p in files if p != first)
        self.assertEqual(cc.parse_capture_header(fork.read_text(encoding='utf-8'))['parent_sid'], SID)
        self.assertEqual(first.read_text(encoding='utf-8'), text)
        self.capture(codex(), 'codex')  # a complete-lines truncation cannot shrink the capture
        self.assertEqual(first.read_text(encoding='utf-8'), text)

    def test_failures_leave_original_and_marker_intact(self):
        original = self.capture(codex(), 'codex')[0]
        before = original.read_bytes()
        marker = self.root / '.active-skill'
        marker.write_text('probe', encoding='utf-8')
        self.path.write_text('{"type":', encoding='utf-8')
        self.assertEqual(tr.read_transcript(self.path).status, 'parse_failure')
        payload = {'cwd': str(self.root), 'transcript_path': str(self.path), 'session_id': SID}
        with patch.object(cc._lib, 'read_stdin_json', return_value=payload), \
             patch.object(cc._lib, 'detect_project', return_value=self.project):
            cc.main()
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue(marker.exists())
        self.path.unlink()
        self.assertEqual(tr.read_transcript(self.path).status, 'unavailable')
        self.path.write_text('[1,2]\n', encoding='utf-8')
        self.assertEqual(tr.read_transcript(self.path).status, 'parse_failure')
        self.path.write_text('{"type":"thread.started"}\n', encoding='utf-8')
        self.assertEqual(tr.read_transcript(self.path, harness='codex').status, 'unsupported')
        self.assertEqual(tr.read_transcript(self.path, harness='pi').status, 'unsupported')
        self.assertEqual(tr.read_transcript(self.path, harness='grok').status, 'unsupported')
        malformed = codex()
        malformed[-1]['payload']['item']['id'] = []
        self.path.write_text('\n'.join(json.dumps(e) for e in malformed), encoding='utf-8')
        self.assertEqual(tr.read_transcript(self.path).status, 'parse_failure')

    def test_opt_in_and_skill_routing_survive_later_turn(self):
        del self.project.extra['capture_harnesses']
        self.assertEqual(self.capture(codex(), 'codex'), [])
        self.project.extra.update(capture_harnesses=['codex'], capture_routing='skills')
        skill = self.root / '.claude' / 'skills' / 'probe'
        skill.mkdir(parents=True)
        (self.root / '.active-skill').write_text('probe', encoding='utf-8')
        self.capture(codex(), 'codex')
        routed = list((skill / 'conversations').glob('*.md'))
        self.assertEqual(len(routed), 1)
        self.capture(codex(), 'codex')
        self.assertEqual(list(self.root.rglob('*.md')), routed)
        self.project.extra['capture'] = False
        self.assertIsNone(cc.capture_config_from_project(self.project))

    def test_stale_marker_cannot_misfile_the_next_session(self):
        """#784: a marker no Stop hook consumed must not route a later session."""
        self.project.extra.update(capture_harnesses=['claude', 'codex'], capture_routing='skills')
        skills = self.root / '.claude' / 'skills'
        (skills / 'journal-daily').mkdir(parents=True)
        (skills / 'probe').mkdir(parents=True)
        marker = self.root / '.active-skill'
        journal = skills / 'journal-daily' / 'conversations'
        archive = self.root / 'conversations' / '_archive'

        # Written and consumed inside one session: the common case still routes.
        marker.write_text('journal-daily', encoding='utf-8')
        self.capture(stamped(codex(), time.time()), 'codex')
        self.assertEqual(len(list(journal.glob('*.md'))), 1)
        self.assertFalse(marker.exists())

        # Left behind by an interrupted session, so it predates the next one.
        stale = time.time() - 3600
        marker.write_text('journal-daily', encoding='utf-8')
        os.utime(marker, (stale, stale))
        self.capture(stamped(codex(OTHER), time.time()), 'codex', OTHER)
        self.assertFalse(marker.exists())
        self.assertEqual(len(list(journal.glob('*.md'))), 1)
        self.assertEqual(len(list(archive.glob('*.md'))), 1)

        # Rejecting the marker still leaves this session's own evidence a turn.
        invoked = claude(THIRD)
        invoked[0]['message']['content'] = '<command-name>/probe</command-name> run it'
        marker.write_text('journal-daily', encoding='utf-8')
        os.utime(marker, (stale, stale))
        self.capture(stamped(invoked, time.time()), 'claude', THIRD)
        self.assertEqual(len(list(journal.glob('*.md'))), 1)
        self.assertEqual(len(list((skills / 'probe' / 'conversations').glob('*.md'))), 1)

    def test_marker_provenance_falls_back_to_age_without_timestamps(self):
        """An unstamped source cannot prove provenance, so bound the marker by age."""
        undated = tr.Transcript('ok', 'codex', SID, entries=[{'type': 'session_meta'}])
        self.assertTrue(cc.marker_is_current(time.time(), undated))
        self.assertFalse(cc.marker_is_current(time.time() - cc._MARKER_MAX_AGE_SECONDS - 60, undated))
        dated = tr.Transcript('ok', 'claude', SID, entries=[{'timestamp': '2026-09-05T12:00:00Z'}])
        started = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(cc.transcript_start_time(dated.entries), started)
        self.assertTrue(cc.marker_is_current(started.timestamp() + 1, dated))
        self.assertFalse(cc.marker_is_current(started.timestamp() - 60, dated))

    def test_legacy_and_native_search_rebuild_preserves_originals(self):
        files = self.capture(codex(), 'codex')
        directory = files[0].parent
        legacy = directory / '2026-01-01-1200-legacy.md'
        legacy.write_text('Legacy lunar gardens\n\n**You**: lunar gardens\n', encoding='utf-8')
        unknown = directory / '2026-01-02-1200-unknown.md'
        unknown.write_text('lunar gardens\n\n' + cc.capture_header(OTHER, '', '') + '\n', encoding='utf-8')
        originals = {p: p.read_bytes() for p in directory.glob('*.md')}
        with patch.object(ci.hub_client, 'complete', return_value='Topic: lunar gardens\nDecisions: none\nOpen loops: none'):
            self.assertEqual(ci.index_dir(directory, 'probe', force=True), 3)
        indexed = ci.parse_index(directory / 'index.md')
        self.assertEqual(indexed[files[0].name].turns, 2)
        self.assertNotIn(cc.parse_capture_header(files[0].read_text(encoding='utf-8'))['key'],
                         ci._split_name(files[0].name)[1])
        cfg = cc.capture_config_from_project(self.project)
        self.assertEqual(cs.sync(cfg, rebuild=True), 3)
        hits = cs.search(cfg, 'lunar gardens')
        self.assertEqual(len(hits), 3)
        self.assertEqual(sum(h['resumable'] for h in hits), 1)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)
        cs.db_path(cfg).unlink()
        self.assertEqual(cs.sync(cfg, rebuild=True), 3)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_native_identity_conflicts_and_missing_sid(self):
        self.capture(codex(), 'codex')
        self.assertEqual(tr.read_transcript(self.path, session_id=OTHER).status, 'parse_failure')
        self.assertEqual(tr.read_transcript(self.path, harness='claude').status, 'parse_failure')
        records = codex()
        records[0]['payload'].pop('id')
        self.capture(records, 'codex', '')
        normalized = tr.read_transcript(self.path, harness='codex')
        self.assertEqual(normalized.session_id, '')
        self.assertEqual(cs.resume_command(normalized.harness, normalized.session_id), '')
        self.assertEqual(cs.resume_command('codex', 'id; unexpected-command'), '')

    def test_event_mirrors_and_old_rollout_generation(self):
        records = codex()
        records += [records[-1], {'type': 'response_item', 'payload': {'role': 'assistant',
                     'content': [{'type': 'output_text', 'text': 'duplicate mirror'}]}}]
        files = self.capture(records, 'codex')
        self.assertEqual(files[0].read_text(encoding='utf-8').count('**Codex**:'), 1)
        old = [codex()[0], *[{'type': 'event_msg', 'payload': {'type': kind, 'message': text}}
               for kind, text in [('user_message', PROMPT), ('agent_message', 'old answer')]]]
        self.capture(old, 'codex')
        self.assertEqual(tr.read_transcript(self.path).messages, [('user', PROMPT), ('assistant', 'old answer')])

    def test_legacy_exact_sid_updates_but_prompt_match_does_not(self):
        directory = self.root / 'conversations'
        directory.mkdir()
        exact = directory / '2026-01-01-1200-old-11111111.md'
        exact.write_text('old\n\n' + cc.capture_header(SID, 'claude', '') + '\n**You**: old\n', encoding='utf-8')
        anonymous = directory / '2026-01-01-1201-other.md'
        anonymous.write_text(PROMPT, encoding='utf-8')
        files = self.capture(claude(), 'claude')
        self.assertEqual(set(files), {exact, anonymous})
        self.assertEqual(anonymous.read_text(encoding='utf-8'), PROMPT)
        self.assertIn('Lunar gardens answer', exact.read_text(encoding='utf-8'))

    def test_atomic_failure_preserves_prior_capture(self):
        original = self.capture(codex(), 'codex')[0]
        before = original.read_bytes()
        changed = codex()
        changed[-1]['payload']['item']['content'][0]['text'] = 'updated answer'
        with patch.object(cc.os, 'replace', side_effect=PermissionError('synthetic locked capture')):
            self.capture(changed, 'codex')
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(list(original.parent.glob('.capture-*.tmp')), [])

    def test_sanitized_native_fixtures(self):
        fixture_dir = Path(__file__).parent / 'fixtures'
        for name, agent, turns in [('capture_claude.jsonl', 'claude', 4),
                                   ('capture_codex.jsonl', 'codex', 4),
                                   ('capture_codex_fork.jsonl', 'codex', 2)]:
            result = tr.read_transcript(fixture_dir / name)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(result.harness, agent)
            self.assertEqual(len(result.messages), turns)
            self.assertEqual([role for role, _ in result.messages], ['user', 'assistant'] * (turns // 2))

    def test_codex_routes_by_transcript_inference_without_marker(self):
        """#785 acceptance #1: no marker at all -- Codex must still route by
        transcript inference, not fall straight to _archive."""
        self.project.extra.update(capture_harnesses=['codex'], capture_routing='skills')
        skill = self.root / '.claude' / 'skills' / 'journal-daily'
        skill.mkdir(parents=True)
        records = codex()
        records[2]['payload']['item']['content'][0]['text'] = (
            'Use the journal-daily skill from .claude/skills/journal-daily/SKILL.md.')
        self.capture(records, 'codex')
        routed = list((skill / 'conversations').glob('*.md'))
        self.assertEqual(len(routed), 1)
        self.assertFalse((self.root / 'conversations' / '_archive').exists())

    def test_claude_and_codex_render_identically_modulo_agent(self):
        """#785 acceptance #2: same conversation, differs only in agent/label."""
        messages = [('user', 'Please help me plan the trip'), ('assistant', 'Sure, where to?')]
        claude_doc = cc.render_markdown('d', messages, agent='claude')
        codex_doc = cc.render_markdown('d', messages, agent='codex')
        self.assertEqual(claude_doc.replace('Claude', 'X'), codex_doc.replace('Codex', 'X'))


if __name__ == '__main__':
    unittest.main()

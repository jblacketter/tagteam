"""Role changes preserve historical participants and refuse active mismatch."""
import json
from unittest.mock import patch

import pytest

from tagteam.cycle import init_cycle, add_round, read_status
from tagteam.participants import ParticipantMismatch, check_participants
from tests.test_watcher import _make_processor


def configure(root, lead, reviewer):
    (root / 'tagteam.yaml').write_text(
        f'agents:\n  lead:\n    name: {lead}\n  reviewer:\n    name: {reviewer}\n')


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}


@pytest.mark.parametrize('lead,reviewer', [('codex', 'claude'), ('claude', 'codex')])
@pytest.mark.parametrize('kind', ['plan', 'impl'])
def test_round_trip_then_switch(tmp_path, lead, reviewer, kind):
    configure(tmp_path, lead, reviewer)
    root = str(tmp_path)
    init_cycle('p1', kind, lead, reviewer, 'initial', root)
    add_round('p1', kind, 'reviewer', 'REQUEST_CHANGES', 1, 'fix', root)
    add_round('p1', kind, 'lead', 'SUBMIT_FOR_REVIEW', 2, 'fixed', root)
    add_round('p1', kind, 'reviewer', 'APPROVE', 2, 'ok', root)
    configure(tmp_path, reviewer, lead)
    init_cycle('p2', kind, reviewer, lead, 'next', root)
    assert read_status('p1', kind, root)['lead'] == lead
    assert read_status('p2', kind, root)['lead'] == reviewer
    assert json.loads((tmp_path / 'handoff-state.json').read_text())['updated_by'] == reviewer


@pytest.mark.parametrize('operation', ['init', 'add', 'amend', 'dispatch'])
def test_active_switch_refused_without_mutation(tmp_path, operation):
    configure(tmp_path, 'codex', 'claude')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    configure(tmp_path, 'claude', 'codex')
    before = snapshot(tmp_path)
    if operation == 'dispatch':
        proc = _make_processor(project_dir=root)
        with patch.object(proc, '_handle_ready') as launch:
            proc._dispatch(json.loads((tmp_path / 'handoff-state.json').read_text()))
        launch.assert_not_called()
    else:
        with pytest.raises(ParticipantMismatch):
            if operation == 'init':
                init_cycle('p2', 'plan', 'claude', 'codex', 'next', root)
            else:
                add_round('p1', 'plan', 'lead',
                          'AMEND' if operation == 'amend' else 'SUBMIT_FOR_REVIEW',
                          1, 'edit', root)
    assert snapshot(tmp_path) == before
    assert read_status('p1', 'plan', root)['lead'] == 'codex'


def test_unknown_active_identity_refused(tmp_path):
    configure(tmp_path, 'codex', 'claude')
    (tmp_path / 'handoff-state.json').write_text(json.dumps(
        dict(phase='lost', type='plan', status='ready')))
    with pytest.raises(ParticipantMismatch):
        check_participants(tmp_path)


def test_watcher_refreshes_names_after_completed_cycle(tmp_path):
    configure(tmp_path, 'codex', 'claude')
    proc = _make_processor(project_dir=str(tmp_path), lead_name='old', reviewer_name='other')
    with patch.object(proc, '_handle_done'):
        proc._dispatch(dict(status='done'))
    assert (proc.lead_name, proc.reviewer_name) == ('codex', 'claude')


def test_restoring_config_rearms_existing_cycle(tmp_path):
    configure(tmp_path, 'codex', 'claude')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    configure(tmp_path, 'claude', 'codex')
    with pytest.raises(ParticipantMismatch):
        check_participants(root)
    configure(tmp_path, 'codex', 'claude')
    add_round('p1', 'plan', 'reviewer', 'APPROVE', 1, 'ok', root)
    assert read_status('p1', 'plan', root)['state'] == 'approved'


def test_human_ruling_still_available_on_mismatch(tmp_path):
    from tagteam.cycle import add_ruling
    configure(tmp_path, 'codex', 'claude')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    add_round('p1', 'plan', 'reviewer', 'ESCALATE', 1, 'help', root)
    configure(tmp_path, 'claude', 'codex')
    add_ruling('p1', 'plan', 'APPROVE', 'resolved', 'human', root)
    assert read_status('p1', 'plan', root)['state'] == 'approved'


def test_direct_headless_entry_refuses_before_process(tmp_path):
    from tagteam.headless import HeadlessEngine
    from tagteam.config import read_config
    configure(tmp_path, 'codex', 'claude')
    original = read_config(tmp_path / 'tagteam.yaml')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    configure(tmp_path, 'claude', 'codex')
    messages = []
    engine = HeadlessEngine(root, original, lead_name='codex', reviewer_name='claude',
                            log=messages.append)
    with patch.object(engine, '_run_attempt') as attempt:
        assert engine.run_owed_turn(dict(status='ready', turn='lead')) is None
    attempt.assert_not_called()
    assert len(messages) == 1
    assert 'Participant mismatch:' in messages[0]
    assert 'Agent configuration changed' not in messages[0]


def test_direct_headless_refuses_stale_config_after_cycle_completed(tmp_path):
    from tagteam.headless import HeadlessEngine
    from tagteam.config import read_config
    configure(tmp_path, 'codex', 'claude')
    original = read_config(tmp_path / 'tagteam.yaml')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    add_round('p1', 'plan', 'reviewer', 'APPROVE', 1, 'ok', root)
    configure(tmp_path, 'claude', 'codex')
    messages = []
    engine = HeadlessEngine(root, original, lead_name='codex', reviewer_name='claude',
                            log=messages.append)
    with patch.object(engine, '_run_attempt') as attempt:
        assert engine.run_owed_turn(dict(status='ready', turn='lead')) is None
    attempt.assert_not_called()
    assert len(messages) == 1
    assert 'Agent configuration changed' in messages[0]
    assert 'Participant mismatch:' not in messages[0]


def test_direct_lead_conversation_refuses_before_claim(tmp_path):
    from tagteam.lead_chat import start_turn, LeadChatError
    configure(tmp_path, 'codex', 'claude')
    root = str(tmp_path)
    init_cycle('p1', 'plan', 'codex', 'claude', 'initial', root)
    configure(tmp_path, 'claude', 'codex')
    with pytest.raises(LeadChatError, match='Participant mismatch'):
        start_turn(root, 'conversation', 'continue', config={})

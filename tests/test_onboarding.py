from unittest.mock import patch
import pytest
from tagteam import setup
from tagteam.onboarding import describe_roles
from tagteam.session import create_tmux_session
from tests.test_participants import configure


@pytest.mark.parametrize('lead,reviewer', [('codex', 'claude'), ('claude', 'codex')])
def test_no_claude_setup_and_preserved_rules(tmp_path, lead, reviewer):
    configure(tmp_path, lead, reviewer)
    (tmp_path / 'CLAUDE.md').write_text('Custom rules\n')
    with patch('tagteam.setup.plugin_status', return_value=setup.PluginStatus(False, 'missing')), \
         patch('tagteam.registry.register_project'):
        setup.main(str(tmp_path), report_user_skills=False)
        assert not setup.needs_setup(str(tmp_path))
        assert (tmp_path / 'CLAUDE.md').read_text() == 'Custom rules\n'
        assert 'docs/workflows.md' in (tmp_path / 'AGENTS.md').read_text()
        before = (tmp_path / 'AGENTS.md').stat().st_mtime_ns
        setup.main(str(tmp_path), report_user_skills=False)
        assert (tmp_path / 'AGENTS.md').stat().st_mtime_ns == before
    output = describe_roles(tmp_path)
    assert f'Lead: {lead}' in output and f'Reviewer: {reviewer}' in output
    assert str(tmp_path) in output and 'tagteam contract' in output


def test_readiness_never_queries_claude(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'workflows.md').write_text('x')
    with patch('tagteam.setup.plugin_status', side_effect=AssertionError('not needed')):
        assert not setup.needs_setup(str(tmp_path))
    with patch('tagteam.contract.contract_text', side_effect=OSError('missing')):
        assert setup.needs_setup(str(tmp_path))


def test_tmux_labels_follow_custom_names(tmp_path):
    configure(tmp_path, 'Builder', 'Checker')
    with patch('tagteam.session.session_exists', return_value=False), \
         patch('tagteam.session._tmux') as tmux:
        assert create_tmux_session(str(tmp_path))
    titles = [c.args[-1] for c in tmux.call_args_list if c.args[0] == 'select-pane']
    assert 'Builder (Lead)' in titles and 'Checker (Reviewer)' in titles

"""Tests for server state POST validation."""

import pytest

from tagteam.server import _validate_state_post


class TestValidateStatePost:
    def test_valid_update_passes(self):
        assert _validate_state_post({"turn": "lead", "status": "ready"}) is None

    def test_valid_round_int(self):
        assert _validate_state_post({"round": 3}) is None

    def test_unknown_field_rejected(self):
        err = _validate_state_post({"bogus": "value"})
        assert err is not None
        assert "Unknown field" in err

    def test_wrong_type_turn_list(self):
        err = _validate_state_post({"turn": ["lead"]})
        assert err is not None
        assert "must be str" in err

    def test_wrong_type_round_string(self):
        err = _validate_state_post({"round": "1"})
        assert err is not None
        assert "must be int" in err

    def test_invalid_turn_value(self):
        err = _validate_state_post({"turn": "nobody"})
        assert err is not None
        assert "Invalid value" in err

    def test_invalid_status_value(self):
        err = _validate_state_post({"status": "banana"})
        assert err is not None
        assert "Invalid value" in err

    def test_valid_roadmap_dict(self):
        assert _validate_state_post({"roadmap": {"queue": []}}) is None

    def test_roadmap_wrong_type(self):
        err = _validate_state_post({"roadmap": "not a dict"})
        assert err is not None
        assert "must be dict" in err

    def test_free_text_fields_accept_any_string(self):
        assert _validate_state_post({"command": "anything goes"}) is None
        assert _validate_state_post({"reason": "some reason"}) is None
        assert _validate_state_post({"result": "custom-result"}) is None

    def test_valid_run_mode(self):
        assert _validate_state_post({"run_mode": "full-roadmap"}) is None

    def test_invalid_run_mode(self):
        err = _validate_state_post({"run_mode": "turbo"})
        assert err is not None
        assert "Invalid value" in err

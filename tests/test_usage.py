"""Phase 55: `tagteam usage` roll-ups (--by role|cycle|model|kind), no dollar
figures in the text view, and a read path that never creates or migrates."""

from __future__ import annotations

import io
import json

import pytest

from tagteam import db, usage


def _row(**kw):
    base = dict(ts="2026-09-14T00:00:00+00:00", status="ok", phase="p", type="impl", round=1,
                role="lead", provider="claude", model=None, kind=None, input_tokens=None,
                output_tokens=None, cache_read_tokens=None, cache_write_tokens=None,
                cost_usd=None, duration_ms=1000, model_usage=None)
    base.update(kw)
    return base


class TestAggregate:
    def test_default_shape_unchanged(self):
        agg = usage.aggregate([_row(input_tokens=1)])
        assert set(agg) == {"turns", "by_role", "by_cycle", "totals"}

    def test_by_model_splits_model_usage(self):
        r = _row(model="claude-fable-5-1", input_tokens=19226, output_tokens=29476,
                 model_usage={"claude-fable-5-1": {"input_tokens": 610, "output_tokens": 29464,
                                                   "cache_read_tokens": 2102926},
                              "claude-haiku-4-5-20251001": {"input_tokens": 18616, "output_tokens": 12}})
        agg = usage.aggregate([r], ("model",))
        m = agg["by_model"]
        assert set(m) == {"claude-fable-5-1", "claude-haiku-4-5-20251001"}
        assert m["claude-haiku-4-5-20251001"]["input_tokens"] == 18616
        assert m["claude-fable-5-1"]["cache_read_tokens"] == 2102926
        assert m["claude-fable-5-1"]["turns"] == 1 and m["claude-haiku-4-5-20251001"]["turns"] == 1
        # totals stay row-based: one turn, the row's own counts
        assert agg["totals"]["turns"] == 1 and agg["totals"]["input_tokens"] == 19226

    def test_by_model_fallbacks(self):
        agg = usage.aggregate([_row(model="claude-fable-5", input_tokens=3),
                               _row(provider="codex", input_tokens=7)], ("model",))
        assert agg["by_model"]["claude-fable-5"]["input_tokens"] == 3
        assert agg["by_model"]["codex (model not reported)"]["input_tokens"] == 7

    def test_by_kind(self):
        agg = usage.aggregate([_row(), _row(kind="conversation"), _row(kind="panel:scope")], ("kind",))
        assert set(agg["by_kind"]) == {"turn", "conversation", "panel:scope"}
        assert set(agg) == {"turns", "by_kind", "totals"}


class TestText:
    def test_no_dollars_in_text(self):
        agg = usage.aggregate([_row(input_tokens=1, cost_usd=1.25)], ("role", "cycle", "model", "kind"))
        text = usage.render_text(agg)
        assert "$" not in text and "cost" not in text.lower()
        assert "By model (turns using this model):" in text and "By kind:" in text

    def test_json_keeps_cost(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        db.add_usage(c, ts="t", status="ok", phase="p", role="lead", input_tokens=1, cost_usd=0.5)
        c.close()
        out = io.StringIO()
        assert usage.usage_command(["--json"], project_root=tmp_path, out=out) == 0
        data = json.loads(out.getvalue())
        assert data["totals"]["cost_usd"] == 0.5 and data["turns"][0]["cost_usd"] == 0.5
        out = io.StringIO()
        assert usage.usage_command([], project_root=tmp_path, out=out) == 0
        assert "$" not in out.getvalue() and "cost" not in out.getvalue().lower()


class TestCommand:
    def test_invalid_by(self, tmp_path):
        out = io.StringIO()
        assert usage.usage_command(["--by", "dollars"], project_root=tmp_path, out=out) == 1
        assert "--by must be one of" in out.getvalue()

    def test_repeatable_by_replaces_default_blocks(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        db.add_usage(c, ts="t", status="ok", phase="p", role="lead", kind="conversation", input_tokens=1)
        c.close()
        out = io.StringIO()
        assert usage.usage_command(["--by", "kind", "--by", "model", "--json"],
                                   project_root=tmp_path, out=out) == 0
        data = json.loads(out.getvalue())
        assert "by_kind" in data and "by_model" in data and "by_role" not in data

    def test_no_database_creates_nothing(self, tmp_path):
        out = io.StringIO()
        assert usage.usage_command([], project_root=tmp_path, out=out) == 0
        assert "No usage rows yet" in out.getvalue()
        assert not (tmp_path / ".tagteam").exists()

"""Tests for the review dialogue builder."""

import pytest

from ai_handoff.tui.review_dialogue import strip_markdown, _chunk_text


class TestStripMarkdown:
    def test_removes_heading_prefixes(self):
        assert strip_markdown("## Heading") == "Heading"
        assert strip_markdown("### Sub") == "Sub"

    def test_removes_bold(self):
        assert strip_markdown("**bold text**") == "bold text"

    def test_removes_italic(self):
        assert strip_markdown("_italic text_") == "italic text"

    def test_removes_bullet_prefixes(self):
        result = strip_markdown("- item one\n- item two")
        assert "item one" in result
        assert "item two" in result
        assert result.startswith("item")

    def test_removes_numbered_list(self):
        result = strip_markdown("1. first\n2. second")
        assert "first" in result
        assert "second" in result

    def test_removes_inline_code(self):
        assert strip_markdown("use `foo()` here") == "use foo() here"

    def test_collapses_whitespace(self):
        result = strip_markdown("hello\n\n\nworld")
        assert result == "hello world"

    def test_empty_string(self):
        assert strip_markdown("") == ""

    def test_plain_text_unchanged(self):
        assert strip_markdown("just plain text") == "just plain text"


class TestChunkText:
    def test_short_text_single_chunk(self):
        result = _chunk_text("Short text.", max_len=140)
        assert len(result) == 1
        assert result[0] == "Short text."

    def test_long_text_splits(self):
        long = "This is a sentence. " * 20
        result = _chunk_text(long, max_len=100)
        assert len(result) > 1
        # Chunks may slightly exceed max_len when seeking sentence boundaries
        assert all(len(c) <= 160 for c in result)

    def test_max_chunks_limit(self):
        very_long = "Word. " * 200
        result = _chunk_text(very_long, max_len=50, max_chunks=3)
        assert len(result) <= 3

    def test_ellipsis_on_truncation(self):
        very_long = "This is a long sentence that goes on and on. " * 20
        result = _chunk_text(very_long, max_len=50, max_chunks=2)
        assert result[-1].endswith("...")

    def test_empty_text(self):
        result = _chunk_text("")
        assert result == [""]

    def test_exact_boundary(self):
        text = "A" * 140
        result = _chunk_text(text, max_len=140)
        assert len(result) == 1

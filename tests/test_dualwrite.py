"""Tests for the dual-write infrastructure (`tagteam.dualwrite`).

Covers the writer lock, the `db_invalid` sentinel, and the
`skip_inner_dualwrite` thread-local layering helper. No live writers
are wired to this module yet, so these tests exercise the primitives
in isolation.
"""

import json
import os
import threading
from pathlib import Path

import pytest

from tagteam import dualwrite


@pytest.fixture
def project(tmp_path):
    return tmp_path


# ---------- Writer lock ----------

class TestWriterLock:
    def test_acquires_and_releases(self, project):
        with dualwrite.writer_lock(project):
            pass
        # Lock file remains (it's a sentinel for the file-based flock),
        # but the lock is released — re-acquiring must not block.
        with dualwrite.writer_lock(project):
            pass

    def test_creates_tagteam_dir(self, project):
        assert not (project / ".tagteam").exists()
        with dualwrite.writer_lock(project):
            assert (project / ".tagteam").exists()
            assert (project / ".tagteam" / ".write.lock").exists()

    def test_writes_holder_info(self, project):
        with dualwrite.writer_lock(project):
            holder = dualwrite.lock_holder(project)
        assert holder is not None
        pid, ts = holder
        assert pid == os.getpid()
        # ISO 8601 timestamp with timezone offset
        assert "T" in ts and ("+" in ts or ts.endswith("Z"))

    def test_released_on_exception(self, project):
        with pytest.raises(RuntimeError, match="boom"):
            with dualwrite.writer_lock(project):
                raise RuntimeError("boom")
        # If the lock leaked, this second acquisition would block.
        # Use a thread to make a deadlock detectable instead of hanging
        # the test runner.
        acquired = threading.Event()

        def try_acquire():
            with dualwrite.writer_lock(project):
                acquired.set()

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=2)
        assert acquired.is_set(), "writer_lock leaked after exception"

    def test_lock_holder_missing_returns_none(self, project):
        assert dualwrite.lock_holder(project) is None

    def test_lock_holder_empty_file(self, project):
        (project / ".tagteam").mkdir()
        (project / ".tagteam" / ".write.lock").write_text("")
        assert dualwrite.lock_holder(project) is None

    def test_lock_holder_unparseable(self, project):
        (project / ".tagteam").mkdir()
        (project / ".tagteam" / ".write.lock").write_text("garbage no spaces")
        assert dualwrite.lock_holder(project) is None

    def test_reentrant_same_thread(self, project):
        """A nested call from the same thread must not deadlock — the
        outer call already holds the OS lock, the inner call is a
        no-op acquisition."""
        with dualwrite.writer_lock(project):
            with dualwrite.writer_lock(project):
                with dualwrite.writer_lock(project):
                    pass

    def test_reentrant_inner_exception_releases_outer_correctly(self, project):
        """An exception in a nested call must not corrupt the depth
        counter. After the outer call exits, a fresh acquisition must
        succeed."""
        try:
            with dualwrite.writer_lock(project):
                try:
                    with dualwrite.writer_lock(project):
                        raise RuntimeError("inner boom")
                except RuntimeError:
                    pass
                # Still inside outer, lock still held by us.
                with dualwrite.writer_lock(project):
                    pass
        except Exception:
            pytest.fail("outer should have completed cleanly")

        # After outer exits, second top-level acquisition works.
        # Use a thread to make a deadlock detectable.
        acquired = threading.Event()

        def try_acquire():
            with dualwrite.writer_lock(project):
                acquired.set()

        t = threading.Thread(target=try_acquire)
        t.start()
        t.join(timeout=2)
        assert acquired.is_set()

    def test_cross_thread_serialization(self, project):
        """Two threads must serialize. While thread A holds the lock,
        thread B's acquisition blocks until A releases."""
        b_acquired = threading.Event()
        a_can_release = threading.Event()
        order = []

        def thread_a():
            with dualwrite.writer_lock(project):
                order.append("a_in")
                a_can_release.wait(timeout=2)
                order.append("a_out")

        def thread_b():
            with dualwrite.writer_lock(project):
                order.append("b_in")
                b_acquired.set()

        ta = threading.Thread(target=thread_a)
        ta.start()
        # Let A get inside the critical section.
        while "a_in" not in order:
            pass
        tb = threading.Thread(target=thread_b)
        tb.start()
        # B should NOT have acquired yet — A is still holding.
        assert not b_acquired.wait(timeout=0.2)
        # Release A; B should then proceed.
        a_can_release.set()
        ta.join(timeout=2)
        tb.join(timeout=2)
        assert b_acquired.is_set()
        # Order proves serialization.
        assert order == ["a_in", "a_out", "b_in"]


# ---------- db_invalid sentinel ----------

class TestDbInvalidSentinel:
    def test_initially_clear(self, project):
        assert dualwrite.is_db_invalid(project) is False
        assert dualwrite.get_db_invalid_info(project) is None

    def test_mark_then_is_invalid(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        assert dualwrite.is_db_invalid(project) is True

    def test_mark_records_reason_and_since(self, project):
        dualwrite.mark_db_invalid(project, reason="db write failed: foo")
        info = dualwrite.get_db_invalid_info(project)
        assert info is not None
        assert info["reason"] == "db write failed: foo"
        assert "since" in info
        assert "updated_at" in info

    def test_idempotent_mark_preserves_earliest_since(self, project):
        dualwrite.mark_db_invalid(project, reason="first")
        first = dualwrite.get_db_invalid_info(project)
        first_since = first["since"]

        # Sleep-free: the second mark in the same microsecond would
        # produce the same timestamp; we just need to check that the
        # idempotent path preserves whatever `since` was already there.
        dualwrite.mark_db_invalid(project, reason="second")
        second = dualwrite.get_db_invalid_info(project)
        assert second["since"] == first_since
        assert second["reason"] == "second"
        # updated_at advances (or at least equals on same-microsecond)
        assert second["updated_at"] >= first["updated_at"]

    def test_clear(self, project):
        dualwrite.mark_db_invalid(project, reason="test")
        assert dualwrite.is_db_invalid(project)
        dualwrite.clear_db_invalid(project)
        assert dualwrite.is_db_invalid(project) is False
        assert dualwrite.get_db_invalid_info(project) is None

    def test_clear_when_not_set_is_noop(self, project):
        # No-op, must not raise
        dualwrite.clear_db_invalid(project)
        assert dualwrite.is_db_invalid(project) is False

    def test_corrupt_flag_file_treated_as_invalid_no_detail(self, project):
        (project / ".tagteam").mkdir()
        (project / ".tagteam" / "DB_INVALID").write_text("not json")
        assert dualwrite.is_db_invalid(project) is True
        # Corrupt -> empty dict, distinguishable from "not set"
        info = dualwrite.get_db_invalid_info(project)
        assert info == {}

    def test_authority_flag_file_not_db_column(self, project):
        """The flag file is authoritative; `is_db_invalid` does not
        consult the DB. Verify by setting the flag with no DB at all."""
        # No DB connection passed; no .tagteam/tagteam.db exists.
        dualwrite.mark_db_invalid(project, reason="db unavailable")
        assert dualwrite.is_db_invalid(project) is True
        # No SQLite errors raised even though no DB exists.

    def test_mark_does_not_require_db(self, project):
        """`mark_db_invalid` writes only the flag file. No DB
        connection is needed — the whole point is that this works
        when SQLite is unavailable."""
        # No tagteam.db exists at all.
        assert not (project / ".tagteam" / "tagteam.db").exists()
        dualwrite.mark_db_invalid(project, reason="DB completely unavailable")
        assert dualwrite.is_db_invalid(project) is True
        info = dualwrite.get_db_invalid_info(project)
        assert info["reason"] == "DB completely unavailable"


# ---------- skip_inner_dualwrite layering helper ----------

class TestSkipInnerDualwrite:
    def test_default_false(self):
        assert dualwrite.should_skip_inner_dualwrite() is False

    def test_inside_block_true(self):
        with dualwrite.skip_inner_dualwrite():
            assert dualwrite.should_skip_inner_dualwrite() is True
        assert dualwrite.should_skip_inner_dualwrite() is False

    def test_reset_on_exception(self):
        with pytest.raises(RuntimeError):
            with dualwrite.skip_inner_dualwrite():
                assert dualwrite.should_skip_inner_dualwrite() is True
                raise RuntimeError("boom")
        # After exception, flag must be cleared. This is the bug the
        # context-manager-based implementation prevents — a bare
        # try/finally with a setattr() on the thread-local would still
        # work, but a context manager makes the exception path
        # impossible to forget.
        assert dualwrite.should_skip_inner_dualwrite() is False

    def test_reentrant_via_depth_counter(self):
        with dualwrite.skip_inner_dualwrite():
            assert dualwrite.should_skip_inner_dualwrite() is True
            with dualwrite.skip_inner_dualwrite():
                assert dualwrite.should_skip_inner_dualwrite() is True
            # Inner exit must NOT clear the outer skip
            assert dualwrite.should_skip_inner_dualwrite() is True
        assert dualwrite.should_skip_inner_dualwrite() is False

    def test_thread_isolation(self):
        """Skip flag is per-thread. Setting it in one thread must not
        leak into another."""
        results = {}

        def worker(name):
            results[name] = dualwrite.should_skip_inner_dualwrite()

        with dualwrite.skip_inner_dualwrite():
            t = threading.Thread(target=worker, args=("background",))
            t.start()
            t.join()
            results["foreground"] = dualwrite.should_skip_inner_dualwrite()

        assert results["foreground"] is True
        assert results["background"] is False


# ---------- Cross-cutting: lock + sentinel together ----------

class TestIntegration:
    def test_mark_invalid_under_lock(self, project):
        """Typical caller pattern: hold the writer lock, then mark
        the DB invalid. Must work without contention against itself."""
        with dualwrite.writer_lock(project):
            dualwrite.mark_db_invalid(project, reason="test")
        assert dualwrite.is_db_invalid(project)

    def test_lock_does_not_interfere_with_sentinel_read(self, project):
        """`is_db_invalid` does not need the writer lock — readers
        must work under contention."""
        dualwrite.mark_db_invalid(project, reason="x")

        def acquire_and_hold():
            with dualwrite.writer_lock(project):
                # Hold briefly while the main thread checks the flag.
                read_ready.wait(timeout=2)

        read_ready = threading.Event()
        t = threading.Thread(target=acquire_and_hold)
        t.start()
        try:
            # While the lock is held, reads of the sentinel still work.
            assert dualwrite.is_db_invalid(project) is True
        finally:
            read_ready.set()
            t.join(timeout=2)

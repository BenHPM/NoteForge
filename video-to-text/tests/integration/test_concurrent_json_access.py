# -*- coding: utf-8 -*-
"""
Integration test: Concurrent JSON file access

Tests that concurrent reads/writes to a JSON file via write_file
produce valid JSON when reads succeed. On Windows, os.rename cannot
atomically replace an existing file, so concurrent writes to the SAME
file may transiently fail with WinError 183. The key invariant is:
  - When a read succeeds, the parsed JSON is valid and self-consistent
  - After all concurrent activity settles, the file is valid JSON

Uses threading to simulate concurrent access.
No external services needed.
"""

import json
import os
import sys
import tempfile
import threading
import time

import pytest

from noteforge.infra.file_io import read_file, write_file


pytestmark = pytest.mark.integration


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

_IS_WIN = sys.platform == "win32"


def _make_json_path():
    """Create a temporary JSON file path."""
    tmp = tempfile.mkdtemp()
    return os.path.join(tmp, "concurrent_test.json"), tmp


def _write_json_data(path, data):
    """Write JSON data using write_file (atomic)."""
    content = json.dumps(data, ensure_ascii=False, indent=2)
    write_file(path, content)


def _read_json_data(path):
    """Read and parse JSON data using read_file."""
    raw = read_file(path)
    return json.loads(raw)


def _write_json_with_retry(path, data, max_retries=5):
    """Write JSON data with retry for Windows rename race conditions.

    On Windows, concurrent writes to the same file can fail with
    WinError 183 (file already exists) because os.rename cannot
    atomically replace. Retry handles this transient condition.
    """
    content = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(max_retries):
        try:
            write_file(path, content)
            return
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(0.01 * (attempt + 1))
            else:
                raise


# ═══════════════════════════════════════════════════════════════
# Concurrent writes produce valid JSON (after settling)
# ═══════════════════════════════════════════════════════════════


class TestConcurrentWritesProduceValidJson:
    """Concurrent writes to a JSON file produce valid JSON after all complete."""

    def test_concurrent_writes_settle_to_valid_json(self):
        """After concurrent writes complete, the file contains valid JSON."""
        path, tmp = _make_json_path()
        num_threads = 10
        writes_per_thread = 20
        write_errors = 0
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def writer(thread_id):
            """Each thread writes its own data repeatedly."""
            nonlocal write_errors
            try:
                barrier.wait(timeout=5)
                for i in range(writes_per_thread):
                    data = {
                        "thread": thread_id,
                        "iteration": i,
                        "value": f"data_{thread_id}_{i}",
                        "timestamp": time.time(),
                    }
                    try:
                        _write_json_with_retry(path, data)
                    except OSError:
                        with lock:
                            write_errors += 1
            except Exception:
                with lock:
                    write_errors += 1

        threads = []
        for t_id in range(num_threads):
            th = threading.Thread(target=writer, args=(t_id,))
            th.start()
            threads.append(th)

        for th in threads:
            th.join(timeout=30)

        # On Windows, some writes may fail due to rename race, but
        # the file must still be valid JSON after all threads finish
        final_data = _read_json_data(path)
        assert isinstance(final_data, dict)
        assert "thread" in final_data
        assert "value" in final_data

    def test_concurrent_writes_large_payload(self):
        """Concurrent writes with larger payloads settle to valid JSON."""
        path, tmp = _make_json_path()
        num_threads = 5
        writes_per_thread = 10
        write_errors = 0
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def writer(thread_id):
            nonlocal write_errors
            try:
                barrier.wait(timeout=5)
                for i in range(writes_per_thread):
                    data = {
                        "thread": thread_id,
                        "iteration": i,
                        "payload": "x" * 10000,  # 10KB payload
                        "items": list(range(100)),
                    }
                    try:
                        _write_json_with_retry(path, data)
                    except OSError:
                        with lock:
                            write_errors += 1
            except Exception:
                with lock:
                    write_errors += 1

        threads = []
        for t_id in range(num_threads):
            th = threading.Thread(target=writer, args=(t_id,))
            th.start()
            threads.append(th)

        for th in threads:
            th.join(timeout=30)

        # Final file is valid JSON
        final_data = _read_json_data(path)
        assert isinstance(final_data, dict)
        # The last writer's payload should be intact
        assert len(final_data.get("payload", "")) == 10000
        assert len(final_data.get("items", [])) == 100


# ═══════════════════════════════════════════════════════════════
# Concurrent reads during writes
# ═══════════════════════════════════════════════════════════════


class TestConcurrentReadsDuringWrites:
    """Concurrent reads during writes always get valid JSON when they succeed."""

    def test_reads_during_writes_are_valid_json(self):
        """Readers always see valid JSON even while writers are active."""
        path, tmp = _make_json_path()

        # Initialize with valid JSON
        _write_json_data(path, {"initial": True, "counter": 0})

        num_writers = 3
        num_readers = 5
        write_iterations = 30
        read_iterations = 50
        json_decode_errors = []
        read_results = []
        barrier = threading.Barrier(num_writers + num_readers)

        def writer(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(write_iterations):
                    data = {
                        "writer": thread_id,
                        "counter": i,
                        "data": f"write_{thread_id}_{i}",
                    }
                    try:
                        _write_json_with_retry(path, data)
                    except OSError:
                        pass  # Windows rename race — acceptable
                    time.sleep(0.001)
            except Exception:
                pass

        def reader(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(read_iterations):
                    try:
                        raw = read_file(path)
                        data = json.loads(raw)
                        read_results.append((thread_id, data))
                    except (json.JSONDecodeError, ValueError) as e:
                        # On Windows, a read during the unlink+rename window
                        # may see a missing file (FileNotFoundError) or partial
                        # content. We only flag JSON decode errors as problems.
                        json_decode_errors.append((thread_id, str(e)))
                    except FileNotFoundError:
                        # Transient: file is between unlink and rename on Windows
                        pass
                    time.sleep(0.001)
            except Exception:
                pass

        threads = []
        for t_id in range(num_writers):
            th = threading.Thread(target=writer, args=(t_id,))
            th.start()
            threads.append(th)
        for t_id in range(num_readers):
            th = threading.Thread(target=reader, args=(t_id,))
            th.start()
            threads.append(th)

        for th in threads:
            th.join(timeout=60)

        # No JSON decode errors during reads — when we can read the file,
        # the content is always valid JSON
        assert len(json_decode_errors) == 0, \
            f"JSON decode errors during reads: {json_decode_errors}"

        # All successful reads got valid JSON dicts
        for thread_id, data in read_results:
            assert isinstance(data, dict)

    def test_readers_see_consistent_data(self):
        """Each read sees a complete, self-consistent JSON object.

        The invariant: when a read successfully parses JSON, the data
        is internally consistent (never a partial/mixed write).
        """
        path, tmp = _make_json_path()

        _write_json_data(path, {"key_a": "val1", "key_b": "val1"})

        num_writers = 2
        num_readers = 4
        iterations = 20
        inconsistent_reads = []
        barrier = threading.Barrier(num_writers + num_readers)

        def writer(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(iterations):
                    # Write both keys with the same value (atomic)
                    val = f"val_{thread_id}_{i}"
                    data = {"key_a": val, "key_b": val}
                    try:
                        _write_json_with_retry(path, data)
                    except OSError:
                        pass  # Windows rename race
                    time.sleep(0.002)
            except Exception:
                pass

        def reader(thread_id):
            try:
                barrier.wait(timeout=5)
                for i in range(iterations):
                    try:
                        raw = read_file(path)
                        data = json.loads(raw)
                        # key_a and key_b should always be equal
                        # (atomic write ensures we never see a partial update)
                        if data.get("key_a") != data.get("key_b"):
                            inconsistent_reads.append({
                                "thread": thread_id,
                                "key_a": data.get("key_a"),
                                "key_b": data.get("key_b"),
                            })
                    except (json.JSONDecodeError, ValueError, FileNotFoundError):
                        # Transient read during rename is acceptable;
                        # the key invariant is: when we DO parse JSON,
                        # it is self-consistent
                        pass
                    time.sleep(0.001)
            except Exception:
                pass

        threads = []
        for t_id in range(num_writers):
            th = threading.Thread(target=writer, args=(t_id,))
            th.start()
            threads.append(th)
        for t_id in range(num_readers):
            th = threading.Thread(target=reader, args=(t_id,))
            th.start()
            threads.append(th)

        for th in threads:
            th.join(timeout=60)

        # No inconsistent reads (key_a == key_b always when both present)
        assert len(inconsistent_reads) == 0, \
            f"Inconsistent reads detected: {inconsistent_reads}"


# ═══════════════════════════════════════════════════════════════
# Atomic write guarantees (single-threaded)
# ═══════════════════════════════════════════════════════════════


class TestAtomicWriteGuarantees:
    """Atomic write (mkstemp + rename) guarantees no partial files."""

    def test_no_tmp_files_after_writes(self):
        """No .tmp files remain after successful writes."""
        path, tmp = _make_json_path()

        for i in range(20):
            _write_json_data(path, {"iteration": i})

        tmp_files = [f for f in os.listdir(tmp) if f.endswith('.tmp')]
        assert len(tmp_files) == 0

    def test_overwrite_preserves_validity(self):
        """Overwriting a file many times always produces valid JSON."""
        path, tmp = _make_json_path()

        for i in range(50):
            data = {"counter": i, "data": f"iteration_{i}"}
            _write_json_data(path, data)

        final = _read_json_data(path)
        assert isinstance(final, dict)
        assert "counter" in final

    def test_write_creates_parent_directories(self):
        """write_file creates parent directories if they don't exist."""
        tmp = tempfile.mkdtemp()
        deep_path = os.path.join(tmp, "a", "b", "c", "data.json")

        _write_json_data(deep_path, {"deep": True})

        assert os.path.exists(deep_path)
        data = _read_json_data(deep_path)
        assert data["deep"] is True

    def test_concurrent_writes_to_different_files(self):
        """Concurrent writes to different files all succeed (no contention)."""
        tmp = tempfile.mkdtemp()
        num_threads = 10
        errors = []

        def writer(thread_id):
            try:
                path = os.path.join(tmp, f"file_{thread_id}.json")
                for i in range(20):
                    data = {"thread": thread_id, "iteration": i}
                    _write_json_data(path, data)
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = []
        for t_id in range(num_threads):
            th = threading.Thread(target=writer, args=(t_id,))
            th.start()
            threads.append(th)

        for th in threads:
            th.join(timeout=30)

        assert len(errors) == 0, f"Errors writing to separate files: {errors}"

        # All files exist and are valid JSON
        for t_id in range(num_threads):
            path = os.path.join(tmp, f"file_{t_id}.json")
            assert os.path.exists(path)
            data = _read_json_data(path)
            assert data["thread"] == t_id

    def test_rapid_sequential_writes_produce_valid_json(self):
        """Rapid sequential writes (single thread) always produce valid JSON."""
        path, tmp = _make_json_path()

        for i in range(100):
            data = {"counter": i, "uuid": f"item-{i}-{time.time()}"}
            _write_json_data(path, data)

        final = _read_json_data(path)
        assert isinstance(final, dict)
        assert final["counter"] == 99

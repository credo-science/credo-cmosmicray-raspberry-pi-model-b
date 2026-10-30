import json
import sys
import threading
import time

import pytest


@pytest.fixture
def cmr(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    import CMOSmicRay as cmr

    log_file = tmp_path / "detections.json"
    monkeypatch.setattr(cmr.store, "log_file", str(log_file))
    return cmr


def _payload(i):
    return {"detections": [{"id": i, "timestamp": i}]}


def test_save_local_log_appends(cmr):
    cmr.store.save_local_log(_payload(1))
    cmr.store.save_local_log(_payload(2))
    data = json.loads(open(cmr.store.log_file).read())
    assert [d["id"] for d in data] == [1, 2]


def test_save_local_log_trims_to_1000(cmr):
    # Seed the file with 1000 existing entries, then add one more -- the
    # oldest entry should be dropped, not the newest.
    seed = [{"id": i} for i in range(1000)]
    with open(cmr.store.log_file, "w") as f:
        json.dump(seed, f)

    cmr.store.save_local_log(_payload(1000))

    data = json.loads(open(cmr.store.log_file).read())
    assert len(data) == 1000
    assert data[0]["id"] == 1
    assert data[-1]["id"] == 1000


def test_save_local_log_recovers_from_corrupt_json(cmr):
    with open(cmr.store.log_file, "w") as f:
        f.write("{not valid json")

    cmr.store.save_local_log(_payload(1))

    data = json.loads(open(cmr.store.log_file).read())
    assert [d["id"] for d in data] == [1]


def test_save_local_log_missing_detections_key_does_not_raise(cmr, caplog):
    # payload without a "detections" list -- should log and return, not crash
    # the caller (this is what the (OSError, KeyError, IndexError) catch guards).
    cmr.store.save_local_log({})
    assert not __import__("os").path.exists(cmr.store.log_file)


def test_concurrent_hits_dont_corrupt_log(cmr):
    # Regression test for the bug class this diff's threading change fixes:
    # concurrent writers racing on detections.json. Route N payloads through
    # the real store.queue + store.writer_loop plumbing (not direct calls)
    # so this actually exercises the single-writer-thread design.
    writer = threading.Thread(target=cmr.store.writer_loop, daemon=True)
    writer.start()

    N = 50
    for i in range(N):
        cmr.store.queue.put(_payload(i))

    cmr.store.queue.join()

    data = json.loads(open(cmr.store.log_file).read())
    assert len(data) == N
    assert sorted(d["id"] for d in data) == list(range(N))


def test_local_log_writer_survives_bad_payload_without_deadlocking_join(cmr):
    # Regression test: a payload that makes save_local_log() raise something
    # outside its own narrow except clause used to kill the writer thread
    # AND skip task_done() -- which would make shutdown()'s
    # store.queue.join() hang forever. Confirms the try/finally fix.
    writer = threading.Thread(target=cmr.store.writer_loop, daemon=True)
    writer.start()

    cmr.store.queue.put("not-a-dict-will-raise-TypeError-on-subscript")
    cmr.store.queue.put(_payload(1))

    joined = threading.Event()

    def _join_then_signal():
        cmr.store.queue.join()
        joined.set()

    joiner = threading.Thread(target=_join_then_signal, daemon=True)
    joiner.start()
    joiner.join(timeout=5)

    assert joined.is_set(), "store.queue.join() deadlocked after a bad payload"
    data = json.loads(open(cmr.store.log_file).read())
    assert [d["id"] for d in data] == [1]

import numpy as np
import pytest


@pytest.fixture
def cmr(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    import CMOSmicRay as cmr

    # process_hit fires off a real network upload, a real weather call, and
    # enqueues onto a background writer thread as side effects -- stub all
    # three so this test stays a pure unit test of the crop-bounds math, not
    # an integration test.
    monkeypatch.setattr(cmr.store, "log_file", str(tmp_path / "detections.json"))
    monkeypatch.setattr(cmr, "upload_executor", _ImmediateExecutor())
    monkeypatch.setattr(cmr.store, "queue", _SynchronousQueue(cmr))
    monkeypatch.setattr(cmr.environment, "get_pressure", lambda lat, lng: 1000.0)
    monkeypatch.setattr(cmr.environment, "get_outdoor_temp", lambda: 15.0)
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: _FakeResponse())
    monkeypatch.setattr(cmr, "config", cmr.AppConfig(
        station_id="test-station", credo_user="test-user", credo_pass="test-pass",
        device_model="test-device", software_version="v0-test",
        lat=1.0, lng=2.0, alt=3.0,
        camera_profile_key="test-camera", camera_profile=cmr.HARDWARE_PROFILES["GENERIC"],
        public_dir=str(tmp_path),
    ))
    return cmr


class _ImmediateExecutor:
    """Runs submitted work synchronously so tests don't race a background thread."""

    def submit(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            pass  # upload_worker already swallows its own errors; this just
            # prevents a stray network call from failing the test process.


class _SynchronousQueue:
    """Replaces store.queue so save_local_log runs inline instead of
    requiring a real writer_loop background thread in every test."""

    def __init__(self, cmr):
        self._cmr = cmr

    def put(self, payload):
        self._cmr.store.save_local_log(payload)


class _FakeResponse:
    """Stands in for requests.post so upload_worker's login()/upload call
    inside the ImmediateExecutor never makes a real network call."""

    def __init__(self, status_code=200, json_data=None, text="ok"):
        self.status_code = status_code
        self._json_data = json_data or {"token": "test-token"}
        self.text = text

    def json(self):
        return self._json_data


def _frame(h=720, w=1280):
    return np.zeros((h, w), dtype=np.uint8)


def test_crop_centered_hit_is_full_size(cmr):
    frame = _frame()
    cmr.process_hit(frame, x=640, y=360, max_val=99)
    data = __import__("json").load(open(cmr.store.log_file))
    assert data[0]["x"] == 640
    assert data[0]["y"] == 360


def test_crop_near_top_left_corner_clamps_in_bounds(cmr):
    # x=0, y=0 would put the crop window off-frame if not clamped; process_hit
    # must not raise and must still record the original hit coordinates.
    frame = _frame()
    cmr.process_hit(frame, x=0, y=0, max_val=99)
    data = __import__("json").load(open(cmr.store.log_file))
    assert data[0]["x"] == 0
    assert data[0]["y"] == 0


def test_crop_near_bottom_right_corner_clamps_in_bounds(cmr):
    frame = _frame(h=720, w=1280)
    cmr.process_hit(frame, x=1279, y=719, max_val=99)
    data = __import__("json").load(open(cmr.store.log_file))
    assert data[0]["x"] == 1279
    assert data[0]["y"] == 719


def test_crop_on_frame_smaller_than_crop_size_does_not_raise(cmr):
    # CROP_SIZE defaults to 64; a smaller frame is a degenerate but real
    # input if the camera falls back to a low-res mode.
    frame = _frame(h=32, w=32)
    cmr.process_hit(frame, x=16, y=16, max_val=99)
    data = __import__("json").load(open(cmr.store.log_file))
    assert data[0]["max"] == 99

import numpy as np
import pytest


@pytest.fixture
def cmr(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    import CMOSmicRay as cmr
    return cmr


def _frame(value, h=64, w=64):
    return np.full((h, w), value, dtype=np.uint8)


def test_first_frame_seeds_background_and_returns_none(cmr):
    # The first frame has nothing to diff against -- it just seeds the
    # running background estimate.
    detector = cmr.MotionDetector()
    result = detector.detect(_frame(50))
    assert result is None
    assert detector.bg_frame is not None


def test_stable_frames_produce_zero_diff(cmr):
    detector = cmr.MotionDetector()
    detector.detect(_frame(50))  # seed
    max_val, x, y, diff = detector.detect(_frame(50))
    assert max_val == 0


def test_bright_spot_is_detected_above_background(cmr):
    detector = cmr.MotionDetector()
    detector.detect(_frame(50))  # seed

    frame = _frame(50)
    frame[10, 20] = 255  # simulated hit at row=10, col=20
    max_val, x, y, diff = detector.detect(frame)

    assert max_val > 0
    assert (x, y) == (20, 10)  # minMaxLoc reports (col, row) == (x, y)

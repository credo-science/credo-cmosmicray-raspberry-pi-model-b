"""Tests for the detection/safety gates: spatial filter, multi-trace scan,
obscuration, thermal tiers, calibration gate, trace CSV and the archive."""
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import CMOSmicRay as cmr

PROFILE = cmr.HARDWARE_PROFILES["GENERIC"]  # min_absolute=80, ratio 3.5 / floor 55


def blank(h=200, w=300):
    return np.zeros((h, w), dtype=np.uint8), np.full((h, w), 5.0)


def blob(img, x, y, peak=200):
    img[y - 1:y + 2, x - 1:x + 2] = peak // 2
    img[y, x] = peak


# --- spatial filter ---------------------------------------------------------
def test_isolated_pixel_is_not_spatially_valid():
    img, _ = blank()
    img[50, 50] = 200
    assert not cmr.is_spatially_valid(img, 50, 50, 200)


def test_clustered_pixels_are_spatially_valid():
    img, _ = blank()
    blob(img, 50, 50)
    assert cmr.is_spatially_valid(img, 50, 50, 200)


def test_spatial_check_is_safe_at_frame_edge():
    img, _ = blank()
    img[0, 0] = 200
    assert not cmr.is_spatially_valid(img, 0, 0, 200)


# --- scan_frame -------------------------------------------------------------
def test_scan_accepts_a_track():
    img, bg = blank()
    blob(img, 100, 80)
    ev = cmr.scan_frame(img, bg, PROFILE)
    assert [e[0] for e in ev] == [cmr.ACCEPTED]
    assert (ev[0][2], ev[0][3]) == (100, 80)


def test_scan_rejects_hot_pixel_as_spatial():
    img, bg = blank()
    img[80, 100] = 200
    assert [e[0] for e in cmr.scan_frame(img, bg, PROFILE)] == [cmr.REJECTED_SPATIAL]


def test_scan_logs_near_miss_as_sensitivity_reject():
    img, bg = blank()
    blob(img, 100, 80, peak=60)  # >= 0.7*80 but < 80
    bg[:] = 40.0                 # adaptive gate now needs 3.5*40 = 140
    assert [e[0] for e in cmr.scan_frame(img, bg, PROFILE)] == [cmr.REJECTED_SENSITIVITY]


def test_scan_ignores_faint_noise():
    img, bg = blank()
    img[80, 100] = 20
    assert cmr.scan_frame(img, bg, PROFILE) == []


def test_scan_finds_two_separate_tracks():
    img, bg = blank()
    blob(img, 60, 60, 220)
    blob(img, 220, 140, 180)
    ev = cmr.scan_frame(img, bg, PROFILE)
    assert [e[0] for e in ev] == [cmr.ACCEPTED, cmr.ACCEPTED]
    assert {(e[2], e[3]) for e in ev} == {(60, 60), (220, 140)}


def test_scan_does_not_double_count_one_track():
    img, bg = blank()
    blob(img, 100, 80)
    img[80, 110] = 90  # tail inside the same 64x64 ROI
    assert len(cmr.scan_frame(img, bg, PROFILE)) == 1


def test_scan_leaves_input_frame_unmodified():
    img, bg = blank()
    blob(img, 100, 80)
    before = img.copy()
    cmr.scan_frame(img, bg, PROFILE)
    assert np.array_equal(img, before)


def test_scan_respects_max_traces():
    img, bg = blank(h=400, w=600)
    for i in range(5):
        blob(img, 40 + i * 100, 50 + i * 60)
    assert len(cmr.scan_frame(img, bg, PROFILE, max_traces=3)) == 3


# --- obscuration ------------------------------------------------------------
def test_covered_lens_passes():
    ok, avg, black = cmr.check_obscuration(np.full((100, 100), 1, np.uint8), 10.0, 0.05)
    assert ok and avg == 1.0 and black == 1.0


def test_bright_frame_fails_average_gate():
    assert not cmr.check_obscuration(np.full((100, 100), 120, np.uint8), 10.0, 0.05)[0]


def test_dark_average_but_no_black_pixels_fails_density_gate():
    ok, avg, black = cmr.check_obscuration(np.full((100, 100), 8, np.uint8), 10.0, 0.05)
    assert avg <= 10.0 and black == 0.0 and not ok


# --- thermal ----------------------------------------------------------------
@pytest.mark.parametrize("temp,expected", [
    (45.0, "OK"), (60.0, "OK"), (60.1, "WARN"), (70.0, "WARN"), (70.1, "HALT")])
def test_thermal_tiers(temp, expected):
    assert cmr.thermal_state(temp, 60.0, 70.0) == expected


# --- calibration ------------------------------------------------------------
def test_calibration_completes_exactly_once():
    gate = cmr.CalibrationGate(3)
    assert [gate.feed(), gate.feed(), gate.feed(), gate.feed()] == [False, False, True, False]
    assert gate.done and gate.count == 3


# --- trace CSV --------------------------------------------------------------
EST = timezone(timedelta(hours=-5))
T0 = 1_800_000_000.0   # 2027-01-15 08:00:00 UTC = 03:00:00 EST (a realistic timestamp)


def test_trace_logger_writes_header_rows_and_local_time(tmp_path):
    path = tmp_path / "trace.csv"
    t = cmr.TraceLogger(str(path), tz=EST)
    t.log(cmr.ACCEPTED, 200, 10, 5, 6)
    t.log(cmr.REJECTED_SPATIAL, 90, 0, 7, 8, "note")
    for h in list(t._logger.handlers):
        h.flush()
        t._logger.removeHandler(h)
        h.close()
    lines = path.read_text().splitlines()
    assert lines[0] == cmr.TraceLogger.HEADER and lines[0].endswith(",note,local_time")
    f1, f2 = lines[1].split(","), lines[2].split(",")
    assert f1[1:7] == ["ACCEPTED", "200", "10", "20.00", "5", "6"]
    assert f2[4] == "inf" and f2[7] == "note"
    # UTC column is naive; local column carries the -05:00 offset and is 5 h behind it
    utc = datetime.fromisoformat(f1[0]).replace(tzinfo=timezone.utc)
    loc = datetime.fromisoformat(f1[8])
    assert f1[8].endswith("-05:00") and abs(loc - utc) < timedelta(milliseconds=1)   # same instant (ms rounding)


def test_trace_logger_retires_old_schema_file(tmp_path):
    path = tmp_path / "trace.csv"
    path.write_text("timestamp_iso,status,max_val,bg_val,ratio,loc_x,loc_y,note\n2026-07-03T07:58:18,ACCEPTED,71,1,71.00,1,2,\n")
    t = cmr.TraceLogger(str(path), tz=EST)
    t.log(cmr.ACCEPTED, 100, 5, 1, 1)
    for h in list(t._logger.handlers):
        h.flush(); t._logger.removeHandler(h); h.close()
    legacy = [f for f in os.listdir(tmp_path) if ".legacy-" in f]
    assert len(legacy) == 1 and "2026-07-03" in (tmp_path / legacy[0]).read_text()   # old data kept intact
    new = path.read_text().splitlines()
    assert new[0] == cmr.TraceLogger.HEADER and len(new) == 2                       # new file: header + 1 row


# --- time helpers -------------------------------------------------------------
def test_utc_iso_is_naive_utc_and_local_iso_has_offset():
    assert cmr.utc_iso(T0) == "2027-01-15T08:00:00"
    assert cmr.local_iso(T0, EST) == "2027-01-15T03:00:00.000-05:00"


def test_local_iso_follows_daylight_saving():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    assert cmr.local_iso(1_800_000_000.0, ny).endswith("-05:00")       # January: EST
    assert cmr.local_iso(1_815_000_000.0, ny).endswith("-04:00")       # July: EDT


def test_detect_timezone_prefers_env(monkeypatch):
    monkeypatch.setenv("STATION_TZ", "America/New_York")
    assert cmr.detect_timezone_name() == "America/New_York"


def test_detect_timezone_never_raises_without_env(monkeypatch):
    monkeypatch.delenv("STATION_TZ", raising=False)
    assert isinstance(cmr.detect_timezone_name(), str)   # "" on systems with no zone info (e.g. Windows)


# --- archive / status -------------------------------------------------------
def make_store(tmp_path):
    os.makedirs(tmp_path / "public")
    return cmr.LocalStore(str(tmp_path / "public"))


def test_archive_is_append_only_outside_public_and_strips_image(tmp_path):
    store = make_store(tmp_path)
    assert os.path.dirname(store.archive_file) == str(tmp_path)
    store.append_archive({"timestamp": 1, "frame_content": "BIGIMAGE", "max": 99})
    store.append_archive({"timestamp": 2, "max": 100})
    rows = [json.loads(line) for line in open(store.archive_file)]
    assert rows == [{"timestamp": 1, "max": 99}, {"timestamp": 2, "max": 100}]


def test_rolling_cache_is_capped_but_archive_keeps_everything(tmp_path):
    store = make_store(tmp_path)
    store.MAX_DETECTIONS = 3
    for i in range(5):
        store.save_local_log({"detections": [{"timestamp": i}]})
    assert [d["timestamp"] for d in json.load(open(store.log_file))] == [2, 3, 4]
    assert len(open(store.archive_file).read().splitlines()) == 5
    assert not os.path.exists(store.log_file + ".tmp")


def test_status_includes_gate_state(tmp_path):
    store = make_store(tmp_path)
    store.status_extra.update(calibrating=True, calibration_progress=42, cpu_temp_c=51.0)
    cfg = cmr.AppConfig("s", "u", "p", "m", "v", 1.0, 2.0, 3.0, "k", PROFILE, str(tmp_path / "public"))
    env = cmr.EnvironmentState()
    env._last_check = 1e18  # skip the weather call
    store.update_status(True, cfg, env)
    st = json.load(open(store.status_file))
    assert st["calibrating"] is True and st["calibration_progress"] == 42
    assert st["cpu_temp_c"] == 51.0 and st["connected"] is True


# --- hot-pixel mask ---------------------------------------------------------
def test_hot_mask_learns_after_min_hits_and_masks_neighbours(tmp_path):
    m = cmr.HotPixelMask(str(tmp_path / "hp.json"), "1280x720", min_hits=3, radius=3)
    assert not m.is_hot(50, 50)
    assert [m.record(50, 50), m.record(50, 50), m.record(50, 50)] == [False, False, True]
    assert m.is_hot(50, 50) and m.is_hot(52, 48) and not m.is_hot(54, 50)


def test_hot_mask_persists_and_is_keyed_by_capture_size(tmp_path):
    path = str(tmp_path / "hp.json")
    big = cmr.HotPixelMask(path, "1280x720", min_hits=2)
    big.record(7, 9); big.record(7, 9)
    assert cmr.HotPixelMask(path, "1280x720", min_hits=2).is_hot(7, 9)      # survives restart
    assert not cmr.HotPixelMask(path, "640x480", min_hits=2).is_hot(7, 9)   # other size unaffected
    small = cmr.HotPixelMask(path, "640x480", min_hits=2)
    small.record(1, 1)
    assert cmr.HotPixelMask(path, "1280x720", min_hits=2).is_hot(7, 9)      # saving 640 keeps 1280


def test_hot_mask_disabled_when_min_hits_zero(tmp_path):
    m = cmr.HotPixelMask(str(tmp_path / "hp.json"), "1280x720", min_hits=0)
    for _ in range(10):
        assert m.record(5, 5) is False
    assert not m.is_hot(5, 5)


def test_hot_mask_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "hp.json"
    p.write_text("{not json")
    assert not cmr.HotPixelMask(str(p), "1280x720").is_hot(1, 1)


def test_scan_rejects_hot_position_but_still_finds_real_track(tmp_path):
    img, bg = blank()
    blob(img, 60, 60, 120)      # known sensor defect (clustered, so it passes the 3x3 test)
    blob(img, 220, 140, 110)    # genuine track
    m = cmr.HotPixelMask(str(tmp_path / "hp.json"), "300x200", min_hits=1)
    m.record(60, 60)
    ev = cmr.scan_frame(img, bg, PROFILE, hot_mask=m)
    assert [(e[0], e[2], e[3]) for e in ev] == [(cmr.REJECTED_HOT, 60, 60), (cmr.ACCEPTED, 220, 140)]


def test_scan_without_mask_still_accepts_that_position():
    img, bg = blank()
    blob(img, 60, 60, 120)
    assert [e[0] for e in cmr.scan_frame(img, bg, PROFILE)] == [cmr.ACCEPTED]


# --- config -----------------------------------------------------------------
def test_config_reads_capture_size_and_hot_pixel_settings(monkeypatch, tmp_path):
    for k, v in dict(CREDO_STATION="s", CREDO_USER="u", CREDO_PASS="p", CREDO_DEVICE_MODEL="m",
                     CREDO_SOFTWARE_VERSION="v", CREDO_LAT="1", CREDO_LNG="2", CREDO_ALT="3",
                     CAPTURE_WIDTH="640", CAPTURE_HEIGHT="480", HOT_PIXEL_MIN_HITS="8").items():
        monkeypatch.setenv(k, v)
    cfg = cmr.AppConfig.from_env(str(tmp_path))
    assert (cfg.capture_width, cfg.capture_height, cfg.hot_pixel_min_hits, cfg.hot_pixel_radius) == (640, 480, 8, 3)


def test_config_capture_defaults_to_640x480(monkeypatch, tmp_path):
    for k, v in dict(CREDO_STATION="s", CREDO_USER="u", CREDO_PASS="p", CREDO_DEVICE_MODEL="m",
                     CREDO_SOFTWARE_VERSION="v", CREDO_LAT="1", CREDO_LNG="2", CREDO_ALT="3").items():
        monkeypatch.setenv(k, v)
    for k in ("CAPTURE_WIDTH", "CAPTURE_HEIGHT", "HOT_PIXEL_MIN_HITS"):
        monkeypatch.delenv(k, raising=False)
    cfg = cmr.AppConfig.from_env(str(tmp_path))
    assert (cfg.capture_width, cfg.capture_height, cfg.hot_pixel_min_hits) == (640, 480, 5)


# --- live-time stats --------------------------------------------------------
def make_stats(tmp_path, window_s=100, read_controls=lambda: ("666", "255")):
    return cmr.LiveStats(str(tmp_path / "live.csv"), window_s, 640, 480, 1000, 255, "v-test", tz=EST,
                         read_controls=read_controls)


def read_rows(tmp_path):
    return (tmp_path / "live.csv").read_text().splitlines()


def test_live_stats_writes_header_and_one_row_per_window(tmp_path):
    s = make_stats(tmp_path)
    s.start(T0)
    for _ in range(40):
        s.frame()
    s.event(cmr.ACCEPTED); s.event(cmr.ACCEPTED); s.event(cmr.REJECTED_HOT); s.event(cmr.REJECTED_SPATIAL)
    s.maybe_flush(T0 + 50)                 # window not over yet -> nothing written
    assert not (tmp_path / "live.csv").exists()
    s.maybe_flush(T0 + 100)
    lines = read_rows(tmp_path)
    assert lines[0] == cmr.LiveStats.HEADER
    f = lines[1].split(",")
    assert f[0] == "2027-01-15T08:00:00" and f[1] == "2027-01-15T03:00:00.000-05:00"
    assert f[2:] == ["100", "40", "2", "1", "1", "0", "640", "480", "1000", "255", "666", "255", "v-test"]   # requested 1000, camera reports 666


def test_live_stats_counters_reset_between_windows(tmp_path):
    s = make_stats(tmp_path)
    s.start(T0)
    s.frame(); s.event(cmr.ACCEPTED)
    s.flush(T0 + 100)
    s.frame(); s.frame()
    s.flush(T0 + 250)
    rows = [r.split(",") for r in read_rows(tmp_path)[1:]]
    assert [(r[2], r[3], r[4]) for r in rows] == [("100", "1", "1"), ("150", "2", "0")]  # elapsed, frames, accepted


def test_live_stats_partial_window_flushes_with_real_elapsed_time(tmp_path):
    s = make_stats(tmp_path, window_s=3600)
    s.start(T0)
    s.frame()
    s.flush(T0 + 42)                       # shutdown/halt mid-window
    assert read_rows(tmp_path)[1].split(",")[2] == "42"


def test_live_stats_inactive_before_calibration_ends(tmp_path):
    s = make_stats(tmp_path)
    s.flush(T0 + 500)                      # start() never called (still calibrating)
    s.maybe_flush(T0 + 500)
    assert not (tmp_path / "live.csv").exists()


def test_live_stats_ignores_sub_second_flush(tmp_path):
    s = make_stats(tmp_path)
    s.start(T0)
    s.flush(T0 + 0.4)
    assert not (tmp_path / "live.csv").exists()


def test_live_stats_retires_old_schema_file(tmp_path):
    (tmp_path / "live.csv").write_text("window_start_utc,window_s,frames\n2026-11-15T21:06:00,3600,21000\n")
    s = make_stats(tmp_path)
    s.start(T0); s.frame(); s.flush(T0 + 10)
    assert any(".legacy-" in f for f in os.listdir(tmp_path))
    assert read_rows(tmp_path)[0] == cmr.LiveStats.HEADER


def test_stats_window_config(monkeypatch, tmp_path):
    for k, v in dict(CREDO_STATION="s", CREDO_USER="u", CREDO_PASS="p", CREDO_DEVICE_MODEL="m",
                     CREDO_SOFTWARE_VERSION="v", CREDO_LAT="1", CREDO_LNG="2", CREDO_ALT="3",
                     STATS_WINDOW_S="900").items():
        monkeypatch.setenv(k, v)
    assert cmr.AppConfig.from_env(str(tmp_path)).stats_window_s == 900


def test_update_status_never_calls_the_weather_api_and_leaves_no_tmp(tmp_path):
    store = make_store(tmp_path)
    cfg = cmr.AppConfig("s", "u", "p", "m", "v", 1.0, 2.0, 3.0, "k", PROFILE, str(tmp_path / "public"))
    env = cmr.EnvironmentState()
    env.pressure_hpa = 1012.5
    env.get_pressure = lambda *a: (_ for _ in ()).throw(AssertionError("must not hit the network"))
    store.update_status(True, cfg, env)
    assert json.load(open(store.status_file))["pressure_hpa"] == 1012.5
    assert not os.path.exists(store.status_file + ".tmp")


def test_status_publishes_timezone(tmp_path):
    store = make_store(tmp_path)
    cfg = cmr.AppConfig("s", "u", "p", "m", "v", 1.0, 2.0, 3.0, "k", PROFILE, str(tmp_path / "public"),
                        station_tz="America/New_York")
    env = cmr.EnvironmentState(); env.pressure_hpa = 1000.0
    store.update_status(True, cfg, env)
    assert json.load(open(store.status_file))["timezone"] == "America/New_York"
    store.update_status(True, cmr.AppConfig("s", "u", "p", "m", "v", 1.0, 2.0, 3.0, "k", PROFILE, "x"), env)
    assert json.load(open(store.status_file))["timezone"] is None      # unknown zone -> null, not ""


def test_config_station_tz_from_env(monkeypatch, tmp_path):
    for k, v in dict(CREDO_STATION="s", CREDO_USER="u", CREDO_PASS="p", CREDO_DEVICE_MODEL="m",
                     CREDO_SOFTWARE_VERSION="v", CREDO_LAT="1", CREDO_LNG="2", CREDO_ALT="3",
                     STATION_TZ="America/New_York").items():
        monkeypatch.setenv(k, v)
    assert cmr.AppConfig.from_env(str(tmp_path)).station_tz == "America/New_York"


def test_live_stats_row_survives_failing_camera_read(tmp_path):
    def boom():
        raise OSError("v4l2-ctl missing")
    s = make_stats(tmp_path, read_controls=boom)
    s.start(T0); s.frame(); s.flush(T0 + 10)
    f = read_rows(tmp_path)[1].split(",")
    assert f[10:14] == ["1000", "255", "", ""] and f[-1] == "v-test"


def test_read_camera_controls_parses_v4l2_output(monkeypatch):
    class R: stdout = "exposure_time_absolute: 666\ngain: 255\n"
    monkeypatch.setattr(cmr.subprocess, "run", lambda *a, **k: R())
    assert cmr.read_camera_controls({"driver_quirk": "legacy"}) == ("666", "255")


def test_read_camera_controls_unreadable(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no v4l2-ctl")
    monkeypatch.setattr(cmr.subprocess, "run", boom)
    assert cmr.read_camera_controls({"driver_quirk": "legacy"}) == ("", "")

import threading

import pytest


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


@pytest.fixture
def cmr(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    import CMOSmicRay as cmr

    monkeypatch.setattr(cmr, "config", cmr.AppConfig(
        station_id="test-station", credo_user="test-user", credo_pass="test-pass",
        device_model="test-device", software_version="v0-test",
        lat=1.0, lng=2.0, alt=3.0,
        camera_profile_key="Generic Camera", camera_profile=cmr.HARDWARE_PROFILES["GENERIC"],
        public_dir=str(tmp_path),
    ))
    # login()/store.update_status() write to store.status_file -- isolate it,
    # and stub the weather fetch so tests don't make a real network call.
    monkeypatch.setattr(cmr.store, "status_file", str(tmp_path / "status.json"))
    monkeypatch.setattr(cmr.environment, "get_pressure", lambda lat, lng: 1000.0)

    # Reset shared auth state so tests don't leak into each other (the
    # module is only imported once per test session).
    cmr.auth.clear_token()
    yield cmr
    cmr.auth.clear_token()


def test_login_success_sets_token(cmr, monkeypatch):
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: FakeResponse(200, {"token": "abc123"}))
    assert cmr.login() is True
    assert cmr.auth.get_token() == "abc123"


def test_login_failure_leaves_token_none(cmr, monkeypatch):
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: FakeResponse(403))
    assert cmr.login() is False
    assert cmr.auth.get_token() is None


def test_login_short_circuits_when_token_cached(cmr, monkeypatch):
    cmr.auth.set_token("already-have-one")
    calls = []
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: calls.append(1) or FakeResponse(200, {"token": "new"}))
    assert cmr.login() is True
    assert calls == []  # never hit the network -- cached token wins
    assert cmr.auth.get_token() == "already-have-one"


def test_login_is_called_once_under_concurrent_callers(cmr, monkeypatch):
    # Regression test for the auth lock: N threads calling login()
    # concurrently while no token is set should result in exactly
    # one POST, not N.
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResponse(200, {"token": "shared-token"})

    monkeypatch.setattr(cmr.requests, "post", fake_post)

    threads = [threading.Thread(target=cmr.login) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert cmr.auth.get_token() == "shared-token"


def test_upload_worker_clears_token_on_401(cmr, monkeypatch):
    # Regression test: upload_worker must clear the token on 401, the
    # same way ping() already does, so the next call re-logs-in instead of
    # silently 401ing on every subsequent detection until ping's 10-minute
    # cycle notices.
    cmr.auth.set_token("stale-token")
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: FakeResponse(401, text="unauthorized"))

    cmr.upload_worker({"detections": [{"id": 1}]})

    assert cmr.auth.get_token() is None


def test_upload_worker_success_keeps_token(cmr, monkeypatch):
    cmr.auth.set_token("good-token")
    monkeypatch.setattr(cmr.requests, "post", lambda *a, **k: FakeResponse(200, text="ok"))

    cmr.upload_worker({"detections": [{"id": 1}]})

    assert cmr.auth.get_token() == "good-token"


def test_upload_worker_logs_in_when_no_token(cmr, monkeypatch):
    login_calls = []

    def fake_post(url, *a, **k):
        if url == cmr.LOGIN_URL:
            login_calls.append(1)
            return FakeResponse(200, {"token": "fresh-token"})
        return FakeResponse(200, text="ok")

    monkeypatch.setattr(cmr.requests, "post", fake_post)

    cmr.upload_worker({"detections": [{"id": 1}]})

    assert login_calls == [1]
    assert cmr.auth.get_token() == "fresh-token"

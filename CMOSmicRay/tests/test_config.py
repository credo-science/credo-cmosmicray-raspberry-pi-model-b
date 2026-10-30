import subprocess
import sys
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "CMOSmicRay.py")

VALID_ENV = {
    "CREDO_STATION": "test-station",
    "CREDO_USER": "test-user",
    "CREDO_PASS": "test-pass",
    "CREDO_DEVICE_MODEL": "RPi Test",
    "CREDO_SOFTWARE_VERSION": "v0.0-test",
    "CREDO_LAT": "1.0",
    "CREDO_LNG": "2.0",
    "CREDO_ALT": "3.0",
}


def run_with_env(env_overrides):
    # AppConfig.from_env() runs under __main__, so we exercise it via
    # subprocess rather than import -- importing CMOSmicRay.py doesn't
    # need real env vars (see test_import below), but *running* it does.
    # Start from a copy of the real environment (not a hand-picked subset) so
    # subprocess creation behaves consistently across platforms, but strip any
    # CREDO_* vars already exported in the dev's shell so tests stay hermetic,
    # then layer this test case's overrides on top.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CREDO_")}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_import_does_not_require_env(monkeypatch):
    # Regression test for the import-time sys.exit(1) bug: importing the
    # module for testing must not require a real .env / camera stack.
    monkeypatch.syspath_prepend(REPO_ROOT)
    import CMOSmicRay  # noqa: F401 -- import succeeding is the assertion


def test_missing_env_exits_1():
    result = run_with_env({})
    assert result.returncode == 1
    assert "Missing required environment variable" in result.stdout


def test_partial_env_exits_1():
    partial = dict(VALID_ENV)
    del partial["CREDO_PASS"]
    result = run_with_env(partial)
    assert result.returncode == 1
    assert "CREDO_PASS" in result.stdout


def test_non_numeric_lat_exits_1():
    bad = dict(VALID_ENV)
    bad["CREDO_LAT"] = "not-a-number"
    result = run_with_env(bad)
    assert result.returncode == 1
    assert "must be numeric" in result.stdout

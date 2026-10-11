"""Native Windows probe; public OpenRouter data only, in a disposable Hermes home."""

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def load_state():
    name = "windows_smoke_plugin"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(name + ".state")


def lock_probe():
    assert sys.platform == "win32", "This probe must run on native Windows"
    state = load_state()
    child = [sys.executable, str(Path(__file__).resolve()), "child-lock"]
    with state.sync_lock() as acquired:
        assert acquired
        result = subprocess.run(child, capture_output=True, text=True, timeout=20, check=True)
        assert result.stdout.strip() == "False", result.stdout + result.stderr
    result = subprocess.run(child, capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == "True", result.stdout + result.stderr
    try:
        with state.sync_lock() as acquired:
            assert acquired
            raise ValueError("probe cleanup")
    except ValueError:
        pass
    with state.sync_lock() as acquired:
        assert acquired
    print("PASS: real msvcrt contention, reacquisition, and exception cleanup")


def integration_probe():
    assert sys.platform == "win32"
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    plugin = "hermes-openrouter-free-rotator"

    def hermes(*args, timeout=240):
        result = subprocess.run(
            ["hermes", "--profile", "default", *args], cwd=home,
            capture_output=True, text=True, timeout=timeout,
        )
        print(result.stdout)
        print(result.stderr)
        assert result.returncode == 0, (args, result.returncode)
        return result.stdout

    hermes("plugins", "install", "GoSlowPoke168/" + plugin,
           "--ref", os.environ["GITHUB_SHA"], "--yes-deps", "--enable")
    hermes("plugins", "enable", plugin)
    hermes("freemodels", "--help")
    hermes("freemodels", "list", "--json")
    hermes("freemodels", "sync", "--json")
    state_path = home / "freemodels" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_sync"] and state["managed_default"], state
    hermes("freemodels", "status", "--json")
    hermes("freemodels", "pin")
    hermes("freemodels", "unpin")
    hermes("freemodels", "install-cron", "--apply")
    hermes("freemodels", "install-cron", "--apply")
    jobs_path = home / "cron" / "jobs.json"
    data = json.loads(jobs_path.read_text(encoding="utf-8-sig"))
    jobs = data["jobs"] if isinstance(data, dict) else data
    owned = [job for job in jobs if job.get("name") == "freemodels-sync"]
    assert len(owned) == 1, jobs
    job_id = owned[0]["id"]
    # A successful sync makes natural minute checks quiet, avoiding repeated public requests.
    state["last_sync"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()
    with (home / "gateway-smoke.log").open("w", encoding="utf-8") as log:
        gateway = subprocess.Popen(
            ["hermes", "--profile", "default", "gateway", "run"],
            cwd=home, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                assert gateway.poll() is None, "Gateway exited; see gateway-smoke.log"
                data = json.loads(jobs_path.read_text(encoding="utf-8-sig"))
                jobs = data["jobs"] if isinstance(data, dict) else data
                job = next(job for job in jobs if job["id"] == job_id)
                if job.get("last_run_at"):
                    assert job.get("last_status") == "ok", job
                    break
                time.sleep(2)
            else:
                raise AssertionError("No natural gateway execution within 180 seconds")
            assert state_path.read_bytes() == before, "Satisfied UTC check rewrote state"
            hermes("cron", "status")
            hermes("cron", "runs", job_id)
        finally:
            gateway.terminate()
            try:
                gateway.wait(timeout=20)
            except subprocess.TimeoutExpired:
                gateway.kill()
                gateway.wait(timeout=10)
    hermes("cron", "remove", job_id)
    hermes("plugins", "uninstall", plugin)
    (home / "windows-result.json").write_text(json.dumps({
        "passed": True, "platform": sys.platform, "python": sys.version,
        "plugin_sha": os.environ["GITHUB_SHA"], "hermes_version": "0.21.6",
        "native_lock": True, "live_sync": True, "idempotent_install": True,
        "natural_gateway_run": True, "quiet_check": True,
    }, indent=2), encoding="utf-8")
    print("PASS: Windows installation, live commands, and natural gateway scheduling")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "child-lock":
        with load_state().sync_lock() as acquired:
            print(acquired)
    elif mode == "lock":
        lock_probe()
    elif mode == "integration":
        integration_probe()
    else:
        raise ValueError(mode)

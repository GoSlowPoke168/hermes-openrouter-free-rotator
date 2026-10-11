"""Install Hermes scheduling or an opt-in system crontab entry."""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
from pathlib import Path

from .state import hermes_home, state_dir
from .scheduled_sync import parse_utc_time, profile_arguments, scheduled_sync_due

CRON_MARKER = "freemodels sync"
JOB_NAME = "freemodels-sync"
SCRIPT_NAME = "freemodels_sync.py"


def hermes_bin() -> str:
    found = shutil.which("hermes")
    if found:
        return found
    fallback = Path.home() / ".local/bin/hermes"
    return str(fallback) if fallback.exists() else "hermes"


def build_cron_line(time_str: str = "00:01") -> str:
    """*time_str* is UTC (OpenRouter's free-tier quota resets at 00:00 UTC).

    cron(8) has no per-job timezone support — it fires on the daemon's
    single system-wide local timezone — so the UTC time is converted to its
    local-time equivalent here, at install time. Note this means the
    installed crontab entry can drift by an hour across DST transitions;
    rerun `install-cron --apply` after a DST change to re-pin it.
    """
    hour, minute = parse_utc_time(time_str)
    utc_target = _dt.datetime.now(_dt.timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    local_target = utc_target.astimezone()
    hour, minute = local_target.hour, local_target.minute
    sd = state_dir()

    return (
        f"{minute} {hour} * * * {hermes_bin()} freemodels sync "
        f">> {sd}/cron.log 2>&1"
    )


def build_sync_script(time_str: str = "00:01") -> str:
    parse_utc_time(time_str)
    source = Path(__file__).with_name("scheduled_sync.py").read_text(encoding="utf-8")
    return source.replace('SYNC_TIME = "00:01"', f"SYNC_TIME = {time_str!r}")


def _hermes_jobs() -> list[dict]:
    path = hermes_home() / "cron" / "jobs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError(f"cannot read Hermes jobs in {path}; expected a list of jobs")
    return jobs


def _uses_sync_script(job: dict) -> bool:
    if not isinstance(job.get("script"), str):
        return False
    scripts = hermes_home() / "scripts"
    path = Path(job["script"]).expanduser()
    if not path.is_absolute():
        path = scripts / path
    return path.resolve() == (scripts / SCRIPT_NAME).resolve()


def _is_own_hermes_job(job: dict) -> bool:
    return job.get("name") == JOB_NAME and _uses_sync_script(job)


def hermes_job_missing() -> bool:
    if not (hermes_home() / "scripts" / SCRIPT_NAME).is_file():
        return True
    return not any(
        _is_own_hermes_job(job) and job.get("enabled", True)
        and job.get("state", "scheduled") not in {"paused", "completed", "blocked", "error"}
        and job.get("no_agent") is True
        for job in _hermes_jobs()
    )


def schedule_missing() -> bool:
    return hermes_job_missing() and cron_line_missing()


def _hermes_cron(*args: str) -> str:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home())
    result = subprocess.run(
        [hermes_bin(), *profile_arguments(hermes_home()), "cron", *args],
        env=env, capture_output=True, text=True,
        check=True, timeout=60,
    )
    return result.stdout.strip()


def _remove_system_entry() -> bool:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return False
    if result.returncode != 0:
        if result.returncode == 1 and "no crontab" in (result.stderr or "").lower():
            return False
        raise RuntimeError(f"could not read crontab: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    remaining = [line for line in lines if line.lstrip().startswith("#") or not is_own_cron_line(line)]
    if remaining == lines:
        return False
    subprocess.run(["crontab", "-"], input="\n".join(remaining) + ("\n" if remaining else ""),
                   text=True, capture_output=True, check=True, timeout=10)
    return True


def install_hermes_job(time_str: str = "00:01") -> str:
    source = build_sync_script(time_str)
    jobs = _hermes_jobs()
    named = [job for job in jobs if job.get("name") == JOB_NAME]
    if len(named) > 1 or (named and not _is_own_hermes_job(named[0])):
        raise ValueError(f"conflict: {JOB_NAME!r} names duplicate or unrelated jobs; resolve them with hermes cron")
    script = hermes_home() / "scripts" / SCRIPT_NAME
    old_source = script.read_text(encoding="utf-8") if script.exists() else None
    if old_source is not None and not old_source.startswith('"""Portable Hermes cron script and UTC deadline check."""'):
        raise ValueError(f"conflict: refusing to replace unrelated script {script}")
    action = "edit" if named else "create"
    help_text = _hermes_cron(action, "--help")
    if not all(flag in help_text for flag in ("--script", "--no-agent", "--deliver")):
        raise RuntimeError("Hermes lacks script-only cron support; update Hermes or use install-cron --system")
    script.parent.mkdir(parents=True, exist_ok=True)
    tmp = script.with_suffix(".py.tmp")
    tmp.write_text(source, encoding="utf-8")
    tmp.replace(script)
    options = ("--script", SCRIPT_NAME, "--no-agent", "--deliver", "local")
    # Minute boundaries prevent interval jobs slipping past Hermes's 60-second ticker.
    try:
        if named:
            job = named[0]
            _hermes_cron("edit", job["id"], "--schedule", "* * * * *", *options)
            if not job.get("enabled", True) or job.get("state", "scheduled") != "scheduled":
                _hermes_cron("resume", job["id"])
        else:
            _hermes_cron("create", "* * * * *", "--name", JOB_NAME, *options)
        installed = [job for job in _hermes_jobs() if _is_own_hermes_job(job)]
        if len(installed) != 1 or hermes_job_missing():
            raise RuntimeError("Hermes job installation could not be verified; existing crontab was preserved")
    except Exception:
        if old_source is None:
            script.unlink()
        else:
            tmp.write_text(old_source, encoding="utf-8")
            tmp.replace(script)
        raise
    message = f"{'updated' if named else 'installed'} Hermes job {JOB_NAME!r}: daily sync at {time_str} UTC (minute checks)\n  script: {script}"
    try:
        if _remove_system_entry():
            message += "\nremoved this home's legacy crontab entry"
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        message += f"\nwarning: could not remove the old crontab entry; remove it manually to avoid duplicate syncs: {exc}"
    try:
        message += "\n" + _hermes_cron("status")
    except (OSError, subprocess.SubprocessError) as exc:
        message += f"\nwarning: could not check scheduler health: {exc}"
    message += "\nAutomatic runs require a live gateway: hermes gateway install, or hermes gateway run."
    return message


def remove_hermes_job() -> str:
    jobs = _hermes_jobs()
    if any(not _is_own_hermes_job(job) and _uses_sync_script(job) for job in jobs):
        raise ValueError("unrelated job uses the sync script; jobs and script preserved")
    owned = [job for job in jobs if _is_own_hermes_job(job)]
    for job in owned:
        _hermes_cron("remove", job["id"])
    script = hermes_home() / "scripts" / SCRIPT_NAME
    if script.exists():
        if not script.read_text(encoding="utf-8").startswith('"""Portable Hermes cron script and UTC deadline check."""'):
            raise ValueError(f"unrelated script at {script}; preserved")
        script.unlink()
    return "removed owned Hermes schedule and script"


def find_stale_cron_line() -> str | None:
    """Return the installed `freemodels sync` crontab line if it is the broken
    flock-wrapped form, else None.

    Releases up to 1.0.0 generated a line wrapping the command in
    `flock -n <state_dir>/sync.lock`. cmd_sync() takes an fcntl.flock on that
    same file, and flock(1) holds its own lock across the exec — so every
    scheduled run exited with "another sync is already running" and the
    default model silently stopped rotating.

    Upgrading the plugin does not rewrite an already-installed crontab, so
    callers surface this and point at `install-cron --apply`.
    """
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None            # no crontab binary, or it hung — not our problem
    if proc.returncode != 0:
        return None            # no crontab installed for this user
    lock = f"{state_dir()}/sync.lock"
    for line in proc.stdout.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if CRON_MARKER in line and "flock" in line and lock in line:
            return line.strip()
    return None


def is_own_cron_line(line: str) -> bool:
    """True if *line* is the sync entry for this HERMES_HOME (crontab is per-user, not per-home)."""
    return CRON_MARKER in line and f"{state_dir()}/cron.log" in line


def cron_line_missing() -> bool:
    """True when the user crontab has no active entry for this state dir; False if unreadable."""
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return True            # "no crontab for <user>"
    return not any(
        is_own_cron_line(l) and not l.lstrip().startswith("#")
        for l in proc.stdout.splitlines()
    )


def install_cron_line(line: str) -> str:
    """Idempotently install *line* in the user crontab.

    A pre-existing entry for this state dir is replaced in place (same
    position, rest of the crontab untouched) rather than left stale — so
    rerunning `install-cron --apply` after a DST change re-pins the
    UTC-derived time in build_cron_line() without manual editing.
    """
    proc = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    if proc.returncode != 0 and "no crontab" not in getattr(proc, "stderr", "").lower():
        raise RuntimeError(f"could not read crontab: {getattr(proc, 'stderr', '').strip()}")
    current = proc.stdout if proc.returncode == 0 else ""
    lines = current.splitlines()
    existing_idx = next((i for i, l in enumerate(lines) if is_own_cron_line(l)), None)
    if existing_idx is not None:
        if lines[existing_idx] == line:
            return "crontab already contains this exact 'freemodels sync' entry — nothing to do."
        old_line = lines[existing_idx]
        lines[existing_idx] = line
        new_tab = "\n".join(lines) + "\n"
        subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)
        return "replaced existing crontab entry:\n" f"  old: {old_line}\n" f"  new: {line}"
    new_tab = current.rstrip("\n")
    new_tab = f"{new_tab}\n{line}\n" if new_tab else f"{line}\n"
    subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)
    return "installed crontab entry:\n  " + line

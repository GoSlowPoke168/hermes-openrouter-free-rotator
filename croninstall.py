"""Generate / install the daily crontab entry for `hermes freemodels sync`."""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import subprocess
from pathlib import Path

from .state import state_dir

CRON_MARKER = "freemodels sync"


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
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", time_str)
    if not m:
        raise ValueError(f"--time must be HH:MM (UTC), got {time_str!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"--time out of range: {time_str!r}")
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


def install_cron_line(line: str) -> str:
    """Idempotently install *line* in the user crontab.

    A pre-existing line matching CRON_MARKER is replaced in place (same
    position, rest of the crontab untouched) rather than left stale — so
    rerunning `install-cron --apply` after a DST change re-pins the
    UTC-derived time in build_cron_line() without manual editing.
    """
    proc = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    current = proc.stdout if proc.returncode == 0 else ""
    lines = current.splitlines()
    existing_idx = next((i for i, l in enumerate(lines) if CRON_MARKER in l), None)
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

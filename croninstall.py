"""Generate / install the daily crontab entry for `hermes freemodels sync`."""

from __future__ import annotations

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


def build_cron_line(time_str: str = "00:30") -> str:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", time_str)
    if not m:
        raise ValueError(f"--time must be HH:MM, got {time_str!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"--time out of range: {time_str!r}")
    sd = state_dir()
    flock = shutil.which("flock")
    prefix = f"{flock} -n {sd}/sync.lock " if flock else ""
    return (
        f"{minute} {hour} * * * {prefix}{hermes_bin()} freemodels sync "
        f">> {sd}/cron.log 2>&1"
    )


def install_cron_line(line: str) -> str:
    """Idempotently add *line* to the user crontab. Returns a status message."""
    proc = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    current = proc.stdout if proc.returncode == 0 else ""
    if CRON_MARKER in current:
        return "crontab already contains a 'freemodels sync' entry — nothing to do."
    new_tab = current.rstrip("\n")
    new_tab = f"{new_tab}\n{line}\n" if new_tab else f"{line}\n"
    subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)
    return "installed crontab entry:\n  " + line

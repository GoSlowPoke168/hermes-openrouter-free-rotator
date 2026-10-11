"""Portable Hermes cron script and UTC deadline check."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SYNC_TIME = "00:01"


def parse_utc_time(time_str: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", time_str)
    if not match:
        raise ValueError(f"--time must be HH:MM (UTC), got {time_str!r}")
    hour, minute = map(int, match.groups())
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"--time out of range: {time_str!r}")
    return hour, minute


def profile_arguments(home: Path) -> list[str]:
    return ["--profile", home.name if home.parent.name == "profiles" else "default"]


def scheduled_sync_due(
    last_sync: str | None, time_str: str, now: _dt.datetime | None = None,
) -> bool:
    hour, minute = parse_utc_time(time_str)
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline > now:
        deadline -= _dt.timedelta(days=1)
    try:
        last = _dt.datetime.fromisoformat(last_sync)
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None or last > now:
        return True
    return last < deadline


def main() -> int:
    home = Path(__file__).resolve().parents[1]
    sd = home / "freemodels"
    try:
        state = json.loads((sd / "state.json").read_text(encoding="utf-8"))
        last_sync = state.get("last_sync") if isinstance(state, dict) else None
    except (OSError, ValueError):
        last_sync = None
    if not scheduled_sync_due(last_sync, SYNC_TIME):
        return 0
    sd.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    # Preserve editable-install paths expanded by Hermes's Windows venv bootstrap.
    env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
    with (sd / "cron.log").open("a", encoding="utf-8") as output:
        try:
            # Import the console entry point once; -m can reimport it and reselect a profile.
            result = subprocess.run(
                [sys.executable, "-c", "from hermes_cli.main import main; main()",
                 *profile_arguments(home), "freemodels", "sync",
                 "--scheduled-time", SYNC_TIME],
                env=env, stdout=output, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            print(f"could not launch freemodels sync: {exc}", file=output)
            return 1
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

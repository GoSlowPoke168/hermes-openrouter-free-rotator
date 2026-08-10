"""Crontab line generation and idempotent installation."""

import datetime as dt
import types

import pytest

from hermes_openrouter_free_rotator import croninstall


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(croninstall, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(croninstall, "hermes_bin", lambda: "/usr/bin/hermes")


# ---- build_cron_line ------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "9", "9:5", "99:99", "24:00", "12:60",
                                 "aa:bb", "12:00:00", " 12:00"])
def test_bad_times_are_rejected(bad):
    with pytest.raises(ValueError):
        croninstall.build_cron_line(bad)


@pytest.mark.parametrize("good", ["00:01", "0:01", "23:59", "12:30"])
def test_good_times_are_accepted(good):
    assert croninstall.build_cron_line(good)


def test_line_has_five_cron_fields_then_the_command():
    line = croninstall.build_cron_line("00:01")
    minute, hour, dom, mon, dow = line.split()[:5]
    assert dom == mon == dow == "*"
    assert 0 <= int(minute) <= 59 and 0 <= int(hour) <= 23
    assert "freemodels sync" in line


def test_line_redirects_output_to_the_state_dir(tmp_path):
    assert f">> {tmp_path}/cron.log 2>&1" in croninstall.build_cron_line("00:01")


def test_line_locks_against_a_concurrent_run(monkeypatch, tmp_path):
    monkeypatch.setattr(croninstall.shutil, "which", lambda n: f"/usr/bin/{n}")
    line = croninstall.build_cron_line("00:01")
    assert f"/usr/bin/flock -n {tmp_path}/sync.lock" in line


def test_line_still_works_without_flock(monkeypatch):
    monkeypatch.setattr(croninstall.shutil, "which", lambda n: None)
    assert "flock" not in croninstall.build_cron_line("00:01")


def test_utc_is_converted_to_local_time(monkeypatch):
    # The stated contract: --time is UTC, cron fires in local time.
    utc_now = dt.datetime.now(dt.timezone.utc)
    line = croninstall.build_cron_line("00:01")
    minute, hour = int(line.split()[0]), int(line.split()[1])
    expected = utc_now.replace(hour=0, minute=1, second=0, microsecond=0).astimezone()
    assert (hour, minute) == (expected.hour, expected.minute)


# ---- install_cron_line ----------------------------------------------------

@pytest.fixture
def crontab(monkeypatch):
    """Fake the `crontab` binary, recording what gets written."""
    box = {"current": "", "written": None}

    def run(cmd, **kw):
        if cmd == ["crontab", "-l"]:
            return types.SimpleNamespace(stdout=box["current"], returncode=0)
        box["written"] = kw.get("input")
        box["current"] = kw.get("input")
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(croninstall.subprocess, "run", run)
    return box


def test_installs_into_an_empty_crontab(crontab):
    msg = croninstall.install_cron_line("1 20 * * * hermes freemodels sync")
    assert "installed" in msg
    assert crontab["written"] == "1 20 * * * hermes freemodels sync\n"


def test_appends_without_disturbing_other_entries(crontab):
    crontab["current"] = "0 9 * * * backup.sh\n"
    croninstall.install_cron_line("1 20 * * * hermes freemodels sync")
    assert crontab["written"] == "0 9 * * * backup.sh\n1 20 * * * hermes freemodels sync\n"


def test_replaces_an_existing_entry_in_place(crontab):
    crontab["current"] = (
        "0 9 * * * backup.sh\n"
        "1 5 * * * hermes freemodels sync\n"
        "0 0 * * * other.sh\n"
    )
    msg = croninstall.install_cron_line("1 20 * * * hermes freemodels sync")
    assert "replaced" in msg
    lines = crontab["written"].splitlines()
    assert lines[1] == "1 20 * * * hermes freemodels sync"   # same position
    assert lines[0] == "0 9 * * * backup.sh" and lines[2] == "0 0 * * * other.sh"
    assert len(lines) == 3                                    # no duplicate


def test_reinstalling_the_identical_line_is_a_no_op(crontab):
    line = "1 20 * * * hermes freemodels sync"
    crontab["current"] = line + "\n"
    msg = croninstall.install_cron_line(line)
    assert "nothing to do" in msg
    assert crontab["written"] is None       # crontab never rewritten


def test_install_is_idempotent_across_repeated_calls(crontab):
    line = "1 20 * * * hermes freemodels sync"
    for _ in range(3):
        croninstall.install_cron_line(line)
    assert crontab["current"].count("freemodels sync") == 1

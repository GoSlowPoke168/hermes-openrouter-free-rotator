"""CLI wiring: every subcommand parses, dispatches, and registers.

Guards the failure mode this file invites — adding a subparser and forgetting
the handlers dict (or vice versa), which argparse won't catch and which only
shows up as a KeyError when a user runs the command.
"""

import argparse

import pytest

import hermes_openrouter_free_rotator as plugin
from hermes_openrouter_free_rotator import cli

SUBCOMMANDS = {
    "sync", "status", "list", "remove-catalog", "pin", "unpin", "mode", "install-cron",
}


def _parser():
    p = argparse.ArgumentParser(prog="freemodels")
    plugin._setup_parser(p)
    return p


def _choices(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("no subparsers registered")


def test_the_expected_subcommands_are_registered():
    assert _choices(_parser()) == SUBCOMMANDS


@pytest.mark.parametrize("name", sorted(SUBCOMMANDS))
def test_every_subcommand_dispatches_to_a_handler(name, monkeypatch):
    # The real guard: a subparser with no entry in _dispatch's handlers dict
    # raises KeyError only at runtime, for the user.
    called = []
    for attr in [a for a in dir(cli) if a.startswith("cmd_")]:
        monkeypatch.setattr(cli, attr, lambda args, _a=attr: called.append(_a))

    args = _parser().parse_args([name])
    plugin._dispatch(args)
    assert len(called) == 1, f"{name} dispatched to {called}"


def test_subcommands_parse_with_their_flags():
    p = _parser()
    assert p.parse_args(["sync", "--dry-run", "--force", "--no-catalog", "--json"])
    assert p.parse_args(["status", "--json"]).json is True
    assert p.parse_args(["list", "--json"]).json is True
    assert p.parse_args(["pin", "vendor/m:free", "--force"]).model_id == "vendor/m:free"
    assert p.parse_args(["pin"]).model_id is None
    assert p.parse_args(["mode"]).mode is None
    assert p.parse_args(["mode", "loose"]).mode == "loose"
    assert p.parse_args(["install-cron", "--time", "03:30", "--apply"]).time == "03:30"


def test_the_parser_offers_exactly_the_real_modes():
    from hermes_openrouter_free_rotator.selection import MODES

    p = _parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    mode_action = next(
        a for a in sub.choices["mode"]._actions if a.dest == "mode"
    )
    # The parser hardcodes these (it must not import .selection at load time),
    # so drift between the two would silently reject a valid mode.
    assert set(mode_action.choices) == set(MODES)


@pytest.mark.parametrize("bad", ["nonsense", "--not-a-flag"])
def test_unknown_subcommands_are_rejected(bad):
    with pytest.raises(SystemExit):
        _parser().parse_args([bad])


# ---- register -------------------------------------------------------------

class _Ctx:
    def __init__(self, slash_raises=False):
        self.cli_commands, self.slash_commands = [], []
        self.slash_raises = slash_raises

    def register_cli_command(self, **kw):
        self.cli_commands.append(kw)

    def register_command(self, **kw):
        if self.slash_raises:
            raise RuntimeError("host predates register_command")
        self.slash_commands.append(kw)


def test_register_wires_both_surfaces():
    ctx = _Ctx()
    plugin.register(ctx)
    assert ctx.cli_commands[0]["name"] == "freemodels"
    assert ctx.slash_commands[0]["name"] == "freemodels"
    hint = ctx.slash_commands[0]["args_hint"]
    for verb in ("full", "json", "pin", "unpin", "mode"):
        assert verb in hint, f"{verb} missing from the slash args_hint"


def test_register_survives_a_host_without_slash_commands():
    # An older host must lose the slash command, not the whole plugin.
    ctx = _Ctx(slash_raises=True)
    plugin.register(ctx)
    assert ctx.cli_commands and not ctx.slash_commands


def test_slash_entrypoint_delegates_to_cli(monkeypatch):
    monkeypatch.setattr(cli, "slash_freemodels", lambda raw: f"got:{raw}")
    assert plugin._slash_freemodels("full") == "got:full"

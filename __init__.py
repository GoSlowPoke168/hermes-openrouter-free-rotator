"""hermes-openrouter-free-rotator — auto-select the best private free OpenRouter models.

Free OpenRouter models (":free" variants) come and go — some carry an
expiration_date and simply vanish. This plugin keeps Hermes's config.yaml
(under the Hermes home directory) pointed at the top 3 privacy-respecting
free models from
https://openrouter.ai/collections/free-models: #1 becomes model.default,
#2/#3 become the leading fallback_providers, and any fallback entries the
plugin didn't add (e.g. paid last-resort models) are preserved at the end.

Registers the `hermes freemodels` CLI command: sync / status / list /
install-cron. Designed to run headless from cron once a day.
"""

from __future__ import annotations


def _setup_parser(parser) -> None:
    import argparse

    sub = parser.add_subparsers(dest="fm_cmd", required=True)

    p_sync = sub.add_parser(
        "sync", help="Select the best free models and update config.yaml if needed"
    )
    p_sync.add_argument(
        "--dry-run", action="store_true", help="Show what would change without writing"
    )
    p_sync.add_argument(
        "--force", action="store_true", help="Rewrite config even if selection is unchanged"
    )
    p_sync.add_argument(
        "--no-catalog",
        action="store_true",
        help="Don't refresh the free-model list shown in Hermes's /model picker",
    )
    p_sync.add_argument("--json", action="store_true", help="Machine-readable output")
    p_sync.add_argument("--scheduled-time", metavar="HH:MM", help=argparse.SUPPRESS)

    p_status = sub.add_parser("status", help="Show current selection and last sync result")
    p_status.add_argument("--json", action="store_true", help="Machine-readable output")

    p_list = sub.add_parser(
        "list", help="List all :free candidates with rank, privacy tier and skip reasons"
    )
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")

    sub.add_parser(
        "remove-catalog",
        help="Remove this plugin's free models from Hermes's /model picker",
    )

    p_pin = sub.add_parser(
        "pin",
        help="Switch to a model and stop sync from rotating model.default away from it",
    )
    p_pin.add_argument(
        "model_id",
        nargs="?",
        metavar="MODEL_ID",
        help="Model to pin (default: whatever model.default is set to now)",
    )
    p_pin.add_argument(
        "--force",
        action="store_true",
        help="Pin an id that isn't in the /model catalog",
    )

    sub.add_parser("unpin", help="Resume automatic rotation of model.default")

    p_mode = sub.add_parser(
        "mode", help="Show or set which privacy tiers auto-selection may pick from"
    )
    p_mode.add_argument(
        "mode",
        nargs="?",
        # Names duplicated rather than imported: _setup_parser runs on every
        # plugin discovery and must not pull in .selection. cli._do_mode
        # validates against selection.MODES, which stays the source of truth.
        choices=["strict", "balanced", "relaxed", "loose"],
        help="strict=private only, balanced=private then logs (default), "
        "relaxed=private+logs by rank, loose=also trains-on-prompts by rank. "
        "Omit to show the current mode.",
    )

    p_cron = sub.add_parser(
        "install-cron", help="Preview or install daily scheduling through Hermes"
    )
    p_cron.add_argument(
        "--time",
        default="00:01",
        metavar="HH:MM",
        help="Daily run time in UTC (default 00:01 — shortly after OpenRouter's "
        "free-tier quota resets at 00:00 UTC)",
    )
    p_cron.add_argument(
        "--apply", action="store_true", help="Create/update the schedule (idempotent)"
    )
    p_cron.add_argument("--system", action="store_true", help="Use system crontab instead of Hermes scheduling")


def _dispatch(args) -> None:
    # Lazy import so plugin load stays cheap for every other hermes invocation.
    from .cli import (
        cmd_install_cron,
        cmd_list,
        cmd_mode,
        cmd_pin,
        cmd_remove_catalog,
        cmd_status,
        cmd_sync,
        cmd_unpin,
    )

    handlers = {
        "sync": cmd_sync,
        "status": cmd_status,
        "list": cmd_list,
        "remove-catalog": cmd_remove_catalog,
        "pin": cmd_pin,
        "unpin": cmd_unpin,
        "mode": cmd_mode,
        "install-cron": cmd_install_cron,
    }
    handlers[args.fm_cmd](args)


def _slash_freemodels(raw_args: str) -> str:
    # Same lazy-import discipline as _dispatch: register() runs on every
    # plugin discovery and must not pull in requests / hermes_cli.
    from .cli import slash_freemodels

    return slash_freemodels(raw_args)


def register(ctx) -> None:
    ctx.register_cli_command(
        name="freemodels",
        help="Auto-select the best privacy-respecting free OpenRouter models",
        setup_fn=_setup_parser,
        handler_fn=_dispatch,
        description=(
            "Rotates model.default and fallback_providers to the top free "
            "OpenRouter models whose free endpoints do not train on prompts."
        ),
    )

    # The /model picker renders bare model-id strings, so it cannot show
    # privacy tiers. This is the companion: check tiers here, switch there.
    try:
        ctx.register_command(
            name="freemodels",
            handler=_slash_freemodels,
            description="Ranked free OpenRouter models this plugin manages",
            args_hint="[full|json|pin <model-id>|unpin|mode <name>]",
        )
    except Exception:
        # Older host without register_command — the CLI command still works,
        # and a missing slash command must never break plugin load.
        pass

"""hermes-openrouter-free-rotator — auto-select the best private free OpenRouter models.

Free OpenRouter models (":free" variants) come and go — some carry an
expiration_date and simply vanish. This plugin keeps ~/.hermes/config.yaml
pointed at the top 3 privacy-respecting free models from
https://openrouter.ai/collections/free-models: #1 becomes model.default,
#2/#3 become the leading fallback_providers, and any fallback entries the
plugin didn't add (e.g. paid last-resort models) are preserved at the end.

Registers the `hermes freemodels` CLI command: sync / status / list /
install-cron. Designed to run headless from cron once a day.
"""

from __future__ import annotations


def _setup_parser(parser) -> None:
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
    p_sync.add_argument("--json", action="store_true", help="Machine-readable output")

    p_status = sub.add_parser("status", help="Show current selection and last sync result")
    p_status.add_argument("--json", action="store_true", help="Machine-readable output")

    p_list = sub.add_parser(
        "list", help="List all :free candidates with rank, privacy tier and skip reasons"
    )
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")

    p_cron = sub.add_parser(
        "install-cron", help="Print (or install with --apply) the daily crontab entry"
    )
    p_cron.add_argument(
        "--time", default="06:17", metavar="HH:MM", help="Daily run time (default 06:17)"
    )
    p_cron.add_argument(
        "--apply", action="store_true", help="Add the entry to your crontab (idempotent)"
    )


def _dispatch(args) -> None:
    # Lazy import so plugin load stays cheap for every other hermes invocation.
    from .cli import cmd_install_cron, cmd_list, cmd_status, cmd_sync

    handlers = {
        "sync": cmd_sync,
        "status": cmd_status,
        "list": cmd_list,
        "install-cron": cmd_install_cron,
    }
    handlers[args.fm_cmd](args)


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

#!/usr/bin/env bash
# Remove hermes-openrouter-free-rotator.
#
#   ./uninstall.sh              interactive — asks standard vs purge
#                                (falls back to standard if not run in a
#                                terminal, e.g. piped or scripted)
#   ./uninstall.sh --standard   remove the plugin; keep state + cron. No prompt.
#   ./uninstall.sh --purge      also remove ~/.hermes/freemodels and the
#                                schedules and generated script. No prompt.
set -euo pipefail

PLUGIN_NAME="hermes-openrouter-free-rotator"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_HOME="${HERMES_HOME%/}"
profile_name="default"
if [ "$(basename "$(dirname "$HERMES_HOME")")" = "profiles" ]; then
  profile_name="$(basename "$HERMES_HOME")"
fi
hermes_cmd=(hermes --profile "$profile_name")
TARGET="$HERMES_HOME/plugins/$PLUGIN_NAME"
STATE_DIR="$HERMES_HOME/freemodels"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE=""
case "${1:-}" in
  --purge)    MODE="purge" ;;
  --standard) MODE="standard" ;;
  -h|--help)  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "")         ;;
  *)          echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
esac

if [ -z "$MODE" ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    echo "Uninstall $PLUGIN_NAME"
    echo
    echo "  1) Standard — remove the plugin; keep state (pin, mode, privacy"
    echo "                cache) and the daily cron job"
    echo "  2) Purge    — also remove state, schedules and the generated script"
    echo "  3) Cancel"
    echo
    read -r -p "Choice [1]: " reply
    case "$reply" in
      2) MODE="purge" ;;
      3) echo "cancelled — nothing removed."; exit 0 ;;
      1|"") MODE="standard" ;;
      *) echo "unrecognized choice '$reply' — defaulting to standard." >&2
         MODE="standard" ;;
    esac
  else
    MODE="standard"
    echo "note: no terminal detected — defaulting to a standard uninstall" >&2
    echo "      (state and cron kept). Pass --purge to remove them." >&2
  fi
fi

PURGE=0
[ "$MODE" = "purge" ] && PURGE=1

if [ "$PURGE" -eq 1 ]; then
  cleanup_python="$(command -v python3 || command -v python || true)"
  if [ -z "$cleanup_python" ]; then
    echo "error: put Python 3 on PATH to clean the Hermes schedule before purging." >&2
    exit 1
  fi
  # Load the checkout directly so purge still works after disabling or removing the plugin.
  HERMES_HOME="$HERMES_HOME" "$cleanup_python" - "$SCRIPT_DIR" <<'PY' || exit 1
import importlib.util
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "freemodels_cleanup", root / "__init__.py", submodule_search_locations=[str(root)]
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from freemodels_cleanup.croninstall import remove_hermes_job

try:
    print(remove_hermes_job())
except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
    print(f"error: could not clean Hermes schedule; plugin and state preserved: {exc}", file=sys.stderr)
    sys.exit(1)
PY
fi

# Always clean the /model picker list — it's a display list maintained by a
# plugin that's going away, so leaving stale free models in the picker is a
# bug, not a preserved preference. Must run BEFORE --purge deletes the state
# dir, which holds the ledger of which entries are ours.
if command -v hermes >/dev/null 2>&1; then
  "${hermes_cmd[@]}" freemodels remove-catalog 2>/dev/null || {
    echo "warning: could not remove the free models from /model — run" >&2
    echo "         \`hermes freemodels remove-catalog\` by hand" >&2
  }
fi

if [ -L "$TARGET" ]; then
  rm -f "$TARGET"
  echo "removed symlink $TARGET"
elif [ -d "$TARGET" ]; then
  rm -rf "$TARGET"
  echo "removed $TARGET"
else
  echo "not installed ($TARGET missing)"
fi

if [ "$PURGE" -eq 1 ]; then
  rm -rf "$STATE_DIR"
  echo "removed $STATE_DIR"
  # crontab is per-user, not per-HERMES_HOME — only touch the line logging to our state dir
  cron_log="$STATE_DIR/cron.log"
  if crontab -l 2>/dev/null \
      | awk -v l="$cron_log" 'index($0, "freemodels sync") && index($0, l) { f=1 } END { exit !f }'; then
    remaining="$(crontab -l 2>/dev/null \
      | awk -v l="$cron_log" '!(index($0, "freemodels sync") && index($0, l))' || true)"
    if [ -n "$remaining" ]; then
      printf '%s\n' "$remaining" | crontab -
    else
      crontab -r 2>/dev/null || true
    fi
    echo "removed crontab entry"
  fi
else
  echo "state kept at $STATE_DIR (use --purge to remove it and the schedules)"
fi

echo "note: your model.default and fallback_providers are untouched; only the"
echo "      plugin's free-model list in Hermes's /model picker was removed."

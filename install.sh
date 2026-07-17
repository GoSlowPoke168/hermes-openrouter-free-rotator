#!/usr/bin/env bash
# Install hermes-openrouter-free-rotator into the Hermes plugins directory.
#
#   ./install.sh             copy install (default) — clone can be deleted after
#   ./install.sh --symlink   dev install — symlink this checkout into plugins/
#   ./install.sh --force     replace an existing install of the other kind
#
# If this script is already running from inside $HERMES_HOME/plugins/ (e.g.
# after `hermes plugins install <repo>`), the copy step is skipped and only
# setup is performed.
set -euo pipefail

PLUGIN_NAME="hermes-openrouter-free-rotator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
TARGET="$PLUGINS_DIR/$PLUGIN_NAME"
STATE_DIR="$HERMES_HOME/freemodels"

MODE="copy"
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --symlink) MODE="symlink" ;;
    --force)   FORCE=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 1 ;;
  esac
done

FILES=(plugin.yaml __init__.py openrouter.py selection.py state.py configsync.py
       croninstall.py cli.py README.md after-install.md uninstall.sh)

mkdir -p "$PLUGINS_DIR" "$STATE_DIR"

resolved_target="$(readlink -f "$TARGET" 2>/dev/null || true)"
if [ "$SCRIPT_DIR" = "$TARGET" ] || [ "$SCRIPT_DIR" = "$resolved_target" ]; then
  echo "already installed at $TARGET — skipping copy, running setup only."
elif [ "$MODE" = "symlink" ]; then
  if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    if [ "$FORCE" -eq 1 ]; then
      rm -rf "$TARGET"
    else
      echo "error: $TARGET exists and is not a symlink; rerun with --force to replace." >&2
      exit 1
    fi
  fi
  ln -sfn "$SCRIPT_DIR" "$TARGET"
  echo "symlinked $TARGET -> $SCRIPT_DIR"
else
  if [ -L "$TARGET" ]; then
    if [ "$FORCE" -eq 1 ]; then
      rm -f "$TARGET"
    else
      echo "error: $TARGET is a symlink (dev install); rerun with --force to replace with a copy." >&2
      exit 1
    fi
  fi
  mkdir -p "$TARGET"
  for f in "${FILES[@]}"; do
    [ -e "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" "$TARGET/$f"
  done
  echo "copied plugin to $TARGET"
fi

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable "$PLUGIN_NAME" || \
    echo "warning: could not enable plugin — run: hermes plugins enable $PLUGIN_NAME" >&2
else
  echo "warning: 'hermes' not found on PATH — is Hermes Agent installed?" >&2
  echo "after installing, run: hermes plugins enable $PLUGIN_NAME" >&2
fi

cat <<EOF

Done. Next steps:
  hermes freemodels list                # see ranked candidates + privacy tiers
  hermes freemodels sync --dry-run      # preview the config change
  hermes freemodels sync                # apply it
  hermes freemodels install-cron --apply  # run daily, automatically
EOF

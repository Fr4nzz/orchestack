#!/usr/bin/env bash
# install.sh: install orchestack for Claude Code and Codex from this checkout.
#
#   scripts/install.sh [--dry-run] [--uninstall] [--retire=NAME[,NAME...]] [--watcher]
#
# 1. Symlinks each skills/<name> into ~/.claude/skills and ~/.codex/skills. An existing folder or foreign symlink
#    of the same name is moved (never deleted) to ~/.local/share/orchestack/backups/<ts>/<root>/<name>.bak-<ts>.
#    Backups go outside the skill folders because agents load every SKILL.md they find there, backups included.
# 2. --retire=NAME,... also moves your own older skills of those names to the same backup folder (off by default).
# 3. Merges the hook registrations (hooks/hooks.json) into ~/.claude/settings.json after a timestamped backup.
# 4. Adds one line to ~/.claude/CLAUDE.md if it is not there yet (after a timestamped backup).
# 5. --watcher (off by default) installs the change-only watcher: writes orchestack-watch.{service,timer} to
#    ~/.config/systemd/user and enables the timer (every 15 min). Try watcher/watch.py --dry-run first.
# --uninstall removes our symlinks, hook registrations, CLAUDE.md line and watcher units, and restores the newest
# backup of each skill name if its slot is empty. --dry-run prints every step and changes nothing.
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
TS=$(date +%Y%m%d-%H%M%S)
DRY=0 UNINSTALL=0 WATCHER=0
SUPERSEDED=()
for a in "$@"; do
  case $a in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --watcher) WATCHER=1 ;;
    --retire=*) IFS=, read -r -a SUPERSEDED <<< "${a#--retire=}" ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done

ROOTS=("$HOME/.claude/skills" "$HOME/.codex/skills")
BACKUP_BASE="$HOME/.local/share/orchestack/backups"
BACKUP="$BACKUP_BASE/$TS"
SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
UNIT_DIR="$HOME/.config/systemd/user"
UNITS=(orchestack-watch.service orchestack-watch.timer)
LINE="Orchestrator or lead chats (told 'you are the orchestrator', or titled Orchestrator:/Lead:): load the \`orchestrator\` skill at start and again after every compaction."

say() { echo "$*"; }
run() { if (( DRY )); then echo "  would run: $*"; else "$@"; fi; }
label() { basename "$(dirname "$(dirname "$1")")"; }   # <root>/skills/<name> -> .claude or .codex

backup() {  # path
  local src=$1 dst
  dst="$BACKUP/$(label "$src")/$(basename "$src").bak-$TS"
  run mkdir -p "$(dirname "$dst")"
  run mv -- "$src" "$dst"
  (( DRY )) || say "backed up $src -> $dst"
}

newest_backup() {  # root-label name
  local d
  d=$(find "$BACKUP_BASE" -mindepth 3 -maxdepth 3 -path "*/$1/$2.bak-*" 2>/dev/null | sort | tail -1)
  printf '%s' "$d"
}

skills=()
for s in "$REPO"/skills/*/; do skills+=("$(basename "$s")"); done

# --retire names: skip empty ones, refuse anything that is not a plain skill name or is one of ours.
checked=()
for name in ${SUPERSEDED[@]+"${SUPERSEDED[@]}"}; do
  [[ -z $name ]] && continue
  if [[ ! $name =~ ^[A-Za-z0-9._-]+$ || $name == . || $name == .. ]]; then
    echo "refusing --retire name '$name' (not a plain skill name)" >&2; exit 2
  fi
  for own in "${skills[@]}"; do
    if [[ $name == "$own" ]]; then echo "refusing --retire name '$name' (an orchestack skill)" >&2; exit 2; fi
  done
  checked+=("$name")
done
SUPERSEDED=(${checked[@]+"${checked[@]}"})

if (( UNINSTALL )); then
  for root in "${ROOTS[@]}"; do
    for name in "${skills[@]}" ${SUPERSEDED[@]+"${SUPERSEDED[@]}"}; do
      dst=$root/$name
      if [[ -L $dst && $(readlink "$dst") == "$REPO/skills/$name" ]]; then
        run unlink -- "$dst"; say "unlinked $dst"
      fi
      if [[ ! -e $dst && ! -L $dst ]]; then
        b=$(newest_backup "$(label "$dst")" "$name")
        if [[ -n $b ]]; then run mv -- "$b" "$dst"; say "restored $dst <- $b"; fi
      fi
    done
  done
  if [[ -f $SETTINGS ]]; then
    run cp -p -- "$SETTINGS" "$SETTINGS.bak-orchestack-$TS"
    if (( DRY )); then
      python3 -I "$REPO/scripts/merge_settings.py" "$SETTINGS" "$REPO/hooks/hooks.json" "$REPO" --uninstall --dry-run
    else
      python3 -I "$REPO/scripts/merge_settings.py" "$SETTINGS" "$REPO/hooks/hooks.json" "$REPO" --uninstall
      say "removed orchestack hooks from $SETTINGS"
    fi
  fi
  if [[ -f $CLAUDE_MD ]] && grep -qxF -- "$LINE" "$CLAUDE_MD"; then
    run cp -p -- "$CLAUDE_MD" "$CLAUDE_MD.bak-orchestack-$TS"
    if (( DRY )); then say "  would remove the orchestack line from $CLAUDE_MD"
    else
      python3 -I - "$CLAUDE_MD" "$LINE" <<'PY'
import sys
path, line = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
out = [l for l in lines if l.rstrip("\n") != line]
# drop the blank line install added before ours
for i, l in enumerate(lines):
    if l.rstrip("\n") == line and i > 0 and lines[i - 1] == "\n" and lines[i - 1] in out:
        out.remove(lines[i - 1])
        break
open(path, "w", encoding="utf-8").write("".join(out))
PY
      say "removed the orchestack line from $CLAUDE_MD"
    fi
  fi
  if [[ -f $UNIT_DIR/orchestack-watch.timer || -f $UNIT_DIR/orchestack-watch.service ]]; then
    run systemctl --user disable --now orchestack-watch.timer
    for u in "${UNITS[@]}"; do
      if [[ -f $UNIT_DIR/$u ]]; then run rm -f -- "$UNIT_DIR/$u"; say "removed $UNIT_DIR/$u"; fi
    done
    run systemctl --user daemon-reload
  fi
  exit 0
fi

# --- install ---
for root in "${ROOTS[@]}"; do
  [[ -d $root ]] || run mkdir -p "$root"
  for name in "${skills[@]}"; do
    src=$REPO/skills/$name dst=$root/$name
    if [[ -L $dst && $(readlink "$dst") == "$src" ]]; then say "ok       $dst"; continue; fi
    if [[ -e $dst || -L $dst ]]; then backup "$dst"; fi
    run ln -s -- "$src" "$dst"; say "linked   $dst -> $src"
  done
  for name in ${SUPERSEDED[@]+"${SUPERSEDED[@]}"}; do
    if [[ -e $root/$name || -L $root/$name ]]; then backup "$root/$name"; fi
  done
done

mkdir_settings_dir() { [[ -d $(dirname "$SETTINGS") ]] || run mkdir -p "$(dirname "$SETTINGS")"; }
mkdir_settings_dir
if [[ -f $SETTINGS ]]; then run cp -p -- "$SETTINGS" "$SETTINGS.bak-orchestack-$TS"; fi
if (( DRY )); then
  say "  would write $SETTINGS as:"
  python3 -I "$REPO/scripts/merge_settings.py" "$SETTINGS" "$REPO/hooks/hooks.json" "$REPO" --dry-run | sed 's/^/    /'
else
  python3 -I "$REPO/scripts/merge_settings.py" "$SETTINGS" "$REPO/hooks/hooks.json" "$REPO"
  say "merged orchestack hooks into $SETTINGS"
fi

if [[ -f $CLAUDE_MD ]] && grep -qxF -- "$LINE" "$CLAUDE_MD"; then
  say "ok       $CLAUDE_MD already has the orchestack line"
else
  if [[ -f $CLAUDE_MD ]]; then run cp -p -- "$CLAUDE_MD" "$CLAUDE_MD.bak-orchestack-$TS"; fi
  if (( DRY )); then say "  would append to $CLAUDE_MD: $LINE"
  else printf '\n%s\n' "$LINE" >> "$CLAUDE_MD"; say "added the orchestack line to $CLAUDE_MD"
  fi
fi

if (( WATCHER )); then
  [[ -d $UNIT_DIR ]] || run mkdir -p "$UNIT_DIR"
  if (( DRY )); then
    say "  would write $UNIT_DIR/orchestack-watch.service (runs $REPO/watcher/watch.py)"
    say "  would write $UNIT_DIR/orchestack-watch.timer (every 15 min)"
  else
    sed "s|@REPO@|$REPO|g" "$REPO/watcher/orchestack-watch.service" > "$UNIT_DIR/orchestack-watch.service"
    cp -- "$REPO/watcher/orchestack-watch.timer" "$UNIT_DIR/orchestack-watch.timer"
    say "wrote $UNIT_DIR/orchestack-watch.{service,timer}"
  fi
  run systemctl --user daemon-reload
  run systemctl --user enable --now orchestack-watch.timer
  if ! python3 -I -c 'import json,os,sys; p=os.path.expanduser("~/.config/orchestack/config.json"); sys.exit(0 if "watcher" in json.load(open(p)) else 1)' 2>/dev/null; then
    say "note: config.json has no watcher section yet, so each run does nothing (see examples/config.example.json)"
  fi
fi

[[ -f $HOME/.config/orchestack/local.md ]] || say "note: no ~/.config/orchestack/local.md (see examples/local.example.md)"
say "done. Check with scripts/check_drift.sh"

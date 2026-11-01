#!/usr/bin/env bash
# check_drift.sh: report installed skills that are not symlinks into this repo, or that differ from it.
#
#   scripts/check_drift.sh
#
# For each repo skill in ~/.claude/skills and ~/.codex/skills it prints one of:
#   ok        symlink into this repo
#   MISSING   not installed
#   ELSEWHERE symlink to another path (same or different content)
#   COPY      a real folder with the same content (hash equal), not linked, so it will drift
#   DRIFT     a real folder whose content differs (hash shown)
# Superseded skills (orchestrator-playbook, dual-critic-loop) still installed are listed as OLD.
# The hook registrations in ~/.claude/settings.json are checked too. Exit 0 if all ok, 1 otherwise.
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ROOTS=("$HOME/.claude/skills" "$HOME/.codex/skills")
SUPERSEDED=(orchestrator-playbook dual-critic-loop)
bad=0

tree_hash() {  # folder: hash of relative paths + contents, backups and caches skipped
  (cd "$1" && find . -type f ! -name '*.bak*' ! -path '*/__pycache__/*' -print0 | LC_ALL=C sort -z \
    | xargs -0 sha256sum) | sha256sum | cut -c1-12
}

for root in "${ROOTS[@]}"; do
  for s in "$REPO"/skills/*/; do
    name=$(basename "$s") src=${s%/} dst=$root/$name
    if [[ -L $dst ]]; then
      target=$(readlink "$dst")
      if [[ $target == "$src" ]]; then echo "ok        $dst"; continue; fi
      state=different; [[ -d $dst && $(tree_hash "$dst") == "$(tree_hash "$src")" ]] && state=same
      echo "ELSEWHERE $dst -> $target ($state content)"; bad=1
    elif [[ -d $dst ]]; then
      h=$(tree_hash "$dst") r=$(tree_hash "$src")
      if [[ $h == "$r" ]]; then echo "COPY      $dst (same hash $h, not linked)"
      else echo "DRIFT     $dst (installed $h, repo $r)"; fi
      bad=1
    else
      echo "MISSING   $dst"; bad=1
    fi
  done
  for name in "${SUPERSEDED[@]}"; do
    if [[ -e $root/$name ]]; then echo "OLD       $root/$name (superseded by orchestack)"; bad=1; fi
  done
done

SETTINGS=$HOME/.claude/settings.json
if [[ -f $SETTINGS ]] && python3 -I - "$SETTINGS" "$REPO/hooks/" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
cmds = [h.get("command", "") for gs in s.get("hooks", {}).values() for g in gs for h in g.get("hooks", [])]
need = ["role_card.py", "memory_gate.py"]
sys.exit(0 if all(any(sys.argv[2] + n in c for c in cmds) for n in need) else 1)
PY
then echo "ok        hooks registered in $SETTINGS"
else echo "MISSING   orchestack hooks in $SETTINGS"; bad=1
fi

exit $bad

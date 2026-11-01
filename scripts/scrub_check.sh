#!/usr/bin/env bash
# scrub_check.sh: fail if files that would be committed contain private identifiers.
#
#   scripts/scrub_check.sh [PATH ...]      (default: every tracked and untracked, non-ignored file)
#
# Two pattern sets, both extended regexes:
#   1. Built-in generic patterns below (host-like names, personal home and scratch paths, emails, job-id
#      phrases). Safe to publish.
#   2. Personal patterns: one regex per line in $ORCHESTACK_SCRUB_PATTERNS, or .scrub-patterns.local at the
#      repo root (git-ignored). Put your user name, uid, group, team paths and project names there. In CI,
#      write that file from a secret. Lines starting with # are ignored. Personal patterns ignore case.
# Exit status: 0 clean, 1 hits found.
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"

GENERIC=(
  '/home/[a-z][a-z0-9_-]+/'            # personal home directories (use $HOME or ~)
  '/(lustre|gpfs|scratch)[^ ]*/users/'  # personal scratch paths
  '\b[a-z]+-(head|login)[0-9]+\b'       # login node names
  '\bnode-[0-9]+-[0-9]+\b'              # compute host names
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(ac\.uk|edu|org|com)\b'   # email addresses
  '\b[Jj]ob <?[0-9]{5,}>?'             # concrete job ids
  '\buid[ =]?[0-9]{4,}\b'               # numeric uids
  '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'   # thread, session and project ids
  '\.ts\.net\b'                          # tailnet host names
)

PATTERN_FILE=${ORCHESTACK_SCRUB_PATTERNS:-$REPO/.scrub-patterns.local}
PERSONAL=()
if [[ -f $PATTERN_FILE ]]; then
  while IFS= read -r line; do
    [[ -z $line || $line == \#* ]] && continue
    PERSONAL+=("${line//\\d/[0-9]}")   # grep -E has no \d
  done < "$PATTERN_FILE"
else
  echo "note: no personal pattern file ($PATTERN_FILE). Only generic patterns are checked." >&2
fi

if (( $# )); then
  mapfile -t FILES < <(printf '%s\n' "$@")
elif git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t FILES < <(find . -type f -not -path './.git/*' | sed 's|^\./||')
fi

# never scan the pattern definitions themselves
SELF=scripts/scrub_check.sh
LOCAL=$(realpath --relative-to="$REPO" "$PATTERN_FILE" 2>/dev/null || echo "$PATTERN_FILE")
CHECK=()
for f in "${FILES[@]}"; do
  [[ $f == "$SELF" || $f == "$LOCAL" || ! -f $f ]] && continue
  CHECK+=("$f")
done

hits=0
check() {  # label, regex, [extra grep flags...]
  local out label=$1 re=$2
  shift 2
  if out=$(grep -nIE "$@" -- "$re" "${CHECK[@]}" 2>/dev/null); then
    echo "== $label: $re"
    echo "$out" | head -50
    hits=$(( hits + $(echo "$out" | wc -l) ))
  fi
}
for p in "${GENERIC[@]}"; do check generic "$p"; done
for p in "${PERSONAL[@]}"; do check personal "$p" -i; done   # personal patterns ignore case
# file names too
for p in "${GENERIC[@]}" "${PERSONAL[@]}"; do
  if names=$(printf '%s\n' "${CHECK[@]}" | grep -iE -- "$p"); then
    echo "== file name: $p"; echo "$names"; hits=$(( hits + $(echo "$names" | wc -l) ))
  fi
done

if (( hits )); then echo "scrub_check: $hits hit(s) in ${#CHECK[@]} files"; exit 1; fi
echo "scrub_check: clean (${#CHECK[@]} files, ${#GENERIC[@]} generic + ${#PERSONAL[@]} personal patterns)"

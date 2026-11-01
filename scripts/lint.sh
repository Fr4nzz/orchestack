#!/usr/bin/env bash
# lint.sh: repository checks. Run before every commit.
#
#   scripts/lint.sh
#
# Checks: SKILL.md frontmatter (name = folder, description with "Use when"), SKILL.md length (<= 200 lines, <= 20,000 characters, so a re-attached skill stays whole),
# agents/openai.yaml present, plugin.json lists exactly the skill folders, JSON validity, `bash -n` and
# `shellcheck` (if installed) on shell scripts, Python syntax of Python scripts, executable bits on helper
# scripts, no em dashes anywhere, no semicolons in Markdown prose (code spans and blocks are ignored).
set -Eeuo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"
fail=0
err() { echo "ERROR: $*"; fail=1; }

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t FILES < <(find . -type f -not -path './.git/*' | sed 's|^\./||')
fi

# --- skills, manifests, prose (python) ---
python3 - "${FILES[@]}" <<'PY' || fail=1
import json, os, re, sys
files = [f for f in sys.argv[1:] if os.path.isfile(f)]
bad = 0
def err(msg):
    global bad
    bad += 1
    print("ERROR: " + msg)

skills = sorted(d for d in os.listdir("skills") if os.path.isdir(os.path.join("skills", d)))
for s in skills:
    p = f"skills/{s}/SKILL.md"
    if not os.path.isfile(p):
        err(f"{p} missing"); continue
    text = open(p).read()
    lines = text.splitlines()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        err(f"{p}: no YAML frontmatter"); continue
    fm = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
    if fm.get("name") != s:
        err(f"{p}: name {fm.get('name')!r} != folder {s!r}")
    desc = fm.get("description", "")
    if "Use when" not in desc:
        err(f"{p}: description must say when to use it ('Use when ...')")
    if len(desc) > 1024:
        err(f"{p}: description longer than 1024 characters")
    if len(lines) > 200 or len(text) > 20000:
        err(f"{p}: {len(lines)} lines, {len(text)} chars, budget is 200 lines and 20,000 chars")
    y = f"skills/{s}/agents/openai.yaml"
    if not os.path.isfile(y):
        err(f"{y} missing")
    else:
        yt = open(y).read()
        for k in ("display_name", "short_description"):
            if k not in yt:
                err(f"{y}: no {k}")
    for ref in re.findall(r"`((?:references|scripts)/[\w./-]+)`", text):
        if not os.path.exists(f"skills/{s}/{ref}"):
            err(f"{p}: mentions {ref}, which does not exist")

for f in files:
    if f.endswith(".json"):
        try:
            json.load(open(f))
        except Exception as e:
            err(f"{f}: invalid JSON: {e}")
plugin = json.load(open(".claude-plugin/plugin.json"))
listed = sorted(x.rstrip("/").split("/")[-1] for x in plugin.get("skills", []))
if listed != skills:
    err(f"plugin.json skills {listed} != folders {skills}")
market = json.load(open(".claude-plugin/marketplace.json"))
if not any(pl.get("name") == plugin.get("name") for pl in market.get("plugins", [])):
    err("marketplace.json does not list the plugin named in plugin.json")

for f in files:
    try:
        text = open(f, encoding="utf-8").read()
    except (UnicodeDecodeError, IsADirectoryError):
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if chr(0x2014) in line:
            err(f"{f}:{i}: em dash")
    if f.endswith(".md"):
        in_code = False
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code; continue
            if in_code or line.startswith("    "):
                continue
            prose = re.sub(r"`[^`]*`", "", line)
            prose = re.sub(r"<[^>]+>|\]\([^)]*\)", "", prose)
            if ";" in prose:
                err(f"{f}:{i}: semicolon in prose")
sys.exit(1 if bad else 0)
PY

# --- scripts ---
for f in "${FILES[@]}"; do
  [[ -f $f ]] || continue
  first=$(head -c 200 "$f" | head -1)
  case $first in
    '#!'*bash*|'#!'*/sh)
      bash -n "$f" || err "$f: bash -n failed"
      if command -v shellcheck >/dev/null 2>&1; then shellcheck -S warning "$f" || err "$f: shellcheck"; fi
      ;;
    '#!'*python*)
      python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$f" || err "$f: python syntax"
      ;;
  esac
  if [[ $f == */scripts/* || $f == scripts/* ]] && [[ $first == '#!'* ]] && [[ ! -x $f ]]; then
    err "$f: has a shebang but is not executable"
  fi
done
command -v shellcheck >/dev/null 2>&1 || echo "note: shellcheck not installed, skipped"

if (( fail )); then echo "lint: FAILED"; exit 1; fi
echo "lint: ok (${#FILES[@]} files)"

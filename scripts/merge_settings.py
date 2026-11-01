#!/usr/bin/env python3
"""merge_settings.py: add or remove the orchestack hook registrations in a Claude Code settings.json.

  merge_settings.py SETTINGS HOOKS_JSON REPO_ROOT [--uninstall] [--dry-run]

HOOKS_JSON is the repo's hooks/hooks.json. "${CLAUDE_PLUGIN_ROOT}" in it is replaced by REPO_ROOT. Existing keys
and other hooks are kept. A hook entry is ours when its command points into REPO_ROOT/hooks/; only those entries are
removed, so other hooks in the same matcher group survive. Install is idempotent: our old entries are replaced by
the current ones. A symlinked settings file is written through the link and keeps its mode. With --dry-run the result is printed, not written.
The caller makes the backup.
"""
import json
import os
import shutil
import sys


def ours(hook, hooks_dir):
    return hooks_dir in (hook.get("command") or "")


def strip_ours(groups, hooks_dir):
    kept = []
    for g in groups:
        entries = g.get("hooks", [])
        rest = [h for h in entries if not ours(h, hooks_dir)]
        if len(rest) == len(entries):
            kept.append(g)
        elif rest:
            kept.append({**g, "hooks": rest})
    return kept


def merge(settings, registrations, hooks_dir, uninstall=False):
    hooks = settings.setdefault("hooks", {})
    for event in set(hooks) | set(registrations):
        kept = strip_ours(hooks.get(event, []), hooks_dir)
        if not uninstall:
            kept += registrations.get(event, [])
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    return settings


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    settings_path, hooks_json, repo = args
    repo = os.path.realpath(repo)
    hooks_dir = os.path.join(repo, "hooks") + os.sep
    with open(hooks_json, encoding="utf-8") as f:
        text = f.read().replace("${CLAUDE_PLUGIN_ROOT}", repo)
    registrations = json.loads(text)["hooks"]
    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    merged = merge(settings, registrations, hooks_dir, uninstall="--uninstall" in flags)
    out = json.dumps(merged, indent=2) + "\n"
    if "--dry-run" in flags:
        sys.stdout.write(out)
        return 0
    target = os.path.realpath(settings_path)
    tmp = target + ".orchestack-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    if os.path.exists(target):
        shutil.copymode(target, tmp)
    else:
        os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

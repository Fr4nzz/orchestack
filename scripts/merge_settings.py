#!/usr/bin/env python3
"""merge_settings.py: add or remove the orchestack hook registrations in a Claude Code settings.json.

  merge_settings.py SETTINGS HOOKS_JSON REPO_ROOT [--uninstall] [--dry-run]

HOOKS_JSON is the repo's hooks/hooks.json. "${CLAUDE_PLUGIN_ROOT}" in it is replaced by REPO_ROOT. Existing keys
and other hooks are kept. A matcher group is ours when one of its commands points into REPO_ROOT/hooks/. Install is
idempotent: our old groups are replaced by the current ones. With --dry-run the result is printed, not written.
The caller makes the backup.
"""
import json
import os
import sys


def ours(group, hooks_dir):
    return any(hooks_dir in (h.get("command") or "") for h in group.get("hooks", []))


def merge(settings, registrations, hooks_dir, uninstall=False):
    hooks = settings.setdefault("hooks", {})
    for event in set(hooks) | set(registrations):
        kept = [g for g in hooks.get(event, []) if not ours(g, hooks_dir)]
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
    tmp = settings_path + ".orchestack-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, settings_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

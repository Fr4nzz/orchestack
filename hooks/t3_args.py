#!/usr/bin/env python3
"""PreToolUse hook: fix the T3 MCP tool inputs agents most often get wrong, before the call fails.

- t3_thread_send with mode "steer": "auto" does the same on a running turn and also starts an idle thread,
  where "steer" fails with thread_not_sendable.
- t3_background_wait with "taskId": becomes "taskIds": [taskId]. A "waiting" that is not a boolean (a label)
  becomes true, or false for the text "false".
- A threadId that is not a thread but names exactly one as a bare UUID (missing its "mcp:" or "mcp:launch:"
  prefix) or as an id prefix of at least 8 characters: replaced by that thread's id. Skipped when the call
  names another environment.

It prints an updatedInput only when it changed something, and stays silent on any error, so the call runs as
the agent wrote it. Read-only, with a 0.1 s database timeout.
"""
import json
import os
import re
import sys

THREAD_TOOLS = re.compile(r"^mcp__t3-code__(t3_thread_\w+|task_status|task_cancel)$")
PREFIXES = ("", "mcp:", "mcp:launch:")
SHORT_ID = re.compile(r"^[0-9a-f][0-9a-f-]{7,}$")
TABLE = "orchestration_v2_projection_threads"


def db_path():
    home = os.environ.get("T3CODE_HOME")
    if not home:
        return None
    path = os.path.join(os.path.expanduser(home), "userdata", "statev2.sqlite")
    return path if os.path.isfile(path) else None


def resolve_thread(thread_id):
    """The one thread this id names, or None when it already exists, matches none or matches several."""
    path = db_path()
    if not path or not SHORT_ID.match(thread_id):
        return None
    import sqlite3
    from urllib.parse import quote

    conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=0.1)
    try:
        def exists(tid):
            return conn.execute(f"SELECT 1 FROM {TABLE} WHERE thread_id = ?", (tid,)).fetchone() is not None

        if exists(thread_id):
            return None
        for prefix in PREFIXES[1:]:
            if exists(prefix + thread_id):
                return prefix + thread_id
        matches = set()
        for prefix in PREFIXES:
            lo = prefix + thread_id
            rows = conn.execute(f"SELECT thread_id FROM {TABLE} WHERE thread_id >= ? AND thread_id < ? LIMIT 3",
                                (lo, lo + "￿")).fetchall()
            matches.update(r[0] for r in rows)
        return matches.pop() if len(matches) == 1 else None
    finally:
        conn.close()


def fixed_input(tool, ti):
    out = dict(ti)
    if tool == "mcp__t3-code__t3_thread_send" and out.get("mode") == "steer":
        out["mode"] = "auto"
    if tool == "mcp__t3-code__t3_background_wait":
        if "taskId" in out and "taskIds" not in out:
            task = out.pop("taskId")
            out["taskIds"] = task if isinstance(task, list) else [task]
        if "waiting" in out and not isinstance(out["waiting"], bool):
            out["waiting"] = str(out["waiting"]).strip().lower() != "false"
    if THREAD_TOOLS.match(tool) and isinstance(out.get("threadId"), str) and not out.get("environmentId"):
        try:
            resolved = resolve_thread(out["threadId"].strip())
        except Exception:
            resolved = None
        if resolved:
            out["threadId"] = resolved
    return out


def main():
    try:
        data = json.load(sys.stdin)
        tool, ti = data.get("tool_name", ""), data.get("tool_input")
        if not isinstance(ti, dict) or not tool.startswith("mcp__t3-code__"):
            return 0
        out = fixed_input(tool, ti)
        if out != ti:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": out}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""orchestack role card: a Claude Code SessionStart hook (matcher startup|resume|compact).

If this T3 chat is an orchestrator or lead, print a short role card to stdout. Claude Code adds SessionStart
stdout to the model's context, so the role survives compaction and resume.

A chat is a role chat when its thread id (env T3CODE_THREAD_ID) is listed in roles.json
({"<thread id>": "orchestrator" | "lead" | "none"}), or when its T3 title starts with "Orchestrator:" or "Lead:".
The title is looked up only if config.json names the T3 state database (role_card.t3_state_db). The lookup is
read-only and by primary key only. The database can be many GB, so it is never scanned.

Config directory: $ORCHESTACK_CONFIG_DIR, else ~/.config/orchestack. Always exits 0. Any error means no card.
"""
import json
import os
import signal
import sys

TITLE_PREFIXES = (("Orchestrator:", "orchestrator"), ("Lead:", "lead"))
TITLE_SQL = "SELECT title FROM orchestration_v2_projection_threads WHERE thread_id = ?"
DEFAULT_SKILL = "~/.claude/skills/orchestrator/SKILL.md"


def config_dir():
    return os.path.expanduser(os.environ.get("ORCHESTACK_CONFIG_DIR") or "~/.config/orchestack")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def title_role(db_path, thread_id):
    """Return the role implied by the thread's T3 title, or None. Read-only primary-key lookup."""
    if not db_path:
        return None
    db_path = os.path.expanduser(db_path)
    if not os.path.isfile(db_path):
        return None
    import sqlite3
    from urllib.parse import quote

    conn = sqlite3.connect(f"file:{quote(db_path)}?mode=ro", uri=True, timeout=0.1)
    try:
        row = conn.execute(TITLE_SQL, (thread_id,)).fetchone()
    finally:
        conn.close()
    title = (row[0] if row else "") or ""
    for prefix, role in TITLE_PREFIXES:
        if title.startswith(prefix):
            return role
    return None


def card(role, thread_id, source, skill_path, overlay):
    who = "ORCHESTRATOR" if role == "orchestrator" else "LEAD"
    up = "the user" if role == "orchestrator" else "your orchestrator"
    lines = [
        f"orchestack role card ({source}): you are the {who} of this chat (thread {thread_id}).",
        f"1. Now, before any brief: read the installed skill {skill_path}"
        + (f" and {overlay}" if overlay else "")
        + ". Never rely on an in-context copy.",
        "2. You route and decide. Split work into independent lanes and run them in parallel via delegates."
        " Do not do multi-step work yourself.",
        "3. Decide reversible, in-budget steps yourself and report afterwards."
        " Ask only about irreversible or outward-facing steps.",
        '4. Never end a reply with "want me to...?" while approved work that needs no answer remains:'
        " start it in the same reply.",
        f"5. Progress is not news. Only decision:, done:, failed: or handoff: go to {up}."
        " FYIs go to the notes file.",
        "6. Long waits: a job file for the watcher (or one wake at the projected finish) plus a state file."
        " Never sleep-poll.",
    ]
    return "\n".join(lines)


def main():
    try:
        signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
        signal.alarm(1)
    except Exception:
        pass
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = {}
    thread_id = os.environ.get("T3CODE_THREAD_ID", "").strip()
    if not thread_id:
        return
    cdir = config_dir()
    roles = load_json(os.path.join(cdir, "roles.json"))
    cfg = load_json(os.path.join(cdir, "config.json")).get("role_card", {}) or {}
    role = roles.get(thread_id)
    if role is None:
        try:
            role = title_role(cfg.get("t3_state_db"), thread_id)
        except Exception:
            role = None
    if role not in ("orchestrator", "lead"):
        return
    skill_path = os.path.expanduser(cfg.get("skill_path") or DEFAULT_SKILL)
    overlay = os.path.join(cdir, "local.md")
    overlay = overlay if os.path.isfile(overlay) else ""
    source = event.get("source") or "startup"
    sys.stdout.write(card(role, thread_id, source, skill_path, overlay) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

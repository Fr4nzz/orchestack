"""Tests for hooks/role_card.py, hooks/memory_gate.py and scripts/merge_settings.py.

Run: python3 -m pytest tests   (or: uvx pytest tests)
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROLE_CARD = REPO / "hooks" / "role_card.py"
MEMORY_GATE = REPO / "hooks" / "memory_gate.py"
MERGE = REPO / "scripts" / "merge_settings.py"

MEMINFO = """MemTotal:       16000000 kB
MemFree:          500000 kB
MemAvailable:    {avail} kB
SwapTotal:      16000000 kB
SwapFree:        {swap} kB
"""


def run(script, stdin, env):
    full = {"PATH": os.environ.get("PATH", ""), "HOME": env.pop("HOME", "/nonexistent")}
    full.update(env)
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-I", str(script)], input=stdin, capture_output=True, text=True,
                       env=full, timeout=10)
    return p, time.monotonic() - t0


def make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orchestration_v2_projection_threads (thread_id TEXT PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO orchestration_v2_projection_threads VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def write_cfg(cdir, roles=None, cfg=None, local=False):
    cdir.mkdir(parents=True, exist_ok=True)
    if roles is not None:
        (cdir / "roles.json").write_text(json.dumps(roles))
    if cfg is not None:
        (cdir / "config.json").write_text(json.dumps(cfg))
    if local:
        (cdir / "local.md").write_text("# overlay\n")


# ---------- role card ----------

def test_role_from_registry(tmp_path):
    cdir = tmp_path / "cfg"
    write_cfg(cdir, roles={"thread-orch-1": "orchestrator"}, local=True)
    p, dt = run(ROLE_CARD, json.dumps({"hook_event_name": "SessionStart", "source": "compact"}),
                {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "thread-orch-1"})
    assert p.returncode == 0
    out = p.stdout.strip().splitlines()
    assert 1 < len(out) <= 12
    assert "ORCHESTRATOR" in out[0] and "(compact)" in out[0]
    assert "orchestrator/SKILL.md" in p.stdout and "local.md" in p.stdout
    assert "want me to" in p.stdout and "Progress is not news" in p.stdout
    assert dt < 1.0  # generous for CI; the hook itself takes a few tens of ms


def test_role_from_title(tmp_path):
    cdir = tmp_path / "cfg"
    db = tmp_path / "state.sqlite"
    make_db(db, [("thread-lead-1", "Lead: mobile app"), ("thread-w-1", "Fix login bug")])
    write_cfg(cdir, roles={}, cfg={"role_card": {"t3_state_db": str(db)}})
    p, _ = run(ROLE_CARD, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "thread-lead-1"})
    assert p.returncode == 0 and "LEAD" in p.stdout and "your orchestrator" in p.stdout
    p, _ = run(ROLE_CARD, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "thread-w-1"})
    assert p.returncode == 0 and p.stdout == ""


def test_registry_none_overrides_title(tmp_path):
    cdir = tmp_path / "cfg"
    db = tmp_path / "state.sqlite"
    make_db(db, [("thread-x", "Orchestrator: shop")])
    write_cfg(cdir, roles={"thread-x": "none"}, cfg={"role_card": {"t3_state_db": str(db)}})
    p, _ = run(ROLE_CARD, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "thread-x"})
    assert p.returncode == 0 and p.stdout == ""


def test_no_thread_id_or_broken_inputs_are_silent(tmp_path):
    cdir = tmp_path / "cfg"
    cdir.mkdir()
    (cdir / "roles.json").write_text("{not json")
    (cdir / "config.json").write_text(json.dumps({"role_card": {"t3_state_db": str(tmp_path / "missing.db")}}))
    p, _ = run(ROLE_CARD, "garbage", {"ORCHESTACK_CONFIG_DIR": str(cdir)})
    assert p.returncode == 0 and p.stdout == "" and p.stderr == ""
    p, _ = run(ROLE_CARD, "garbage", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "t"})
    assert p.returncode == 0 and p.stdout == "" and p.stderr == ""


def test_corrupt_db_is_skipped(tmp_path):
    cdir = tmp_path / "cfg"
    db = tmp_path / "state.sqlite"
    db.write_bytes(b"this is not a database" * 100)
    write_cfg(cdir, cfg={"role_card": {"t3_state_db": str(db)}})
    p, _ = run(ROLE_CARD, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "t"})
    assert p.returncode == 0 and p.stdout == ""


def test_db_is_opened_read_only(tmp_path):
    cdir = tmp_path / "cfg"
    db = tmp_path / "state.sqlite"
    make_db(db, [("t", "Lead: x")])
    before = db.stat().st_mtime_ns
    write_cfg(cdir, cfg={"role_card": {"t3_state_db": str(db)}})
    run(ROLE_CARD, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "T3CODE_THREAD_ID": "t"})
    assert db.stat().st_mtime_ns == before
    assert not (tmp_path / "state.sqlite-journal").exists()


# ---------- memory gate ----------

def gate(tmp_path, avail_kb, swap_kb=0, cfg=None, tool="Agent", tool_input=None):
    cdir = tmp_path / "cfg"
    write_cfg(cdir, cfg=cfg)
    mi = tmp_path / "meminfo"
    mi.write_text(MEMINFO.format(avail=avail_kb, swap=swap_kb))
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input or {}}
    p, _ = run(MEMORY_GATE, json.dumps(event), {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(mi)})
    assert p.returncode == 0
    return p.stdout


GIB = 1024 * 1024


def test_gate_allows_silently_above_threshold(tmp_path):
    assert gate(tmp_path, 8 * GIB) == ""


def test_gate_denies_below_default_threshold(tmp_path):
    out = json.loads(gate(tmp_path, 2 * GIB, tool="mcp__t3-code__delegate_task"))
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "free RAM 2.0 GiB < 3.0" in hso["permissionDecisionReason"]
    assert "wait for a child to finish or free memory" in hso["permissionDecisionReason"].lower()


def test_gate_threshold_from_config(tmp_path):
    assert gate(tmp_path, 4 * GIB, cfg={"memory_gate": {"min_available_gib": 5}}) != ""
    assert gate(tmp_path, 4 * GIB, cfg={"memory_gate": {"min_available_gib": 3.5}}) == ""


def test_gate_swap_weight_off_by_default_and_optional(tmp_path):
    assert gate(tmp_path, 2 * GIB, swap_kb=10 * GIB) != ""
    assert gate(tmp_path, 2 * GIB, swap_kb=10 * GIB, cfg={"memory_gate": {"swap_weight": 0.25}}) == ""


def test_gate_disabled(tmp_path):
    assert gate(tmp_path, 1 * GIB, cfg={"memory_gate": {"enabled": False}}) == ""


def test_gate_skips_remote_calls(tmp_path):
    assert gate(tmp_path, 1 * GIB, tool="mcp__t3-code__t3_thread_launch",
                tool_input={"environmentId": "env-remote"}) == ""
    assert gate(tmp_path, 1 * GIB, tool="Agent", tool_input={"isolation": "remote"}) == ""
    assert gate(tmp_path, 1 * GIB, tool="Agent", tool_input={"isolation": "worktree"}) != ""


def test_gate_fails_open(tmp_path):
    cdir = tmp_path / "cfg"
    cdir.mkdir()
    (cdir / "config.json").write_text("{broken")
    p, _ = run(MEMORY_GATE, "not json",
               {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(tmp_path / "missing")})
    assert p.returncode == 0 and p.stdout == ""
    bad = tmp_path / "meminfo"
    bad.write_text("MemTotal: 1 kB\n")  # no MemAvailable
    p, _ = run(MEMORY_GATE, "{}", {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(bad)})
    assert p.returncode == 0 and p.stdout == ""


# ---------- settings merge ----------

def merge(settings_path, *flags):
    return subprocess.run([sys.executable, "-I", str(MERGE), str(settings_path), str(REPO / "hooks" / "hooks.json"),
                           str(REPO), *flags], capture_output=True, text=True, check=True)


def test_merge_keeps_existing_and_is_idempotent(tmp_path):
    s = tmp_path / "settings.json"
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
    s.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [other]}}))
    merge(s)
    merge(s)
    d = json.loads(s.read_text())
    assert d["model"] == "x"
    pre = d["hooks"]["PreToolUse"]
    assert pre[0] == other and len(pre) == 2
    assert str(REPO / "hooks" / "memory_gate.py") in pre[1]["hooks"][0]["command"]
    assert pre[1]["matcher"] == "Agent|mcp__t3-code__delegate_task|mcp__t3-code__t3_thread_launch"
    assert d["hooks"]["SessionStart"][0]["matcher"] == "startup|resume|compact"
    assert "${CLAUDE_PLUGIN_ROOT}" not in s.read_text()
    merge(s, "--uninstall")
    d = json.loads(s.read_text())
    assert d == {"model": "x", "hooks": {"PreToolUse": [other]}}


def test_merge_dry_run_writes_nothing(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text('{"model": "x"}')
    out = merge(s, "--dry-run").stdout
    assert "role_card.py" in out and s.read_text() == '{"model": "x"}'

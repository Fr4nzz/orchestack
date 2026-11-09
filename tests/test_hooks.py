"""Tests for hooks/role_card.py, hooks/memory_gate.py and scripts/merge_settings.py.

Run: python3 -m pytest tests   (or: uvx pytest tests)
"""
import importlib.util
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
    p, _ = run(MEMORY_GATE, json.dumps(event), {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(mi),
                                                "ORCHESTACK_GATE_RECHECK_S": "0"})
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
    assert "do not sleep-poll" in hso["permissionDecisionReason"].lower()


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
    assert pre[1]["matcher"] == "^(Agent|Task|mcp__t3-code__delegate_task|mcp__t3-code__t3_thread_launch)$"
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


def test_merge_uninstall_keeps_sibling_hooks_in_a_shared_group(tmp_path):
    s = tmp_path / "settings.json"
    merge(s)
    d = json.loads(s.read_text())
    sibling = {"type": "command", "command": "echo mine"}
    d["hooks"]["SessionStart"][0]["hooks"].append(sibling)
    s.write_text(json.dumps(d))
    merge(s, "--uninstall")
    d = json.loads(s.read_text())
    assert d["hooks"]["SessionStart"] == [{"matcher": "startup|resume|compact", "hooks": [sibling]}]
    assert "PreToolUse" not in d["hooks"]


def test_merge_writes_through_symlink_and_keeps_mode(tmp_path):
    real = tmp_path / "real.json"
    real.write_text('{"model": "x"}')
    real.chmod(0o600)
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    merge(link)
    assert link.is_symlink() and "role_card.py" in real.read_text()
    assert (real.stat().st_mode & 0o777) == 0o600


def test_gate_allows_on_invalid_config(tmp_path):
    cdir = tmp_path / "cfg"
    cdir.mkdir()
    (cdir / "config.json").write_text("{not json")
    mi = tmp_path / "meminfo"
    mi.write_text(MEMINFO.format(avail=1 * GIB, swap=0))
    event = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {}}
    p, _ = run(MEMORY_GATE, json.dumps(event), {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(mi)})
    assert p.returncode == 0 and p.stdout == ""


def test_precommit_uses_external_pattern_file(tmp_path):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel in (".githooks/pre-commit", "scripts/scrub_check.sh", "scripts/lint.sh"):
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((REPO / rel).read_bytes())
        dst.chmod(0o755)
    (repo / "note.md").write_text("mentions secretproject here\n")
    pats = tmp_path / "patterns.txt"
    pats.write_text("secretproject\n")
    subprocess.run(["git", "-C", str(repo), "add", "note.md", "scripts", ".githooks"], check=True)
    env = {**os.environ, "ORCHESTACK_SCRUB_PATTERNS": str(pats)}
    p = subprocess.run([str(repo / ".githooks/pre-commit")], cwd=repo, env=env, capture_output=True, text=True)
    assert p.returncode != 0 and "secretproject" in p.stdout


def test_install_refuses_bad_retire_names(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    for bad in ("a,,../x", "orchestrator", "a/b"):
        p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run", f"--retire={bad}"],
                           env=env, capture_output=True, text=True)
        assert p.returncode == 2 and "refusing" in p.stderr, bad
    p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run", "--retire=a,,b"],
                       env=env, capture_output=True, text=True)
    assert p.returncode == 0 and "mv --" not in p.stdout


def test_gate_denial_names_the_largest_processes(tmp_path):
    reason = json.loads(gate(tmp_path, 2 * GIB))["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Largest processes:" in reason and "GiB" in reason
    assert "(pid " in reason or " processes)" in reason


def write_meminfo(path, avail_kb):
    """Atomic, so a read during the re-check never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(MEMINFO.format(avail=avail_kb, swap=0))
    os.replace(tmp, path)


def test_gate_allows_when_memory_frees_during_the_recheck(tmp_path):
    import threading
    cdir = tmp_path / "cfg"
    write_cfg(cdir)
    mi = tmp_path / "meminfo"
    write_meminfo(mi, 2 * GIB)
    event = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {}})
    threading.Timer(0.4, lambda: write_meminfo(mi, 8 * GIB)).start()
    p, _ = run(MEMORY_GATE, event, {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(mi),
                                    "ORCHESTACK_GATE_RECHECK_S": "2"})
    assert p.returncode == 0 and p.stdout == ""


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gate_default_denial_stays_under_the_hook_timeout(tmp_path):
    cdir = tmp_path / "cfg"
    write_cfg(cdir)
    mi = tmp_path / "meminfo"
    write_meminfo(mi, 2 * GIB)
    event = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {}})
    p, dt = run(MEMORY_GATE, event, {"ORCHESTACK_CONFIG_DIR": str(cdir), "ORCHESTACK_MEMINFO": str(mi)})
    assert "deny" in p.stdout and dt < 4.5, dt


def test_gate_caps_an_oversized_recheck_to_the_deadline(tmp_path, monkeypatch):
    mg = load(MEMORY_GATE, "memory_gate_cap")
    cdir = tmp_path / "cfg"
    write_cfg(cdir, cfg={"memory_gate": {"recheck_s": 8}})
    mi = tmp_path / "meminfo"
    write_meminfo(mi, 2 * GIB)
    slept = []
    monkeypatch.setenv("ORCHESTACK_CONFIG_DIR", str(cdir))
    monkeypatch.setenv("ORCHESTACK_MEMINFO", str(mi))
    monkeypatch.delenv("ORCHESTACK_GATE_RECHECK_S", raising=False)
    monkeypatch.setattr(mg.time, "sleep", slept.append)
    monkeypatch.setattr(mg, "largest_processes", lambda *a, **k: "")
    monkeypatch.setattr(mg.sys, "stdin", __import__("io").StringIO("{}"))
    out = __import__("io").StringIO()
    monkeypatch.setattr(mg.sys, "stdout", out)
    mg.main()
    assert "deny" in out.getvalue()
    assert len(slept) == 2 and sum(slept) <= 3.0, slept


def test_gate_process_scan_stops_at_the_deadline(monkeypatch):
    mg = load(MEMORY_GATE, "memory_gate_scan")
    monkeypatch.setattr(mg.os, "listdir", lambda _: [str(n) for n in range(1, 2001)])

    def slow_rss(pid):
        time.sleep(0.01)
        return 1024

    monkeypatch.setattr(mg, "process_rss_kb", slow_rss)
    monkeypatch.setattr(mg, "process_name", lambda pid: "node")
    t0 = time.monotonic()
    top = mg.largest_processes(3, t0 + 0.2)
    assert time.monotonic() - t0 < 0.5 and top.startswith("node ")


def test_gate_names_a_process_by_its_command_not_its_thread_name():
    mg = load(MEMORY_GATE, "memory_gate_name")
    child = subprocess.Popen([sys.executable, "-c", "import ctypes, time\n"
                              "ctypes.CDLL(None).prctl(15, b'MainThread', 0, 0, 0)\n"
                              "print('ok', flush=True)\ntime.sleep(30)"], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ok"
        assert Path(f"/proc/{child.pid}/comm").read_text().strip() == "MainThread"
        assert mg.process_name(str(child.pid)).startswith("python")
    finally:
        child.kill()
        child.wait()

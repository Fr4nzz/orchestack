"""Tests for watcher/watch.py: state diffing, each check, rate limit, quiet hours, dry run, read-only database.

Run: python3 -m pytest tests   (or: uvx pytest tests)
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WATCH = REPO / "watcher" / "watch.py"
T0 = "2026-01-07T12:00:00.000Z"  # a Wednesday


def at(minutes):
    """T0 plus minutes, as the watcher's ISO format."""
    from datetime import datetime, timedelta, timezone
    t = datetime(2026, 1, 7, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Rig:
    """A config dir, state dir, fake send command and optional fake T3 database."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.cfg_dir = tmp / "cfg"
        self.state_dir = tmp / "state"
        self.sent = tmp / "sent.jsonl"
        self.cfg_dir.mkdir()
        fake = tmp / "fake_send.py"
        fake.write_text(
            "import json, sys\n"
            f"open({str(self.sent)!r}, 'a').write(json.dumps({{'thread': sys.argv[1], 'text': sys.stdin.read()}}) + '\\n')\n"
        )
        self.base = {"orchestrator": "orch-1", "wake": {"command": [sys.executable, str(fake), "{thread}"]}}

    def config(self, **sections):
        cfg = dict(self.base)
        cfg.update(sections)
        (self.cfg_dir / "config.json").write_text(json.dumps({"watcher": cfg}))

    def run(self, now=T0, *flags):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "TZ": "UTC",
               "ORCHESTACK_CONFIG_DIR": str(self.cfg_dir), "ORCHESTACK_STATE_DIR": str(self.state_dir)}
        p = subprocess.run([sys.executable, "-I", str(WATCH), "--now", now, *flags], capture_output=True,
                           text=True, env=env, timeout=30)
        assert p.returncode == 0, p.stderr
        return p

    def wakes(self):
        if not self.sent.exists():
            return []
        out = [json.loads(line) for line in self.sent.read_text().splitlines()]
        self.sent.unlink()
        return out

    def state(self):
        return json.loads((self.state_dir / "watch.json").read_text())

    def script(self, name, body):
        path = self.tmp / name
        path.write_text(body)
        return [sys.executable, str(path)]


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE projection_projects (project_id TEXT PRIMARY KEY, deleted_at TEXT);
        CREATE TABLE orchestration_v2_projection_threads (thread_id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
            updated_at TEXT, archived_at TEXT, deleted_at TEXT, payload_json TEXT);
        CREATE INDEX t_proj ON orchestration_v2_projection_threads(project_id, updated_at);
        CREATE TABLE orchestration_v2_projection_runs (run_id TEXT PRIMARY KEY, thread_id TEXT, ordinal INTEGER,
            status TEXT, requested_at TEXT, completed_at TEXT, payload_json TEXT);
        CREATE UNIQUE INDEX r_ord ON orchestration_v2_projection_runs(thread_id, ordinal);
        CREATE TABLE orchestration_v2_projection_runtime_requests (runtime_request_id TEXT PRIMARY KEY,
            thread_id TEXT, kind TEXT, status TEXT, created_at TEXT);
        CREATE TABLE orchestration_v2_projection_messages (message_id TEXT PRIMARY KEY, thread_id TEXT, role TEXT,
            streaming INTEGER, created_at TEXT, updated_at TEXT, payload_json TEXT);
        INSERT INTO projection_projects VALUES ('p1', NULL);
    """)
    conn.commit()
    return conn


def add_thread(conn, tid, title, parent=None, updated=T0):
    lineage = {"parentThreadId": parent} if parent else {}
    conn.execute("INSERT OR REPLACE INTO orchestration_v2_projection_threads VALUES (?, 'p1', ?, ?, NULL, NULL, ?)",
                 (tid, title, updated, json.dumps({"lineage": lineage})))
    conn.commit()


def add_run(conn, tid, ordinal, status, requested, completed=None, started=None):
    conn.execute("INSERT INTO orchestration_v2_projection_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (f"run:{tid}:{ordinal}", tid, ordinal, status, requested, completed,
                  json.dumps({"startedAt": started or requested})))
    conn.commit()


def add_message(conn, mid, tid, role, text, created):
    conn.execute("INSERT INTO orchestration_v2_projection_messages VALUES (?, ?, ?, 0, ?, ?, ?)",
                 (mid, tid, role, created, created, json.dumps({"text": text})))
    conn.commit()


# ---------- jobs ----------

def job_rig(tmp_path, listing):
    rig = Rig(tmp_path)
    proj = tmp_path / "proj"
    jobs = proj / ".work" / "orchestrator" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "train.json").write_text(json.dumps({"thread": "child-7", "jobs": ["101", "102"],
                                                 "next_step": "read metrics.json"}))
    listing_file = tmp_path / "listing.txt"
    listing_file.write_text(listing)
    cmd = rig.script("bjobs.py", f"print(open({str(listing_file)!r}).read(), end='')\n")
    rig.config(projects=[str(proj)], jobs={"command": cmd})
    return rig, listing_file


def test_jobs_wake_owner_once_when_all_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 PEND\n")
    rig.run(T0)
    assert rig.wakes() == []
    listing.write_text("101 DONE\n102 RUN\n")
    rig.run(at(15))
    assert rig.wakes() == []
    listing.write_text("101 DONE\n102 DONE\n")
    rig.run(at(30))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "child-7"
    assert w[0]["text"].startswith("watcher: jobs: train.json: all 2 jobs ended (DONE 2).")
    assert "Next step: read metrics.json" in w[0]["text"]
    rig.run(at(45))
    rig.run(at(200))
    assert rig.wakes() == []  # same state never wakes twice


def test_jobs_failure_is_urgent_and_forgotten_jobs_count_as_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    rig.run(T0)
    listing.write_text("101 EXIT\n102 RUN\n")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and "job 101 failed (1 still running)" in w[0]["text"]
    listing.write_text("102 RUN\n")  # 101 dropped out of the listing
    rig.run(at(30))
    assert rig.wakes() == []
    listing.write_text("")
    rig.run(at(45))  # all ended, but within an hour of the failure wake: held by the rate limit
    assert rig.wakes() == []
    rig.run(at(80))
    w = rig.wakes()
    assert len(w) == 1 and "all 2 jobs ended (GONE 2)" in w[0]["text"] and "check the job's output" in w[0]["text"]


def test_jobs_cluster_unreachable_decides_nothing(tmp_path):
    rig = Rig(tmp_path)
    proj = tmp_path / "proj"
    (proj / ".work/orchestrator/jobs").mkdir(parents=True)
    (proj / ".work/orchestrator/jobs/5.json").write_text(json.dumps({"thread": "c"}))
    rig.config(projects=[str(proj)], jobs={"command": rig.script("down.py", "import sys; sys.exit(255)\n")})
    p = rig.run(T0, "--verbose")
    assert rig.wakes() == [] and "cluster command failed" in p.stdout


def test_jobs_skip_cluster_call_when_no_open_files(tmp_path):
    rig = Rig(tmp_path)
    marker = tmp_path / "called"
    rig.config(projects=[str(tmp_path / "proj")],
               jobs={"command": rig.script("b.py", f"open({str(marker)!r}, 'w')\n")})
    rig.run(T0)
    assert not marker.exists()


# ---------- children ----------

def test_children_failed_wakes_parent_after_baseline(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead: app")
    add_thread(conn, "old-child", "old work", parent="lead-1")
    add_run(conn, "lead-1", 1, "completed", at(-300), at(-290))
    add_run(conn, "old-child", 1, "failed", at(-200), at(-199))
    rig.config(t3_db=str(db), children={})
    rig.run(T0)
    assert rig.wakes() == []  # baseline: old failures recorded, not woken
    add_thread(conn, "child-2", "Fix login bug", parent="lead-1", updated=at(10))
    add_run(conn, "child-2", 1, "failed", at(5), at(10))
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "lead-1"
    assert w[0]["text"].startswith('watcher: children: failed: "Fix login bug" (child-2) run failed.')
    rig.run(at(30))
    assert rig.wakes() == []


def test_children_skip_when_parent_already_ran_after_failure(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead: app")
    rig.config(t3_db=str(db), children={})
    rig.run(T0)
    add_thread(conn, "child-3", "Docs", parent="lead-1", updated=at(10))
    add_run(conn, "child-3", 1, "failed", at(5), at(10))
    add_run(conn, "lead-1", 1, "completed", at(11), at(12))  # the parent already woke on the completion notice
    rig.run(at(15))
    assert rig.wakes() == []


def test_children_approval_and_stuck(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead: app")
    add_thread(conn, "c-a", "Needs approval", parent="lead-1")
    add_thread(conn, "c-s", "Long run", parent="lead-1")
    rig.config(t3_db=str(db), children={"stuck_hours": 3, "approval_minutes": 10}, rate_limit_minutes=0)
    rig.run(T0)
    conn.execute("INSERT INTO orchestration_v2_projection_runtime_requests VALUES ('rq1', 'c-a', 'command', "
                 "'pending', ?)", (at(5),))
    add_run(conn, "c-s", 1, "running", at(-170))
    rig.run(at(10))
    assert rig.wakes() == []  # approval only 5 min old, run 3 h exactly not yet over
    rig.run(at(20))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "lead-1"
    assert "waiting on an approval for 15 min" in w[0]["text"] and "running for 3.2 h" in w[0]["text"]
    assert w[0]["text"].count("watcher: children: failed:") == 2  # merged into one wake


def test_db_is_opened_read_only(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead")
    conn.close()
    before = db.stat().st_mtime_ns
    rig.config(t3_db=str(db), children={}, decision={"threads": ["lead-1"]})
    rig.run(T0)
    rig.run(at(15))
    assert db.stat().st_mtime_ns == before and not (tmp_path / "state.sqlite-journal").exists()


# ---------- decision ----------

def test_decision_unanswered_wakes_once(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "orch-1", "Orchestrator: shop")
    rig.config(t3_db=str(db), decision={"minutes": 30})
    rig.run(T0)
    add_message(conn, "m1", "orch-1", "user", "Delegated task x [completed]:\ndecision: ship A or B?", at(5))
    add_message(conn, "m2", "orch-1", "user", "done: tests pass", at(6))
    rig.run(at(20))
    assert rig.wakes() == []  # only 15 min old
    rig.run(at(40))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "orch-1"
    assert w[0]["text"].startswith("watcher: decision: unanswered for 35 min: decision: ship A or B?")
    rig.run(at(120))
    assert rig.wakes() == []


def test_decision_answered_or_busy_is_quiet(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "orch-1", "Orchestrator: shop")
    rig.config(t3_db=str(db), decision={})
    rig.run(T0)
    add_message(conn, "m1", "orch-1", "user", "decision: A?", at(1))
    add_message(conn, "a1", "orch-1", "assistant", "A.", at(2))
    add_message(conn, "m2", "orch-1", "user", "decision: B?", at(3))
    add_run(conn, "orch-1", 1, "running", at(3))
    rig.run(at(60))
    assert rig.wakes() == []  # m1 answered, m2 queued behind a running turn


# ---------- disk ----------

def test_disk_low_wakes_immediately_once_and_rearms(tmp_path):
    rig = Rig(tmp_path)
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9}, rate_limit_minutes=600)
    rig.run(T0)
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "orch-1" and w[0]["text"].startswith("watcher: disk: ")
    rig.run(at(15))
    assert rig.wakes() == []
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 0.0}, rate_limit_minutes=600)
    rig.run(at(30))
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9}, rate_limit_minutes=600)
    rig.run(at(45))
    assert len(rig.wakes()) == 1  # urgent: not held by the rate limit


# ---------- usage ----------

def usage_rig(tmp_path, pct, **extra):
    rig = Rig(tmp_path)
    meter = tmp_path / "pct.txt"
    meter.write_text(str(pct))
    cmd = rig.script("meter.py", f"print('five_hour used 3 %')\nprint('seven_day used', open({str(meter)!r}).read(), '%')\n")
    usage = {"command": cmd, "weekly_pattern": r"seven_day\s+used\s+([0-9.]+)", "ideas_file": "/x/IDEAS.md"}
    usage.update(extra)
    rig.config(usage=usage)
    return rig, meter


def test_usage_reset_sends_backlog_prompt_once(tmp_path):
    rig, meter = usage_rig(tmp_path, 80)
    rig.run(T0)
    assert rig.wakes() == []
    meter.write_text("1")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["text"].startswith("watcher: usage: weekly usage reset (1 % used now, was 80 %).")
    assert "/x/IDEAS.md" in w[0]["text"] and "proud" in w[0]["text"] and "wait for the user" in w[0]["text"]
    rig.run(at(30))
    rig.run(at(300))
    assert rig.wakes() == []


def test_usage_far_behind_pace_rearms_daily(tmp_path):
    # reset Monday 00:00 UTC, now Wednesday 12:00: 2.5 days, pace 15 %/day -> 37.5 % expected
    rig, meter = usage_rig(tmp_path, 10, weekly_reset="Mon 00:00", pace_per_day=15, behind_by=20)
    rig.config(usage=json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]["usage"], timezone="UTC")
    rig.run(T0)
    w = rig.wakes()
    assert len(w) == 1 and "far behind pace: 10 % of the week used, pace says 38 %" in w[0]["text"]
    rig.run(at(60))
    assert rig.wakes() == []
    rig.run(at(24 * 60 + 5))
    assert len(rig.wakes()) == 1
    meter.write_text("60")
    rig.run(at(2 * 24 * 60 + 10))
    assert rig.wakes() == []


# ---------- delivery rules ----------

def test_rate_limit_holds_and_merges(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "orch-1", "Orchestrator")
    rig.config(t3_db=str(db), decision={"minutes": 1})
    rig.run(T0)
    add_message(conn, "m1", "orch-1", "user", "decision: one?", at(1))
    rig.run(at(5))
    assert len(rig.wakes()) == 1
    add_message(conn, "m2", "orch-1", "user", "decision: two?", at(6))
    add_message(conn, "m3", "orch-1", "user", "decision: three?", at(7))
    p = rig.run(at(20))
    assert rig.wakes() == [] and "held for orch-1 (rate limit" in p.stdout
    rig.run(at(70))
    w = rig.wakes()
    assert len(w) == 1 and "two?" in w[0]["text"] and "three?" in w[0]["text"]
    assert rig.state()["outbox"] == []


def test_quiet_hours_hold_non_urgent(tmp_path):
    rig, meter = usage_rig(tmp_path, 80)
    cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
    rig.config(usage=cfg["usage"], quiet_hours={"start": "11:00", "end": "13:00"}, timezone="UTC")
    rig.run(T0)
    meter.write_text("2")
    rig.run(at(15))
    assert rig.wakes() == []
    rig.run(at(65))  # 13:05
    assert len(rig.wakes()) == 1


def test_dry_run_changes_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    p = rig.run(T0, "--dry-run")
    assert "would wake orch-1" in p.stdout and "watcher: disk:" in p.stdout and "dry run" in p.stdout
    assert rig.wakes() == [] and not rig.state_dir.exists()


def test_failed_delivery_stays_in_outbox(tmp_path):
    rig = Rig(tmp_path)
    rig.base["wake"] = {"command": rig.script("fail.py", "import sys; sys.exit(3)\n")}
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    p = rig.run(T0)
    assert "FAILED to wake orch-1" in p.stdout and len(rig.state()["outbox"]) == 1


def test_hosts_on_online_runs_copy_once_without_wake(tmp_path):
    rig = Rig(tmp_path)
    flag = tmp_path / "up"
    copies = tmp_path / "copies"
    probe = rig.script("probe.py", f"import os, sys; sys.exit(0 if os.path.exists({str(flag)!r}) else 1)\n")
    copy = rig.script("copy.py", f"open({str(copies)!r}, 'a').write('x')\n")
    rig.config(hosts=[{"name": "box", "probe": probe, "on_online": copy}])
    rig.run(T0)
    flag.write_text("")
    rig.run(at(15))
    rig.run(at(30))
    assert copies.read_text() == "x" and rig.wakes() == []


def test_disabled_or_missing_config_does_nothing(tmp_path):
    rig = Rig(tmp_path)
    p = rig.run(T0)
    assert "disabled" in p.stdout and not rig.state_dir.exists()


def test_install_watcher_dry_run(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run", "--watcher"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "orchestack-watch.timer" in p.stdout and "would run: systemctl --user enable --now" in p.stdout
    assert not (tmp_path / ".config" / "systemd").exists()
    p = subprocess.run(["bash", str(REPO / "scripts" / "install.sh"), "--dry-run"], env=env, capture_output=True,
                       text=True)
    assert "orchestack-watch" not in p.stdout


def test_webhook_transport_posts_text(tmp_path):
    import http.server
    import threading
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rig = Rig(tmp_path)
        url = f"http://127.0.0.1:{srv.server_port}/api/hooks/task-1/secret-token"
        rig.base["wake"] = {"webhooks": {"orch-1": url}}
        rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
        p = rig.run(T0)
    finally:
        srv.shutdown()
    assert len(got) == 1 and got[0]["text"].startswith("watcher: disk: ")
    assert "secret-token" not in p.stdout + p.stderr  # the URL holds a token: never logged


def test_no_baseline_dry_run_shows_existing_events(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead")
    add_thread(conn, "c1", "Broken child", parent="lead-1")
    add_run(conn, "c1", 1, "failed", at(-20), at(-10))
    rig.config(t3_db=str(db), children={})
    assert "would wake" not in rig.run(T0, "--dry-run").stdout
    p = rig.run(T0, "--dry-run", "--no-baseline")
    assert "would wake lead-1" in p.stdout and '"Broken child" (c1) run failed' in p.stdout
    assert not rig.state_dir.exists()

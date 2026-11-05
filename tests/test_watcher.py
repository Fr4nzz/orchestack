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


def parse_ts(text):
    from datetime import datetime
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


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

    def run(self, now=T0, *flags, extra_env=None, check=True):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.tmp), "TZ": "UTC",
               "ORCHESTACK_CONFIG_DIR": str(self.cfg_dir), "ORCHESTACK_STATE_DIR": str(self.state_dir)}
        env.update(extra_env or {})
        args = list(flags) if now is None else ["--now", now, *flags]
        p = subprocess.run([sys.executable, "-I", str(WATCH), *args], capture_output=True,
                           text=True, env=env, timeout=30)
        if check:
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


INDEXES = """
    CREATE INDEX r_status ON orchestration_v2_projection_runs(status, thread_id)
        WHERE status IN ('queued', 'preparing', 'starting', 'running', 'waiting');
    CREATE INDEX rq_pending ON orchestration_v2_projection_runtime_requests(thread_id) WHERE status = 'pending';
    CREATE INDEX m_user ON orchestration_v2_projection_messages(thread_id, updated_at DESC, message_id DESC)
        WHERE role = 'user';
    CREATE INDEX m_created ON orchestration_v2_projection_messages(thread_id, created_at, message_id);
"""


def make_db(path, indexes=True):
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
    if indexes:
        conn.executescript(INDEXES)
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


def test_jobs_wake_tells_a_child_to_send_its_result_to_the_parent(tmp_path):
    # Without a database the wake still says it, without an id.
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    rig.run(T0)
    listing.write_text("101 DONE\n102 DONE\n")
    rig.run(at(30))
    text = rig.wakes()[0]["text"]
    assert 'If you are a child, send your done:/failed:/handoff:/decision: to your parent with t3_thread_send' in text
    assert '(mode "queue")' in text
    # The lineage parent from the database, or a top-level owner (no sentence).
    for owner, parent, expect in (("child-7", "lead-1", "to your parent lead-1 with t3_thread_send"),
                                  ("top-1", None, None)):
        sub = tmp_path / owner
        sub.mkdir()
        rig, listing = job_rig(sub, "101 RUN\n102 RUN\n")
        conn = make_db(sub / "state.sqlite")
        add_thread(conn, "lead-1", "Lead")
        add_thread(conn, owner, "Owner", parent=parent)
        jobs = sub / "proj" / ".work" / "orchestrator" / "jobs"
        (jobs / "train.json").write_text(json.dumps({"thread": owner, "jobs": ["101", "102"]}))
        cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
        rig.config(**{**cfg, "t3_db": str(sub / "state.sqlite")})
        rig.run(T0)
        listing.write_text("101 DONE\n102 DONE\n")
        rig.run(at(30))
        text = rig.wakes()[0]["text"]
        assert (expect in text) if expect else ("t3_thread_send" not in text)
    # The job file's "parent" key wins.
    sub = tmp_path / "keyed"
    sub.mkdir()
    rig, listing = job_rig(sub, "101 RUN\n")
    jobs = sub / "proj" / ".work" / "orchestrator" / "jobs"
    (jobs / "train.json").write_text(json.dumps({"thread": "child-7", "parent": "lead-9", "jobs": ["101"]}))
    rig.run(T0)
    listing.write_text("101 EXIT\n")
    rig.run(at(30))
    assert "to your parent lead-9 with t3_thread_send" in rig.wakes()[0]["text"]


def test_jobs_failure_is_urgent_and_forgotten_jobs_count_as_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    rig.run(T0)
    listing.write_text("101 EXIT\n102 RUN\n")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and "job 101 failed (1 still running)" in w[0]["text"]
    listing.write_text("102 RUN\n")  # 101 dropped out of the listing: its EXIT is remembered
    rig.run(at(30))
    assert rig.wakes() == []
    listing.write_text("102 DONE\n999 RUN\n")
    rig.run(at(45))  # all ended, but within an hour of the failure wake: held by the rate limit
    assert rig.wakes() == []
    rig.run(at(80))
    w = rig.wakes()
    assert len(w) == 1 and "all 2 jobs ended (DONE 1, FAILED 1)" in w[0]["text"]


def test_jobs_gone_when_the_cluster_answers_without_them(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    rig.run(T0)
    listing.write_text("")  # rc 0 but no output: decide nothing
    p = rig.run(at(15), "--verbose")
    assert rig.wakes() == [] and "listed no jobs at all" in p.stdout
    listing.write_text("555 ZOMBI\n102 DONE\n")  # 101 dropped out, 102 ended: both ended
    rig.run(at(30))
    w = rig.wakes()
    assert len(w) == 1 and "(DONE 1, GONE 1)" in w[0]["text"] and "check the job's output" in w[0]["text"]


def test_jobs_array_with_a_running_element_has_not_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    rig.run(T0)
    listing.write_text("101[1] DONE\n101[2] RUN\n102 DONE\n")
    rig.run(at(15))
    assert rig.wakes() == []
    listing.write_text("101[1] DONE\n101[2] DONE\n102 DONE\n")
    rig.run(at(30))
    w = rig.wakes()
    assert len(w) == 1 and "(DONE 2)" in w[0]["text"]


def test_jobs_gone_job_is_looked_up_once(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    calls = tmp_path / "calls"
    cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
    cfg["jobs"]["lookup_command"] = rig.script("l.py", f"import sys; open({str(calls)!r}, 'a').write('x')\n") + ["{jobs}"]
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    rig.run(T0)
    listing.write_text("102 RUN\n")
    rig.run(at(15))
    rig.run(at(30))
    assert calls.read_text() == "x" and rig.wakes() == []
    listing.write_text("102 DONE\n")
    rig.run(at(45))
    w = rig.wakes()
    assert len(w) == 1 and "(DONE 1, GONE 1)" in w[0]["text"]


def test_jobs_none_listed_after_an_outage_wake_once_with_the_lookup_state(tmp_path):
    """Jobs seen running, then the cluster was unreachable while they ended and dropped out of bjobs -a."""
    rig, listing = job_rig(tmp_path, "101 RUN\n102 PEND\n")
    calls = tmp_path / "lookup_calls"
    lookup = rig.script("lookup.py", "import sys\n"
                        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                        "print('101 DONE')\nprint('102 EXIT')\nprint('999 DONE')\n")
    cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
    cfg["jobs"]["lookup_command"] = lookup + ["{jobs}"]
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    rig.run(T0)
    assert not calls.exists()
    down = cfg["jobs"]["command"]
    cfg["jobs"]["command"] = rig.script("down.py", "import sys; sys.exit(255)\n")
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    rig.run(at(15))
    rig.run(at(30))
    assert rig.wakes() == []
    cfg["jobs"]["command"] = down
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    listing.write_text("555 ZOMBI\n")  # back: other jobs listed, none of this file's
    rig.run(at(45))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "child-7"
    assert "all 2 jobs ended (DONE 1, FAILED 1)" in w[0]["text"]
    assert calls.read_text() == "101 102\n"
    rig.run(at(120))
    assert rig.wakes() == [] and calls.read_text() == "101 102\n"  # decided once, never looked up again


def test_jobs_lookup_failure_still_counts_unlisted_jobs_as_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
    cfg["jobs"]["lookup_command"] = " ".join(rig.script("l.py", "import sys; print('101 DONE'); sys.exit(3)\n")) \
        + " {jobs}"
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    rig.run(T0)
    listing.write_text("No unfinished job found\n")
    p = rig.run(at(15), "--verbose")
    w = rig.wakes()
    assert len(w) == 1 and "(DONE 1, GONE 1)" in w[0]["text"] and "lookup failed (3)" in p.stdout


def test_jobs_never_listed_wait_for_the_grace_period(tmp_path):
    rig, listing = job_rig(tmp_path, "555 RUN\n")
    os.utime(tmp_path / "proj/.work/orchestrator/jobs/train.json", (parse_ts(at(-10)),) * 2)
    write_state(rig, {"initialised": ["jobs"], "jobs": {}})
    rig.run(T0)
    assert rig.wakes() == []  # written 10 min ago, not yet submitted
    rig.run(at(25))
    w = rig.wakes()
    assert len(w) == 1 and "(GONE 2)" in w[0]["text"]


def test_jobs_custom_states_share_one_terminal_form(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUNNING\n102 PENDING\n")
    cfg = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]
    cfg["jobs"].update(done_states=["completed"], failed_states=["FAILED", "TIMEOUT"])
    rig.config(projects=cfg["projects"], jobs=cfg["jobs"])
    rig.run(T0)
    listing.write_text("101 COMPLETED\n102 COMPLETED\n")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and "all 2 jobs ended (DONE 2)" in w[0]["text"]


def test_jobs_old_files_ignored_and_baseline_records_ended(tmp_path):
    rig, listing = job_rig(tmp_path, "101 DONE\n102 DONE\n")
    jobs = tmp_path / "proj" / ".work" / "orchestrator" / "jobs"
    old = jobs / "old.json"
    old.write_text(json.dumps({"thread": "child-9", "jobs": ["101"]}))
    os.utime(old, (1, 1))
    p = rig.run(T0, "--verbose")  # first run (state lost): ended files recorded, not woken
    assert rig.wakes() == [] and "ignored old.json" in p.stdout and "baseline" in p.stdout
    rig.run(at(15))
    assert rig.wakes() == []
    (jobs / "new.json").write_text(json.dumps({"thread": "child-8", "jobs": ["102"]}))
    rig.run(at(30))
    w = rig.wakes()
    assert [x["thread"] for x in w] == ["child-8"]


def test_jobs_invalid_owner_thread_is_never_put_in_argv(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    (tmp_path / "proj/.work/orchestrator/jobs/train.json").write_text(json.dumps({"thread": "--help",
                                                                               "jobs": ["101", "102"]}))
    rig.run(T0)
    listing.write_text("101 DONE\n102 DONE\n")
    p = rig.run(at(15), "--verbose")
    assert rig.wakes() == [] and "no valid thread" in p.stdout


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
    assert 'watcher: children: approval: "Needs approval" (c-a) waiting on an approval for 15 min.' in w[0]["text"]
    assert 'watcher: children: stuck: "Long run" (c-s) running for 3.2 h' in w[0]["text"]  # merged into one wake


def test_children_active_events_survive_the_prune(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead: app")
    add_thread(conn, "c-a", "Needs approval", parent="lead-1")
    rig.config(t3_db=str(db), children={}, rate_limit_minutes=0)
    rig.run(T0)
    conn.execute("INSERT INTO orchestration_v2_projection_runtime_requests VALUES ('rq1', 'c-a', 'command', "
                 "'pending', ?)", (at(1),))
    conn.commit()
    rig.run(at(15))
    assert len(rig.wakes()) == 1
    rig.run(at(15 * 24 * 60))  # still pending 15 days later: not woken again
    assert rig.wakes() == []


def test_children_failure_waits_while_parent_is_busy(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead: app")
    add_run(conn, "lead-1", 1, "running", at(-5))
    rig.config(t3_db=str(db), children={})
    rig.run(T0)
    add_thread(conn, "c1", "Broken", parent="lead-1", updated=at(10))
    add_run(conn, "c1", 1, "failed", at(5), at(10))
    rig.run(at(15))
    assert rig.wakes() == []
    conn.execute("UPDATE orchestration_v2_projection_runs SET status = 'completed' WHERE thread_id = 'lead-1'")
    conn.commit()
    rig.run(at(30))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["text"].startswith('watcher: children: failed: "Broken" (c1) run failed.')


def test_missing_index_skips_the_check(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db, indexes=False)
    add_thread(conn, "lead-1", "Lead")
    rig.config(t3_db=str(db), children={}, decision={})
    p = rig.run(T0, "--verbose")
    assert "children: error: query plan would scan orchestration_v2_projection_runs" in p.stdout
    assert "decision: error: query plan would scan orchestration_v2_projection_messages" in p.stdout
    assert "children" not in rig.state()["initialised"]


def test_db_deadline_aborts_slow_queries(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    conn.executemany("INSERT INTO orchestration_v2_projection_threads VALUES (?, 'p1', 'x', ?, NULL, NULL, '{}')",
                     [(f"t{i}", at(-1)) for i in range(3000)])
    conn.commit()
    rig.config(t3_db=str(db), children={}, db_budget_ms=0)
    p = rig.run(T0, "--verbose")
    assert "children: error: interrupted" in p.stdout


def test_baseline_only_after_a_successful_run(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    rig.config(t3_db=str(db), children={})
    rig.run(T0)  # no database yet: the check fails and stays uninitialised
    assert "children" not in rig.state()["initialised"]
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead")
    add_thread(conn, "old", "Old failure", parent="lead-1")
    add_run(conn, "old", 1, "failed", at(-30), at(-20))
    rig.run(at(15))
    assert rig.wakes() == []  # the first successful run is the baseline
    assert "children" in rig.state()["initialised"]


def test_failing_check_alerts_once_and_rearms(tmp_path):
    rig = Rig(tmp_path)
    flag = tmp_path / "broken"
    flag.write_text("")
    meter = rig.script("m.py", f"import os, sys\nif os.path.exists({str(flag)!r}): sys.exit(1)\nprint('week 50 %')\n")
    rig.config(usage={"command": meter})
    rig.run(T0)
    rig.run(at(60))
    assert rig.wakes() == []
    rig.run(at(125))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "orch-1"
    assert w[0]["text"].startswith("watcher: usage failing since 2026-01-07T12:00:00.000Z: no weekly percentage")
    rig.run(at(200))
    assert rig.wakes() == []  # once per streak
    flag.unlink()
    rig.run(at(215))
    flag.write_text("")
    rig.run(at(230))
    rig.run(at(360))
    assert len(rig.wakes()) == 1  # re-armed after a success


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



def fake_uuid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


TASK_UUID = fake_uuid(0xbb)  # T3 names a delegate's thread after the parent's command and the delegate task
CHILD = f"thread:delegated-task:command%3Amcp%3A{fake_uuid(0xaa)}%3Adelegate-task%3A{TASK_UUID}"


def child_rig(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "orch-1", "Orchestrator: shop")
    add_thread(conn, CHILD, "Retrain stage", parent="orch-1", updated=at(10))
    rig.config(t3_db=str(db), decision={"minutes": 30})
    rig.run(T0)  # baseline
    # The child's turn was started by an outside send (a watcher jobs wake), so T3 relays nothing to the parent.
    add_message(conn, "u1", CHILD, "user", "watcher: jobs: train.json: all 2 jobs ended", at(5))
    add_message(conn, "a1", CHILD, "assistant", "s05 finished.", at(7))
    add_message(conn, "a2", CHILD, "assistant", "decision: the vocabulary diff is ready. GO?", at(10))
    add_run(conn, CHILD, 1, "completed", at(5), at(10))
    return rig, conn


def test_child_decision_not_relayed_wakes_parent_once(tmp_path):
    rig, conn = child_rig(tmp_path)
    add_message(conn, "p1", "orch-1", "user", "unrelated question from the user", at(20))
    add_message(conn, "p2", "orch-1", "user", f"is {TASK_UUID} done yet?", at(21))  # quotes the id, not a relay
    rig.run(at(30))
    assert rig.wakes() == []  # only 20 min old
    rig.run(at(45))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "orch-1"
    assert w[0]["text"].startswith('watcher: decision: child "Retrain stage" (' + CHILD + ") ended with a decision"
                                   " 35 min ago that T3 did not relay to you: decision: the vocabulary diff is ready.")
    rig.run(at(200))
    assert rig.wakes() == []


def relay_text(task_uuid):  # the continuation form: the child id encoded twice, then the run ordinal
    return ("Delegated task node:delegated-task:provider-continuation%3Amessage%3Athread%3Athread%253Adelegated-task"
            f"%253Adelegate-task%25253A{task_uuid.upper()}%3Aordinal%3A1%3A{fake_uuid(0xcc)} (Retrain stage)"
            " [completed]")


def test_child_decision_relayed_answered_or_busy_is_quiet(tmp_path):
    rig, conn = child_rig(tmp_path)
    add_message(conn, "p1", "orch-1", "user", relay_text(TASK_UUID), at(11))
    rig.run(at(60))
    assert rig.wakes() == []  # the parent got the relay
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_rig(tmp_path / "b")
    add_message(conn2, "a3", CHILD, "user", "GO", at(20))
    rig2.run(at(60))
    assert rig2.wakes() == []  # the parent answered: the decision is no longer the child's last message
    add_message(conn2, "a4", CHILD, "assistant", "decision: second question?", at(61))
    add_run(conn2, CHILD, 2, "running", at(62))
    rig2.run(at(120))
    assert rig2.wakes() == []  # the child is busy again


def test_child_decision_waits_for_a_busy_parent(tmp_path):
    rig, conn = child_rig(tmp_path)
    add_run(conn, "orch-1", 1, "running", at(8))
    rig.run(at(45))
    assert rig.wakes() == []  # T3 may relay when the parent's turn ends
    conn.execute("UPDATE orchestration_v2_projection_runs SET status = 'completed' WHERE thread_id = 'orch-1'")
    conn.commit()
    add_message(conn, "p1", "orch-1", "user", relay_text(TASK_UUID), at(50))
    rig.run(at(60))
    assert rig.wakes() == []  # the relay came after the parent's turn
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_rig(tmp_path / "b")
    add_run(conn2, "orch-1", 1, "running", at(8))
    rig2.run(at(45))
    conn2.execute("UPDATE orchestration_v2_projection_runs SET status = 'completed' WHERE thread_id = 'orch-1'")
    conn2.commit()
    rig2.run(at(60))
    assert len(rig2.wakes()) == 1  # still no relay once the parent is idle


def test_child_decision_held_wake_is_dropped_once_answered(tmp_path):
    rig, conn = child_rig(tmp_path)
    state = rig.state()
    state["sent"] = {"orch-1": at(40)}  # the parent was woken recently: the next wake is held
    (rig.state_dir / "watch.json").write_text(json.dumps(state))
    rig.run(at(45))
    assert rig.wakes() == [] and len(rig.state()["outbox"]) == 1
    add_message(conn, "a3", CHILD, "user", "GO", at(50))
    p = rig.run(at(105), "--verbose")
    assert rig.wakes() == [] and rig.state()["outbox"] == []
    assert "answered or relayed meanwhile" in p.stdout


def test_child_decision_ignores_other_cases(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "orch-1", "Orchestrator: shop", updated=at(10))
    add_thread(conn, "gone", "Archived lead")
    conn.execute("UPDATE orchestration_v2_projection_threads SET archived_at = ? WHERE thread_id = 'gone'", (T0,))
    add_thread(conn, "c-orphan", "Orphan", parent="gone", updated=at(10))
    add_thread(conn, "c-stream", "Streaming", parent="orch-1", updated=at(10))
    add_thread(conn, "c-plain", "No uuid child", parent="orch-1", updated=at(10))
    rig.config(t3_db=str(db), decision={"threads": ["x"]})
    rig.run(T0)
    add_message(conn, "o1", "orch-1", "assistant", "decision: top-level threads have no parent to wake", at(10))
    add_message(conn, "g1", "c-orphan", "assistant", "decision: parent archived", at(10))
    add_message(conn, "s1", "c-stream", "assistant", "decision: still writing", at(10))
    conn.execute("UPDATE orchestration_v2_projection_messages SET streaming = 1 WHERE message_id = 's1'")
    add_message(conn, "n1", "c-plain", "assistant", "decision: relayed?", at(10))
    add_message(conn, "r1", "orch-1", "user", "Delegated task node:c-plain (No uuid child) [completed]", at(11))
    conn.commit()
    rig.run(at(60))
    assert rig.wakes() == []
    rig.config(t3_db=str(db), decision={"threads": ["x"], "children": False})
    add_message(conn, "n2", "c-plain", "assistant", "decision: off?", at(61))
    rig.run(at(120))
    assert rig.wakes() == []


def test_child_decision_part_starts_with_a_baseline(tmp_path):
    rig, conn = child_rig(tmp_path)
    add_thread(conn, "c2", "Young", parent="orch-1", updated=at(40))
    state = rig.state()
    del state["decision"]["child_seen"]  # state written before the child part existed
    (rig.state_dir / "watch.json").write_text(json.dumps(state))
    add_message(conn, "y1", "c2", "assistant", "decision: young one?", at(40))
    rig.run(at(45))
    assert rig.wakes() == []
    assert set(rig.state()["decision"]["child_seen"]) == {"a2", "y1"}  # recorded whatever their age
    rig.run(at(90))
    assert rig.wakes() == []
    add_message(conn, "a3", CHILD, "assistant", "decision: another one?", at(90))
    rig.run(at(130))
    assert len(rig.wakes()) == 1

def child_result_rig(tmp_path, text="done: s05 trained, metrics in results/s05.json"):
    rig, conn = child_rig(tmp_path)
    conn.execute("DELETE FROM orchestration_v2_projection_messages WHERE message_id = 'a2'")
    conn.commit()
    add_message(conn, "a2", CHILD, "assistant", text, at(10))
    return rig, conn


def test_child_result_not_relayed_wakes_parent_once(tmp_path):
    for word in ("done", "failed", "handoff"):
        sub = tmp_path / word
        sub.mkdir()
        rig, conn = child_result_rig(sub, f"**{word}:** the s05 report")
        rig.run(at(45))
        w = rig.wakes()
        assert len(w) == 1 and w[0]["thread"] == "orch-1"
        assert w[0]["text"].startswith(f'watcher: decision: child "Retrain stage" ({CHILD}) ended with a {word}:'
                                       " report 35 min ago that T3 did not relay to you:")
        rig.run(at(200))
        assert rig.wakes() == []
    (tmp_path / "x").mkdir()
    rig, conn = child_result_rig(tmp_path / "x", "s05 still training")
    rig.run(at(45))
    assert rig.wakes() == []  # progress, not a report


def test_child_result_sent_by_the_child_is_quiet(tmp_path):
    rig, conn = child_result_rig(tmp_path)
    # The child sent its result itself during the turn (before its final message), as the jobs wake asks.
    conn.execute("INSERT INTO orchestration_v2_projection_messages VALUES (?, ?, ?, 0, ?, ?, ?)",
                 ("s1", "orch-1", "user", at(9), at(9), json.dumps({"text": "done: s05", "senderThreadId": CHILD})))
    conn.commit()
    rig.run(at(45))
    assert rig.wakes() == []
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_result_rig(tmp_path / "b")
    conn2.execute("INSERT INTO orchestration_v2_projection_messages VALUES (?, ?, ?, 0, ?, ?, ?)",
                  ("s1", "orch-1", "user", at(4), at(4), json.dumps({"text": "old", "senderThreadId": CHILD})))
    conn2.commit()
    rig2.run(at(45))
    assert len(rig2.wakes()) == 1  # a send from before this turn does not count


def test_child_report_sent_with_a_subject_or_after_the_message_is_quiet(tmp_path):
    rig, conn = child_result_rig(tmp_path, "handoff: the dev guards say GO")
    child_sends(conn, "s1", "handoff (s05): the dev guards say GO", at(9))  # before its final message
    rig.run(at(45))
    assert rig.wakes() == []
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_result_rig(tmp_path / "b", "done: s05 trained")
    child_sends(conn2, "s1", "Results for s05 are in results/s05.json", at(11))  # any send after its message
    rig2.run(at(45))
    assert rig2.wakes() == []
    (tmp_path / "c").mkdir()
    rig3, conn3 = child_result_rig(tmp_path / "c", "done: s05 trained")
    child_sends(conn3, "s1", "progress: still on it", at(9))  # earlier in the turn and not a report
    child_sends(conn3, "s2", "Done with step 1 of 3: moving on", at(9))
    rig3.run(at(45))
    assert len(rig3.wakes()) == 1


def test_child_report_sent_with_its_subject_first_is_quiet(tmp_path):
    for i, text in enumerate(("cls-freeze handoff: the dev guards say GO",
                              "**Retrain stage** - done (s05): trained",
                              "Classifier freeze \u2014 **failed**: s05 OOM",
                              "_done_ \u2014 s05: trained")):
        (tmp_path / str(i)).mkdir()
        rig, conn = child_result_rig(tmp_path / str(i), "handoff: the dev guards say GO")
        child_sends(conn, "s1", text, at(9))  # earlier in the turn, before its final message
        rig.run(at(45))
        assert rig.wakes() == [], text
    for i, text in enumerate(("Not done: still training", "not **done**: epoch 30", "Training not yet done: 30/40",
                              "isn’t done: epoch 30", "Step 1 of 3 done: starting step 2",
                              "Download failed: retrying on the mirror", "Done with step 1 of 3: moving on")):
        (tmp_path / f"n{i}").mkdir()
        rig, conn = child_result_rig(tmp_path / f"n{i}", "done: s05 trained")
        child_sends(conn, "s1", text, at(9))
        rig.run(at(45))
        assert len(rig.wakes()) == 1, text  # progress, not a report: the real one still wakes


def child_sends(conn, mid, text, created):
    conn.execute("INSERT INTO orchestration_v2_projection_messages VALUES (?, ?, ?, 0, ?, ?, ?)",
                 (mid, "orch-1", "user", created, created, json.dumps({"text": text, "senderThreadId": CHILD})))
    conn.commit()


def test_child_result_progress_send_does_not_count_and_held_wake_drops(tmp_path):
    rig, conn = child_result_rig(tmp_path)
    child_sends(conn, "s1", "progress: s05 at epoch 30", at(8))  # not a report
    state = rig.state()
    state["sent"] = {"orch-1": at(40)}  # the parent was woken recently: the next wake is held
    (rig.state_dir / "watch.json").write_text(json.dumps(state))
    rig.run(at(45))
    assert rig.wakes() == [] and len(rig.state()["outbox"]) == 1
    child_sends(conn, "s2", "done: s05 trained", at(50))  # sent late, but within the turn's window
    p = rig.run(at(105), "--verbose")
    assert rig.wakes() == [] and rig.state()["outbox"] == []
    assert "answered or relayed meanwhile" in p.stdout


def test_child_result_turn_ignores_a_later_empty_run_and_old_held_wakes(tmp_path):
    rig, conn = child_result_rig(tmp_path)
    child_sends(conn, "s1", "done: s05 trained", at(9))
    add_run(conn, CHILD, 2, "failed", at(20), at(21))  # a later run that wrote nothing
    rig.run(at(45))
    assert rig.wakes() == []
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_result_rig(tmp_path / "b")
    state = rig2.state()  # a wake held before the upgrade: its recheck has no "since"
    state["outbox"] = [{"thread": "orch-1", "line": "watcher: decision: old", "urgent": False, "prompt": None,
                        "created": at(40), "attempts": 0,
                        "recheck": {"child": CHILD, "message": "a2", "parent": "orch-1", "created": at(10)}}]
    state["sent"] = {"orch-1": at(40)}
    state["decision"]["child_seen"]["a2"] = at(40)
    (rig2.state_dir / "watch.json").write_text(json.dumps(state))
    child_sends(conn2, "s1", "done: s05 trained", at(9))  # sent during the turn, before the final message
    p = rig2.run(at(105), "--verbose")
    assert rig2.wakes() == [] and "answered or relayed meanwhile" in p.stdout


def test_child_result_kinds_start_with_a_baseline(tmp_path):
    rig, conn = child_result_rig(tmp_path)
    state = rig.state()
    del state["decision"]["child_results"]  # state written when only decision: counted
    (rig.state_dir / "watch.json").write_text(json.dumps(state))
    rig.run(at(45))
    assert rig.wakes() == []
    assert "a2" in rig.state()["decision"]["child_seen"]
    add_message(conn, "a3", CHILD, "assistant", "decision: and now?", at(50))
    rig.run(at(100))
    assert len(rig.wakes()) == 1
    (tmp_path / "b").mkdir()
    rig2, conn2 = child_rig(tmp_path / "b")  # a decision: already open on the upgrade run still wakes
    state = rig2.state()
    del state["decision"]["child_results"]
    (rig2.state_dir / "watch.json").write_text(json.dumps(state))
    rig2.run(at(45))
    assert len(rig2.wakes()) == 1


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
    assert "small pilot" in w[0]["text"] and "ask threshold in local.md" in w[0]["text"]
    assert "last 3 days" in w[0]["text"]
    rig.run(at(30))
    rig.run(at(300))
    assert rig.wakes() == []


def test_usage_no_second_reset_within_five_days(tmp_path):
    rig, meter = usage_rig(tmp_path, 80)
    rig.run(T0)
    meter.write_text("1")
    rig.run(at(15))
    assert len(rig.wakes()) == 1
    meter.write_text("40")
    rig.run(at(2 * 24 * 60))
    meter.write_text("15")  # a dip of 25 points two days later is not a reset
    rig.run(at(2 * 24 * 60 + 15))
    assert rig.wakes() == []
    meter.write_text("90")
    rig.run(at(7 * 24 * 60))
    meter.write_text("2")
    rig.run(at(7 * 24 * 60 + 15))
    assert len(rig.wakes()) == 1


def test_usage_far_behind_pace_rearms_daily(tmp_path):
    # reset Monday 00:00 UTC, now Wednesday 12:00: 2.5 days, pace 15 %/day -> 37.5 % expected
    rig, meter = usage_rig(tmp_path, 10, weekly_reset="Mon 00:00", pace_per_day=15, behind_by=20)
    rig.config(usage=json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]["usage"], timezone="UTC")
    rig.run(T0)
    assert rig.wakes() == []  # first run: recorded without waking
    rig.run(at(60))
    assert rig.wakes() == []
    rig.run(at(24 * 60 + 5))
    w = rig.wakes()
    assert len(w) == 1 and "far behind pace: 10 % of the week used, pace says 53 %" in w[0]["text"]
    meter.write_text("60")
    rig.run(at(2 * 24 * 60 + 10))
    assert rig.wakes() == []


def test_usage_second_account_has_own_state_and_label(tmp_path):
    rig = Rig(tmp_path)
    m1, m2 = tmp_path / "a.txt", tmp_path / "b.txt"
    m1.write_text("80")
    m2.write_text("70")
    cmd = rig.script("meter.py", f"print('seven_day used', open({str(m1)!r}).read(), '%')\n"
                                 f"print('acct2 seven_day used', open({str(m2)!r}).read(), '%')\n")
    rig.config(usage={"command": cmd, "weekly_pattern": r"^seven_day\s+used\s+([0-9.]+)", "ideas_file": "/x/I.md",
                      "accounts": [{"name": "acct2", "weekly_pattern": r"^acct2 seven_day\s+used\s+([0-9.]+)"},
                                   {"name": "broken", "weekly_pattern": r"^nothing ([0-9]+)"}]})
    rig.run(T0)
    assert rig.wakes() == []
    m2.write_text("1")  # only the second account resets
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["text"].startswith("watcher: usage: [acct2] weekly usage reset (1 % used now, was 70 %).")
    m1.write_text("2")  # the first account resets later; its state was kept despite the broken third account
    rig.run(at(90))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["text"].startswith("watcher: usage: weekly usage reset (2 % used now, was 80 %).")


def test_usage_broken_accounts_never_cost_the_first_account_its_wake(tmp_path):
    rig = Rig(tmp_path)
    m1, m2 = tmp_path / "a.txt", tmp_path / "b.txt"
    m1.write_text("80")
    m2.write_text("10")
    cmd = rig.script("meter.py", f"print('seven_day used', open({str(m1)!r}).read(), '%')\n"
                                 f"print('b seven_day used', open({str(m2)!r}).read(), '%')\n")
    pat = r"^b seven_day\s+used\s+([0-9.]+)"
    rig.config(usage={"command": cmd, "weekly_pattern": r"^seven_day\s+used\s+([0-9.]+)", "ideas_file": "/x/I.md",
                      "accounts": [None, {"name": "bad", "weekly_pattern": pat, "weekly_reset": "Wed"},
                                   {"name": "nopat"}, {"name": "regex", "weekly_pattern": "(["},
                                   {}, {"name": ""}, {"name": "b", "weekly_pattern": pat},
                                   {"name": "b", "weekly_pattern": r"^seven_day\s+used\s+([0-9.]+)"}]})
    p = rig.run(T0)
    assert rig.wakes() == []
    for err in ("#1: error: each accounts entry needs a non-empty string name", "bad: error:",
                "nopat: error: each accounts entry needs its own weekly_pattern", "regex: error:",
                "#5: error: each accounts", "#6: error: each accounts", "#8: error: duplicate account name"):
        assert f"watcher: usage {err}" in p.stderr
    assert rig.state()["usage"]["accounts"] == {"b": rig.state()["usage"]["accounts"]["b"]}
    # the duplicate "b" reads 80 and the first "b" reads 10: shared state would wake a false reset on every run
    m1.write_text("2")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and w[0]["text"].startswith("watcher: usage: weekly usage reset (2 % used now, was 80 %).")
    rig.run(at(30))
    assert rig.wakes() == [] and rig.state()["usage"]["accounts"]["b"]["pct"] == 10


def test_usage_account_added_later_records_first_and_alerts_once_when_broken(tmp_path):
    rig, meter = usage_rig(tmp_path, 80, weekly_reset="Mon 00:00", pace_per_day=15, behind_by=20, rearm_hours=1)
    rig.run(T0)  # the first account alone, as before accounts existed
    usage = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]["usage"]
    usage["accounts"] = [{"name": "acct2", "weekly_pattern": r"^five_hour used ([0-9.]+)"},
                         {"name": "gone", "weekly_pattern": r"^missing ([0-9]+)"}]
    rig.config(usage=usage, timezone="UTC", fail_alert_minutes=60)
    rig.run(at(15))
    assert rig.wakes() == []  # acct2 reads 3 %, far behind pace, but its first run only records
    assert rig.state()["usage"]["accounts"]["acct2"]["pct"] == 3
    rig.run(at(80))
    w = rig.wakes()
    assert len(w) == 1 and "[acct2] far behind pace: 3 %" in w[0]["text"]  # one wake per thread per run
    assert "watcher: usage gone: error: no weekly percentage read" in w[0]["text"]
    rig.run(at(110))
    assert rig.wakes() == []  # the broken entry alerts only once, the pace wake waits rearm_hours again
    usage["accounts"] = usage["accounts"][:1]
    rig.config(usage=usage, timezone="UTC")
    rig.run(at(150))
    assert "usage:gone" not in rig.state().get("health", {})


def test_usage_accounts_need_explicit_first_pattern(tmp_path):
    rig, meter = usage_rig(tmp_path, 80)
    usage = json.loads((rig.cfg_dir / "config.json").read_text())["watcher"]["usage"]
    del usage["weekly_pattern"]
    usage["accounts"] = [{"name": "acct2", "weekly_pattern": r"^five_hour used ([0-9.]+)"}]
    rig.config(usage=usage)
    p = rig.run(T0)
    assert "usage: error: usage.weekly_pattern is required when usage.accounts is set" in p.stderr

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


def test_failed_delivery_backs_off_and_drops_only_after_24h(tmp_path):
    rig = Rig(tmp_path)
    calls = tmp_path / "calls"
    rig.base["wake"] = {"command": rig.script("fail.py", f"import sys\nopen({str(calls)!r}, 'a').write('x')\n"
                                                         "sys.exit(3)\n")}
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    p = rig.run(T0)
    assert "FAILED to wake orch-1" in p.stdout and "watcher: FAILED" in p.stderr  # urgent: loud
    for m in range(30, 23 * 60, 30):
        rig.run(at(m))
    attempts = len(calls.read_text())
    assert 15 <= attempts <= 30  # backoff 15, 30, 60, 60 ... min, never dropped after a few attempts
    assert len(rig.state()["outbox"]) == 1
    p = rig.run(at(24 * 60 + 15))
    assert "DROPPED URGENT wake for orch-1" in p.stderr and rig.state()["outbox"] == []


def test_one_broken_webhook_does_not_stop_other_threads(tmp_path):
    rig = Rig(tmp_path)
    proj = tmp_path / "proj"
    jobs = proj / ".work/orchestrator/jobs"
    jobs.mkdir(parents=True)
    (jobs / "a.json").write_text(json.dumps({"thread": "child-a", "jobs": ["1"]}))
    (jobs / "b.json").write_text(json.dumps({"thread": "child-b", "jobs": ["2"]}))
    rig.base["wake"]["webhooks"] = {"child-a": "not a url"}
    listing = rig.script("b.py", "print('1 RUN')\nprint('2 RUN')\n")
    rig.config(projects=[str(proj)], jobs={"command": listing})
    rig.run(T0)
    rig.config(projects=[str(proj)], jobs={"command": rig.script("c.py", "print('1 DONE')\nprint('2 DONE')\n")})
    p = rig.run(at(15))
    assert [w["thread"] for w in rig.wakes()] == ["child-b"]
    assert "FAILED to wake child-a" in p.stdout
    assert [i["thread"] for i in rig.state()["outbox"]] == ["child-a"]


def test_default_t3_send_puts_double_dash_before_the_thread(tmp_path):
    rig = Rig(tmp_path)
    argv_file = tmp_path / "argv.json"
    fake_t3 = tmp_path / "t3"
    fake_t3.write_text(f"#!{sys.executable}\nimport json, sys\n"
                       f"json.dump([sys.argv[1:], sys.stdin.read()], open({str(argv_file)!r}, 'w'))\n")
    fake_t3.chmod(0o755)
    rig.base["wake"] = {"t3_bin": str(fake_t3)}
    rig.config(t3_home=str(tmp_path / "t3home"), disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    rig.run(T0)
    args, text = json.loads(argv_file.read_text())
    assert args == ["thread", "send", "--base-dir", str(tmp_path / "t3home"), "--", "orch-1", "-"]
    assert text.startswith("watcher: disk: ")


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


# ---------- round 2 ----------

def write_state(rig, state):
    rig.state_dir.mkdir(parents=True, exist_ok=True)
    (rig.state_dir / "watch.json").write_text(json.dumps(state))


def test_incompatible_partial_index_is_rejected_by_the_plan(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db, indexes=False)
    conn.executescript(INDEXES.replace("WHERE status IN ('queued', 'preparing', 'starting', 'running', 'waiting')",
                                       "WHERE status IN ('running')"))
    add_thread(conn, "lead-1", "Lead")
    rig.config(t3_db=str(db), children={})
    p = rig.run(T0, "--verbose")
    assert "children: error: query plan would scan orchestration_v2_projection_runs" in p.stdout
    (tmp_path / "ok").mkdir()
    rig2 = Rig(tmp_path / "ok")
    db2 = tmp_path / "ok" / "state.sqlite"
    make_db(db2)
    rig2.config(t3_db=str(db2), children={}, decision={"threads": ["x"]})
    p = rig2.run(T0, "--verbose")
    assert "error" not in p.stdout  # the full index set passes


def test_webhook_errors_never_leak_the_token(tmp_path):
    token = "SYNTH-TOKEN-8f3a"
    rig = Rig(tmp_path)
    rig.base["wake"] = {"webhooks": {"orch-1": f"not a url/{token}?k={token}",
                                     "lead-1": f"http://127.0.0.1:notaport/api/hooks/t/{token}"}}
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead")
    add_thread(conn, "c1", "Broken", parent="lead-1")
    rig.config(t3_db=str(db), children={}, disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    rig.run(T0)
    add_thread(conn, "c2", "Broken too", parent="lead-1", updated=at(10))
    add_run(conn, "c2", 1, "failed", at(5), at(10))
    p = rig.run(at(15), "--verbose")
    assert "FAILED to wake orch-1" in p.stdout and "FAILED to wake lead-1" in p.stdout
    assert "details hidden" in p.stdout
    assert token not in p.stdout + p.stderr + (rig.state_dir / "watch.json").read_text()


def test_cached_raw_job_states_are_normalised(tmp_path):
    rig, listing = job_rig(tmp_path, "102 DONE\n")
    write_state(rig, {"initialised": ["jobs"], "jobs": {"seen_jobs": {"101": {"state": "EXIT", "at": T0}}}})
    rig.run(at(5))
    w = rig.wakes()
    assert len(w) == 1 and "all 2 jobs ended (DONE 1, FAILED 1)" in w[0]["text"]


def test_old_open_job_file_is_still_tracked(tmp_path):
    rig, listing = job_rig(tmp_path, "101 RUN\n102 RUN\n")
    write_state(rig, {"initialised": ["jobs"], "jobs": {}})
    os.utime(tmp_path / "proj/.work/orchestrator/jobs/train.json", (1, 1))
    rig.run(T0)
    listing.write_text("101 DONE\n102 DONE\n")
    rig.run(at(15))
    w = rig.wakes()
    assert len(w) == 1 and "all 2 jobs ended (DONE 2)" in w[0]["text"]


def test_no_job_found_counts_as_empty_and_purged_jobs_end_once(tmp_path):
    rig = Rig(tmp_path)
    proj = tmp_path / "proj"
    jobs = proj / ".work/orchestrator/jobs"
    jobs.mkdir(parents=True)
    f = jobs / "lost.json"
    f.write_text(json.dumps({"thread": "child-5", "jobs": ["7", "8"]}))
    os.utime(f, (parse_ts(at(-11 * 60)),) * 2)
    lsf = rig.script("bjobs.py", "import sys\nprint('No unfinished job found', file=sys.stderr)\nsys.exit(255)\n")
    rig.config(projects=[str(proj)], jobs={"command": lsf})
    write_state(rig, {"initialised": ["jobs"], "jobs": {}})
    p = rig.run(T0, "--verbose")
    assert "error" not in p.stdout
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "child-5"
    assert w[0]["text"].startswith("watcher: jobs: lost.json: all 2 jobs ended (GONE 2). GONE means no longer listed")
    rig.run(at(80))
    rig.run(at(200))
    assert rig.wakes() == []


def test_upgrade_seeds_initialised_from_existing_state(tmp_path):
    rig = Rig(tmp_path)
    db = tmp_path / "state.sqlite"
    conn = make_db(db)
    add_thread(conn, "lead-1", "Lead")
    add_thread(conn, "c1", "Broken", parent="lead-1")
    add_run(conn, "c1", 1, "failed", at(-10), at(-5))
    rig.config(t3_db=str(db), children={})
    write_state(rig, {"children": {"seen": {}}})  # older state file, no "initialised"
    rig.run(T0)
    w = rig.wakes()
    assert len(w) == 1 and w[0]["thread"] == "lead-1"  # not treated as a fresh baseline


def test_new_urgent_item_skips_the_thread_backoff(tmp_path):
    rig = Rig(tmp_path)
    old = {"thread": "orch-1", "line": "watcher: decision: older", "urgent": False, "prompt": None,
           "created": at(-5), "attempts": 1, "retry_at": at(10)}
    write_state(rig, {"initialised": ["disk"], "outbox": [old]})
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 0})
    p = rig.run(T0)
    assert rig.wakes() == [] and "backing off for orch-1" in p.stdout  # nothing new: the backoff holds
    rig.config(disk={"paths": [str(tmp_path)], "min_free_gb": 10**9})
    rig.run(at(1))
    w = rig.wakes()
    assert len(w) == 1 and "watcher: disk:" in w[0]["text"] and "older" in w[0]["text"]


# ---------- expiry ----------

def expiry_rig(tmp_path, left_text):
    rig = Rig(tmp_path)
    out = tmp_path / "status.txt"
    out.write_text(left_text)
    cmd = rig.script("status.py", f"print(open({str(out)!r}).read(), end='')\n")
    rig.config(expiry={"command": cmd, "name": "tsh", "hint": "Run tsh login.", "only_with_jobs": False,
                       "pattern": r"Valid until:\s+(\S+ \S+ [+-]\d{4})"})
    return rig, out


def test_expiry_wakes_once_per_login_and_again_after_renewal(tmp_path):
    rig, out = expiry_rig(tmp_path, "  Valid until: 2026-01-07 14:00:00 +0000 UTC [valid for 2h0m0s]\n")
    rig.run(T0)
    assert rig.wakes() == []
    rig.run(at(75))  # 45 min left: urgent, so not held by quiet hours or the rate limit
    w = rig.wakes()
    assert w == [{"thread": "orch-1", "text": "watcher: expiry: tsh valid for 45 min more (until 2026-01-07 14:00)."
                                              " Run tsh login."}]
    rig.run(at(90))
    rig.run(at(130))
    assert rig.wakes() == []
    out.write_text("  Valid until: 2026-01-07 09:00:00 -0500 -05 [valid for 1h0m0s]\n")  # renewed: 14:00 UTC again
    rig.run(at(140))
    assert rig.wakes() == []  # same expiry time as already reported
    out.write_text("  Valid until: 2026-01-07 22:30:00 +0000\n")
    rig.run(at(150))
    out.write_text("  Valid until: 2026-01-07 22:30:00 +0000 [EXPIRED]\n")
    rig.run(at(631))
    w = rig.wakes()
    assert len(w) == 1 and "tsh expired at 2026-01-07 22:30." in w[0]["text"]


def test_expiry_logged_out_or_duration_pattern(tmp_path):
    rig, out = expiry_rig(tmp_path, "Not logged in.\n")
    rig.run(T0)
    w = rig.wakes()
    assert len(w) == 1 and "tsh: no validity found" in w[0]["text"]
    rig.run(at(15))
    assert rig.wakes() == []
    (tmp_path / "d").mkdir()
    rig2 = Rig(tmp_path / "d")
    rig2.config(expiry={"command": rig2.script("s.py", "print('valid for 0h20m5s')\n"),
                        "pattern": r"valid for (\S+)", "only_with_jobs": False})
    rig2.run(T0)
    w = rig2.wakes()
    assert len(w) == 1 and "login valid for 20 min more" in w[0]["text"]
    rig2.config(expiry={"command": rig2.script("s.py", "print('valid for 0h5m3s')\n"), "pattern": r"valid for (\S+)",
                        "only_with_jobs": False})
    rig2.run(at(15))
    assert rig2.wakes() == []  # the same login, read again as a duration a few seconds off


def test_expiry_is_off_by_default(tmp_path):
    rig = Rig(tmp_path)
    rig.config()
    p = rig.run(T0, "--verbose")
    assert "expiry" not in p.stdout and rig.wakes() == []


def test_expiry_only_while_a_job_file_is_open_and_to_its_owners(tmp_path):
    rig = Rig(tmp_path)
    out = tmp_path / "status.txt"
    out.write_text("  Valid until: 2026-01-07 12:45:00 +0000\n")  # 45 min left at T0
    calls = tmp_path / "calls.txt"
    status = rig.script("status.py", f"open({str(calls)!r}, 'a').write('x')\nprint(open({str(out)!r}).read(), end='')\n")
    listing = tmp_path / "listing.txt"
    listing.write_text("101 RUN\n")
    bjobs = rig.script("bjobs.py", f"print(open({str(listing)!r}).read(), end='')\n")
    shared = rig.state_dir / "jobs"
    rig.config(jobs={"command": bjobs}, expiry={"command": status, "name": "tsh",
                                                "pattern": r"Valid until:\s+(\S+ \S+ [+-]\d{4})"})
    p = rig.run(T0, "--verbose")
    assert rig.wakes() == [] and not calls.exists()  # no job file: the login is not even read
    assert "tsh not checked, no open job file" in p.stdout
    shared.mkdir(parents=True)
    (shared / "a.json").write_text(json.dumps({"thread": "child-7", "jobs": ["101"]}))
    (shared / "b.json").write_text(json.dumps({"thread": "child-8", "jobs": ["101"]}))
    rig.run(at(1))
    w = rig.wakes()
    assert sorted(x["thread"] for x in w) == ["child-7", "child-8"]  # the owners, not the orchestrator
    assert all("tsh valid for 44 min more" in x["text"] for x in w)
    rig.run(at(2))
    assert rig.wakes() == []  # once per expiry time and recipient
    (shared / "c.json").write_text(json.dumps({"thread": "child-9", "jobs": ["101"]}))
    rig.run(at(3))
    assert [x["thread"] for x in rig.wakes()] == ["child-9"]  # a new owner gets it too
    listing.write_text("101 DONE\n")
    rig.run(at(20))
    assert all(x["text"].startswith("watcher: jobs:") for x in rig.wakes())  # jobs ended: their own wake only
    out.write_text("Not logged in.\n")
    n = len(calls.read_text())
    rig.run(at(40))
    assert rig.wakes() == [] and len(calls.read_text()) == n  # all files ended: no check, no wake


def test_expiry_still_warns_when_the_jobs_check_fails_on_the_same_login(tmp_path):
    rig = Rig(tmp_path)
    proj = tmp_path / "proj"
    (proj / ".work/orchestrator/jobs").mkdir(parents=True)
    (proj / ".work/orchestrator/jobs/t.json").write_text(json.dumps({"thread": "child-7", "jobs": ["101"]}))
    fail = rig.script("fail.py", "import sys; sys.exit(255)\n")
    rig.config(projects=[str(proj)], jobs={"command": fail},
               expiry={"command": rig.script("s.py", "print('Not logged in.')\n"), "name": "tsh",
                       "pattern": r"Valid until:\s+(\S+ \S+ [+-]\d{4})"})
    rig.run(T0)
    w = rig.wakes()
    assert [x["thread"] for x in w] == ["child-7"] and "tsh: no validity found" in w[0]["text"]


def test_expiry_state_from_before_warned_to_does_not_repeat(tmp_path):
    rig = Rig(tmp_path)
    shared = rig.state_dir / "jobs"
    shared.mkdir(parents=True)
    (shared / "a.json").write_text(json.dumps({"thread": "child-7", "jobs": ["101"]}))
    rig.config(jobs={"command": rig.script("b.py", "print('101 RUN')\n")},
               expiry={"command": rig.script("s.py", "print('Not logged in.')\n"),
                       "pattern": r"Valid until:\s+(\S+)"})
    (rig.state_dir / "watch.json").write_text(json.dumps({"initialised": ["jobs", "expiry"],
                                                          "expiry": {"warned": "unreadable"}}))
    rig.run(T0)
    assert rig.wakes() == []


# ---------- job files from any project ----------

def test_jobs_from_the_shared_dir_and_globs(tmp_path):
    rig = Rig(tmp_path)
    listing = tmp_path / "listing.txt"
    listing.write_text("101 RUN\n202 RUN\n303 RUN\n")
    other = tmp_path / "Wings" / ".work" / "orchestrator" / "jobs"
    other.mkdir(parents=True)
    (other / "eval.json").write_text(json.dumps({"thread": "child-g", "jobs": ["202"]}))
    (other / "done").mkdir()
    (other / "done" / "old.json").write_text(json.dumps({"thread": "child-old", "jobs": ["303"]}))
    proj = tmp_path / "proj"
    rig.config(projects=[str(proj)], jobs={"command": rig.script("b.py", f"print(open({str(listing)!r}).read())\n"),
                                           "globs": [str(tmp_path / "*" / ".work/orchestrator/jobs")]})
    (proj / ".work/orchestrator").mkdir(parents=True)
    (proj / ".work/orchestrator/jobs").symlink_to(other)  # the same files twice count once
    p = rig.run(None, "add", "--job", "101", "--next-step", "read state.md",
                extra_env={"T3CODE_THREAD_ID": "thread:delegated-task:command%3Amcp%3Aabc"})
    assert "watcher job file:" in p.stdout
    data = json.loads((rig.state_dir / "jobs" / "101.json").read_text())
    assert data == {"thread": "thread:delegated-task:command%3Amcp%3Aabc", "jobs": ["101"],
                    "next_step": "read state.md"}
    rig.run(T0)
    assert rig.wakes() == []
    listing.write_text("303 RUN\n")
    rig.run(at(15))
    w = sorted(rig.wakes(), key=lambda x: x["thread"])
    assert [x["thread"] for x in w] == ["child-g", "thread:delegated-task:command%3Amcp%3Aabc"]
    assert "101.json: all 1 jobs ended" in w[1]["text"] and "Next step: read state.md" in w[1]["text"]


def test_add_needs_a_thread_and_refuses_bad_ids_or_overwrites(tmp_path):
    rig = Rig(tmp_path)
    rig.config()
    p = rig.run(None, "add", "--job", "101", check=False)
    assert p.returncode == 2 and "T3CODE_THREAD_ID" in p.stderr
    p = rig.run(None, "add", "--job", "101; rm -rf ~", "--thread", "t1", check=False)
    assert p.returncode == 2 and "invalid job id" in p.stderr
    p = rig.run(None, "add", "--job", "101", "--thread", "bad thread", check=False)
    assert p.returncode == 2
    d = tmp_path / "pj"
    rig.run(None, "add", "-j", "5[1]", "-j", "6", "--thread", "t1", "--parent", "p1", "--name", "ev", "--dir", str(d))
    assert json.loads((d / "ev.json").read_text()) == {"thread": "t1", "jobs": ["5[1]", "6"], "parent": "p1"}
    p = rig.run(None, "add", "-j", "7", "--thread", "t1", "--name", "ev", "--dir", str(d), check=False)
    assert p.returncode == 1 and "exists" in p.stderr
    rig.run(None, "add", "-j", "7", "--thread", "t1", "--name", "ev", "--dir", str(d), "--force")
    assert json.loads((d / "ev.json").read_text())["jobs"] == ["7"]

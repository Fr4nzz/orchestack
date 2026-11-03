#!/usr/bin/env python3
"""orchestack watcher: wake a chat only when something changed. No LLM calls.

A systemd --user timer (watcher/orchestack-watch.timer) runs this every 15 minutes. Each run performs the
configured checks, compares what it sees with the last run and sends one short message per change to the chat that
owns it. The same state never wakes anyone twice.

  jobs      cluster jobs named in <project>/.work/orchestrator/jobs/*.json ended or failed -> the owner thread
  children  a T3 child thread failed, waits on an approval or is stuck -> its parent thread
  disk      free disk below the floor -> the orchestrator (urgent)
  usage     the weekly usage reset happened, or usage is far behind pace -> the orchestrator, with the backlog prompt
            (optional "accounts": further accounts read from the same meter output, each with its own pattern/reset)
  decision  a decision: message to an orchestrator sat unanswered for 30 min -> that orchestrator; a child whose
            last message is a decision:, done:, failed: or handoff: that never reached its parent (no relay and no
            thread send from the child there) for 30 min -> its parent
  expiry    a login (e.g. tsh) has less than warn_minutes left or is gone -> the orchestrator (urgent, opt-in)
  hosts     a host came online -> run its copy command (no wake)

Wakes go through the T3 CLI (`t3 thread send <thread> -`) or, for a thread that has one, a scheduled-task webhook
URL. At most one wake per thread per hour (disk and failures excepted). Wakes held by that limit or by quiet hours
stay in an outbox and go out merged with later ones.

  watch.py [--dry-run] [--verbose] [--no-baseline] [--now ISO-TIME]

--dry-run prints what it would do and changes nothing (no state written, no wake sent, no command run except the
read-only checks). Config: the "watcher" section of config.json in $ORCHESTACK_CONFIG_DIR or ~/.config/orchestack.
State: watch.json in $ORCHESTACK_STATE_DIR or ~/.local/state/orchestack. The T3 database is opened read-only and is
read by indexed columns only (it can be tens of GB): a check whose indexes are missing is skipped, and its
queries are aborted after db_budget_ms (2000). A check that keeps failing for fail_alert_minutes (120) wakes the
orchestrator once. Exit 0 unless the config is unreadable.
"""
import argparse
import copy
import fcntl
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

UTC = timezone.utc
ACTIVE = ("queued", "preparing", "starting", "running", "waiting")
DECISION_RE = re.compile(r"^\s*[*_>]*\s*decision:", re.I | re.M)
RESULT_RE = re.compile(r"^\s*[*_>]*\s*(decision|done|failed|handoff):", re.I)  # a child's report to its parent
# A report the child sent its parent itself may name its subject first: "handoff (s05): ...", "**done** - s05:".
SENT_REPORT_RE = re.compile(r"^\s*[*_>]*\s*(decision|done|failed|handoff)[*_]*\s*(\([^)\n]{1,60}\)|[-\u2013]\s*[^:\n]{1,40})?"
                            r"[*_]*\s*:", re.I)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
KEEP_DAYS = 14
OUTBOX_MAX_HOURS = 24
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9:_%.][A-Za-z0-9:_%.-]*$")

BACKLOG_PROMPT = (
    "Budget is unused. Read {ideas} (format: orchestrator skill, Backlog). Pick the most valuable reversible,"
    " in-budget item the user would be proud to find started, price it with a small pilot, run it through"
    " delegates, and report afterwards. Ask before anything over the ask threshold in local.md."
    " If the backlog is empty, look for ideas left unexecuted in the notes file, IDEAS.md and the last 3 days of"
    " chats (no further back), propose 3 ideas grounded in the project's current state, add them to IDEAS.md and"
    " start the best reversible one."
    " Irreversible or outward-facing items wait for the user."
)

Q_PROJECTS = "SELECT project_id FROM projection_projects WHERE deleted_at IS NULL"
Q_RECENT_THREADS = (
    "SELECT thread_id FROM orchestration_v2_projection_threads"
    " WHERE project_id = ? AND updated_at > ? AND deleted_at IS NULL AND archived_at IS NULL"
)
Q_THREAD = (
    "SELECT title, json_extract(payload_json, '$.lineage.parentThreadId'), deleted_at, archived_at"
    " FROM orchestration_v2_projection_threads WHERE thread_id = ?"
)
Q_LATEST_RUN = (
    "SELECT run_id, status, requested_at, completed_at FROM orchestration_v2_projection_runs"
    " WHERE thread_id = ? ORDER BY ordinal DESC LIMIT 1"
)
# The statuses are literals, not parameters: SQLite uses T3's partial index (WHERE status IN (...)) only when it can
# see at prepare time that the query's statuses are covered.
ACTIVE_SQL = ", ".join(f"'{a}'" for a in ACTIVE)
Q_ACTIVE_RUNS = (
    "SELECT run_id, thread_id, status, requested_at, json_extract(payload_json, '$.startedAt')"
    f" FROM orchestration_v2_projection_runs WHERE status IN ({ACTIVE_SQL})"
)
Q_BUSY = f"SELECT 1 FROM orchestration_v2_projection_runs WHERE thread_id = ? AND status IN ({ACTIVE_SQL}) LIMIT 1"
Q_PENDING_REQUESTS = (
    "SELECT runtime_request_id, thread_id, kind, created_at FROM orchestration_v2_projection_runtime_requests"
    " WHERE status = 'pending'"
)
Q_LATEST_USER_MESSAGES = (
    "SELECT message_id, created_at, substr(json_extract(payload_json, '$.text'), 1, 4000)"
    " FROM orchestration_v2_projection_messages WHERE thread_id = ? AND role = 'user'"
    " ORDER BY updated_at DESC, message_id DESC LIMIT 20"
)
Q_ANSWERED_AFTER = (
    "SELECT 1 FROM orchestration_v2_projection_messages"
    " WHERE thread_id = ? AND created_at > ? AND role = 'assistant' AND streaming = 0 LIMIT 1"
)
Q_LATEST_MESSAGE = (
    "SELECT message_id, role, streaming, created_at, substr(json_extract(payload_json, '$.text'), 1, 4000)"
    " FROM orchestration_v2_projection_messages WHERE thread_id = ? ORDER BY created_at DESC, message_id DESC LIMIT 1"
)
# T3's completion relay in the parent: a user message "Delegated task node:<encoded child id> (<title>) [<status>]".
Q_RELAYED = (
    "SELECT 1 FROM orchestration_v2_projection_messages"
    " WHERE thread_id = ? AND created_at >= ? AND role = 'user'"
    " AND json_extract(payload_json, '$.text') LIKE 'Delegated task %'"
    " AND instr(lower(json_extract(payload_json, '$.text')), ?) > 0 LIMIT 1"
)
# Messages one thread sent another with t3_thread_send (the payload names the sender thread).
Q_SENT_BY = (
    "SELECT created_at, substr(json_extract(payload_json, '$.text'), 1, 200) FROM orchestration_v2_projection_messages"
    " WHERE thread_id = ? AND created_at >= ? AND role = 'user'"
    " AND json_extract(payload_json, '$.senderThreadId') = ? ORDER BY created_at DESC LIMIT 50"
)
Q_RECENT_RUNS = (
    "SELECT requested_at, json_extract(payload_json, '$.startedAt') FROM orchestration_v2_projection_runs"
    " WHERE thread_id = ? ORDER BY ordinal DESC LIMIT 10"
)

# Each check's queries with sample parameters. Before a check runs, EXPLAIN QUERY PLAN must show no scan of a big
# table (a plain scan, or a scan of an index that is not partial), or the check is skipped: after a T3 schema change
# a query could otherwise read the whole database.
PLANNED = {
    "children": [(Q_PROJECTS, ()), (Q_RECENT_THREADS, ("", "")), (Q_THREAD, ("",)), (Q_LATEST_RUN, ("",)),
                 (Q_ACTIVE_RUNS, ()), (Q_PENDING_REQUESTS, ()), (Q_BUSY, ("",))],
    "decision": [(Q_BUSY, ("",)), (Q_LATEST_USER_MESSAGES, ("",)), (Q_ANSWERED_AFTER, ("", "")),
                 (Q_PROJECTS, ()), (Q_RECENT_THREADS, ("", "")), (Q_THREAD, ("",)), (Q_LATEST_MESSAGE, ("",)),
                 (Q_RELAYED, ("", "", "")), (Q_SENT_BY, ("", "", "")), (Q_RECENT_RUNS, ("",))],
}
SCAN_OK = {"projection_projects", "orchestration_v2_projection_runtime_requests"}  # tiny tables
SCAN_RE = re.compile(r"^SCAN (?:TABLE )?(\w+)(?: USING (?:COVERING )?INDEX (\S+))?")
NO_JOBS_RE = re.compile(r"No (unfinished )?job found", re.I)


# ---------- small helpers ----------

def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(text):
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def config_dir():
    return Path(os.path.expanduser(os.environ.get("ORCHESTACK_CONFIG_DIR") or "~/.config/orchestack"))


def state_dir():
    base = os.environ.get("ORCHESTACK_STATE_DIR")
    if base:
        return Path(os.path.expanduser(base))
    xdg = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(os.path.expanduser(xdg)) / "orchestack"


def run_cmd(cmd, timeout, stdin=None):
    """Run a configured command: a list is run as is, a string through a login shell. -> (rc or None, out, err)."""
    argv = list(cmd) if isinstance(cmd, list) else ["bash", "-lc", str(cmd)]
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout} s"
    except OSError as e:
        return None, "", str(e)


def short(text, n=70):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def tzinfo(name):
    if not name:
        return datetime.now().astimezone().tzinfo
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def hhmm(text):
    h, m = str(text).split(":")
    return int(h) * 60 + int(m)


def last_weekly(now, spec, tz):
    """Most recent '<Weekday> HH:MM' (e.g. 'Wed 20:00') at or before now, in tz."""
    day, at = spec.split()
    target = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(day[:3].lower())
    local = now.astimezone(tz)
    minutes = hhmm(at)
    cand = local.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    cand -= timedelta(days=(local.weekday() - target) % 7)
    if cand > local:
        cand -= timedelta(days=7)
    return cand


# ---------- context ----------

class Ctx:
    def __init__(self, now, cfg, state, dry, verbose, baseline=True):
        self.now, self.cfg, self.state, self.dry, self.verbose = now, cfg, state, dry, verbose
        self.baseline = baseline
        self.notes = []
        self._db = None
        self.work, self.first, self.deadline, self.budget = {}, False, None, 2.0
        self.tz = tzinfo(cfg.get("timezone"))

    def section(self, name):
        sec = self.cfg.get(name)
        return sec if isinstance(sec, dict) else {}

    def note(self, text):
        self.notes.append(text)

    def check_state(self, name):
        """(working copy of a check's state, baseline flag). main() keeps the copy only if the check succeeds, and
        a check counts as initialised only after one successful run. The first run records without waking."""
        return self.work, self.first

    def wake(self, thread, check, text, urgent=False, prompt=None, line=None, recheck=None):
        if not thread or not THREAD_ID_RE.match(str(thread)):
            self.note(f"{check}: no valid thread to wake for: {text}")
            return
        self.state.setdefault("outbox", []).append({
            "thread": thread, "line": line or f"watcher: {check}: {text}", "urgent": bool(urgent),
            "prompt": prompt, "created": iso(self.now), "attempts": 0,
            **({"recheck": recheck} if recheck else {}),
        })

    @property
    def orchestrator(self):
        return self.cfg.get("orchestrator")

    def t3_home(self):
        home = self.cfg.get("t3_home")
        return os.path.expanduser(home) if home else None

    def db(self):
        if self._db is None:
            path = self.cfg.get("t3_db")
            if not path and self.t3_home():
                path = os.path.join(self.t3_home(), "userdata", "statev2.sqlite")
            if not path:
                raise RuntimeError("no T3 database configured (watcher.t3_home or watcher.t3_db)")
            path = os.path.expanduser(path)
            if not os.path.isfile(path):
                raise RuntimeError(f"no T3 database at {path}")
            self._db = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5)
            self._db.execute("PRAGMA query_only = 1")
            self._db.set_progress_handler(
                lambda: 1 if self.deadline is not None and time.monotonic() > self.deadline else 0, 1000)
        return self._db

    def restart_budget(self):
        """A check that waited on a slow command gets a fresh database budget for the queries after it."""
        if self.deadline is not None:
            self.deadline = time.monotonic() + self.budget

    def require_plans(self, check):
        """Raise if any of the check's queries would scan a big table (see PLANNED)."""
        db = self.db()
        partial = {name for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index'")
                   if sql and " where " in " ".join(sql.lower().split())}
        for query, params in PLANNED.get(check, []):
            for row in db.execute("EXPLAIN QUERY PLAN " + query, params).fetchall():
                m = SCAN_RE.match(str(row[-1]))
                if m and m.group(1) not in SCAN_OK and m.group(2) not in partial:
                    raise RuntimeError(f"query plan would scan {m.group(1)} ({row[-1]}), skipped to avoid a full scan")

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def thread(self, thread_id):
        """(title, parent id) of a live thread, or None. Primary-key lookup."""
        row = self.db().execute(Q_THREAD, (thread_id,)).fetchone()
        if not row or row[2] or row[3]:
            return None
        return row[0], row[1]


def projects(ctx):
    return [Path(os.path.expanduser(p)) for p in ctx.cfg.get("projects") or []]


def prune(seen, now, active=(), days=KEEP_DAYS):
    """Forget resolved events (not in active) older than days. Active events are kept, so they never wake again."""
    cutoff = iso(now - timedelta(days=days))
    for key in [k for k, v in seen.items() if k not in active and (v.get("at") if isinstance(v, dict) else v) < cutoff]:
        del seen[key]


# ---------- checks ----------

def check_jobs(ctx):
    """Cluster jobs listed in job files ended (all of a file's jobs) or one failed. Wakes the file's owner thread."""
    sec = ctx.section("jobs")
    st, baseline = ctx.check_state("jobs")
    reported = st.setdefault("reported", {})
    seen_jobs = st.setdefault("seen_jobs", {})
    dirs = [p / ".work" / "orchestrator" / "jobs" for p in projects(ctx)]
    dirs += [Path(os.path.expanduser(d)) for d in sec.get("dirs") or []]
    files = sorted(f for d in dirs if d.is_dir() for f in d.glob("*.json"))
    groups = []
    too_old = (ctx.now - timedelta(days=KEEP_DAYS)).timestamp()
    for f in files:
        key = f"{f}:{f.stat().st_mtime_ns}"
        if baseline and f.stat().st_mtime < too_old:  # recovery run only: an old file is taken as handled
            reported[key] = {"ended": iso(ctx.now), "ignored": True}
            ctx.note(f"jobs: ignored {f.name}, older than {KEEP_DAYS} days (move it to jobs/done/)")
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            thread = str(data["thread"])
            jobs = [str(j) for j in (data.get("jobs") or [f.stem])]
        except Exception as e:
            ctx.note(f"jobs: skipped {f.name}: {e}")
            continue
        if reported.get(key, {}).get("ended"):
            continue
        groups.append((f, key, thread, jobs, str(data.get("next_step") or ""), f.stat().st_mtime,
                       str(data.get("parent") or "")))
    live = {f"{f}:{f.stat().st_mtime_ns}" for f in files}
    for key in [k for k in reported if k not in live]:
        del reported[key]
    prune(seen_jobs, ctx.now, active={j for g in groups for j in g[3]})
    if not groups:
        ctx.note(f"jobs: nothing open ({len(files)} job files)")
        return
    cmd = sec.get("command")
    if not cmd:
        ctx.note("jobs: no jobs.command configured")
        return
    rc, out, err = run_cmd(cmd, float(sec.get("timeout_s", 90)))
    no_jobs = bool(NO_JOBS_RE.search(out + err))  # LSF exits non-zero when it lists nothing
    if rc != 0 and not no_jobs:
        raise RuntimeError(f"cluster command failed ({rc}): {short(err, 120)}")
    done = {s.upper() for s in sec.get("done_states") or ["DONE"]}
    failed_states = {s.upper() for s in sec.get("failed_states") or ["EXIT"]}

    def norm(raw):  # one internal form: DONE, FAILED, or the scheduler's own word for a job still under way
        raw = str(raw or "").upper()
        return "FAILED" if raw in failed_states else "DONE" if raw in done else raw

    listed = parse_listing(out, norm)
    if not listed and not no_jobs:
        ctx.note("jobs: the cluster listed no jobs at all (empty or partial output?). Nothing decided.")
        return
    grace = timedelta(minutes=float(sec.get("missing_grace_minutes", 30)))
    terminal = ("DONE", "FAILED", "GONE")

    def absent(j, mtime):  # not listed now, not known to have ended, and seen before or past the grace period
        last = norm(seen_jobs.get(j, {}).get("state")) or None
        return j not in listed and last not in terminal and (
            last or ctx.now - datetime.fromtimestamp(mtime, UTC) > grace)

    missing = list(dict.fromkeys(j for g in groups for j in g[3] if absent(j, g[5])))
    if missing and sec.get("lookup_command"):  # jobs that dropped out of the listing: ask how they ended
        for j, got in lookup_jobs(ctx, sec, missing, norm).items():
            listed.setdefault(j, got)
    ctx.restart_budget()  # report_hint reads the database after the (slow) cluster calls
    for f, key, thread, jobs, next_step, mtime, parent in groups:
        states = {}
        for j in jobs:
            got = listed.get(j)
            last = norm(seen_jobs.get(j, {}).get("state")) or None
            if got:
                s = "FAILED" if "FAILED" in got else "DONE" if all(x == "DONE" for x in got) else next(
                    x for x in got if x != "DONE")  # an array with elements still running has not ended
                seen_jobs[j] = {"state": s, "at": iso(ctx.now)}
            elif last in terminal:
                s = last  # ended earlier, since dropped from the listing
            elif last or ctx.now - datetime.fromtimestamp(mtime, UTC) > grace:
                s = "GONE"  # the cluster answered but no longer (or never) lists it, and no lookup knew it: ended
                seen_jobs[j] = {"state": s, "at": iso(ctx.now)}  # decided once: not looked up again
            else:
                s = "UNLISTED"
            states[j] = s
        bad = [j for j, s in states.items() if s == "FAILED"]
        ended = all(s in terminal for s in states.values())
        rep = reported.setdefault(key, {})
        nxt = f" Next step: {short(next_step, 160)}" if next_step else ""
        counts = ", ".join(f"{s} {n}" for s, n in sorted(_count(states.values()).items()))
        if baseline and (bad or ended):
            rep.update(failed=iso(ctx.now), ended=iso(ctx.now) if ended else None)
            ctx.note(f"jobs: {f.name} -> {counts} (baseline, recorded without waking)")
            continue
        if bad and not rep.get("failed") and not ended:
            running = sum(1 for s in states.values() if s not in terminal)
            ctx.wake(thread, "jobs", f"{f.name}: job {', '.join(bad[:5])} failed ({running} still running).{nxt}"
                     f"{report_hint(ctx, thread, parent)}",
                     urgent=True)
            rep["failed"] = iso(ctx.now)
        if ended:
            gone = (" GONE means no longer listed and its exit status unknown: check the job's output."
                    if "GONE" in states.values() else "")
            ctx.wake(thread, "jobs", f"{f.name}: all {len(jobs)} jobs ended ({counts}).{gone}{nxt}"
                     f"{report_hint(ctx, thread, parent)}",
                     urgent=bool(bad) and not rep.get("failed"))
            rep["ended"] = iso(ctx.now)
        ctx.note(f"jobs: {f.name} -> {counts}")


JOB_ID_RE = re.compile(r"^\d+(\[\d+\])?$")
SAFE_JOB_RE = re.compile(r"^[A-Za-z0-9_.:\[\]-]{1,64}$")


def parse_listing(out, norm):
    """'<jobid> <state>' lines -> {base job id: [normalised state per line]} (array elements share their base id)."""
    listed = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and JOB_ID_RE.match(parts[0]):
            listed.setdefault(parts[0].split("[")[0], []).append(norm(parts[1]))
    return listed


def lookup_jobs(ctx, sec, ids, norm):
    """Ask the scheduler about jobs that dropped out of the listing (jobs.lookup_command, "{jobs}" = the ids, e.g.
    watcher/lsf_job_states.sh: bjobs -a, then bacct, then bhist). Same '<jobid> <state>' output. A failed or silent
    lookup returns what it could read: the rest count as GONE (ended, exit status unknown)."""
    ids = [j for j in ids if SAFE_JOB_RE.match(j)]
    if not ids:
        return {}
    cmd = sec["lookup_command"]
    if isinstance(cmd, list):
        argv = []
        for a in cmd:
            argv += ids if a == "{jobs}" else [str(a).replace("{jobs}", " ".join(shlex.quote(j) for j in ids))]
        cmd = argv
    else:
        cmd = str(cmd).replace("{jobs}", " ".join(shlex.quote(j) for j in ids))
    rc, out, err = run_cmd(cmd, float(sec.get("lookup_timeout_s", 180)))
    found = {j: got for j, got in parse_listing(out, norm).items() if j in ids}
    if rc != 0:
        ctx.note(f"jobs: lookup failed ({rc}): {short(err, 120)}; read {len(found)} of {len(ids)}")
    else:
        ctx.note(f"jobs: lookup of {len(ids)} unlisted jobs: " + (", ".join(
            f"{j} {'/'.join(v)}" for j, v in found.items()) or "none known"))
    return found


def report_hint(ctx, thread, parent=""):
    """The sentence a jobs wake adds for a child: T3 tells a parent only when a turn the delegate task started ends,
    so a turn this wake starts ends silently and the child must send its result itself. The parent comes from the
    job file's "parent" key, else from the thread's lineage. A top-level thread gets nothing."""
    how = ' with t3_thread_send (mode "queue"): T3 does not tell your parent when a turn started by this wake ends.'
    if parent and THREAD_ID_RE.match(parent):
        return f" Send your done:/failed:/handoff:/decision: to your parent {parent}{how}"
    if thread == ctx.orchestrator:
        return ""
    try:
        info = ctx.thread(thread)
    except Exception:  # no database or an unreadable one: say it without the id
        info = None
    else:
        if info is None or not info[1]:
            return ""  # a top-level thread (or one T3 no longer has): no parent to report to
        if THREAD_ID_RE.match(str(info[1])):
            return f" Send your done:/failed:/handoff:/decision: to your parent {info[1]}{how}"
    return f" If you are a child, send your done:/failed:/handoff:/decision: to your parent{how}"


def _count(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def check_children(ctx):
    """A T3 thread failed, waits on an approval or input, or runs too long. Wakes its parent thread."""
    sec = ctx.section("children")
    st, baseline = ctx.check_state("children")
    seen = st.setdefault("seen", {})
    ctx.require_plans("children")
    db = ctx.db()
    failed = set(sec.get("failed_statuses") or ["failed"])
    stuck_statuses = set(sec.get("stuck_statuses") or ["preparing", "starting", "running"])
    stuck_after = timedelta(hours=float(sec.get("stuck_hours", 3)))
    approval_after = timedelta(minutes=float(sec.get("approval_minutes", 10)))
    since = iso(ctx.now - timedelta(hours=float(sec.get("lookback_hours", 48))))
    events = []  # key, thread, what, urgent, when
    for (pid,) in db.execute(Q_PROJECTS).fetchall():
        for (tid,) in db.execute(Q_RECENT_THREADS, (pid, since)).fetchall():
            run = db.execute(Q_LATEST_RUN, (tid,)).fetchone()
            if run and run[1] in failed:
                events.append((f"run:{run[0]}", tid, f"run {run[1]}", True, run[3] or run[2]))
    for run_id, tid, status, requested, started in db.execute(Q_ACTIVE_RUNS).fetchall():
        if status in stuck_statuses and ctx.now - parse_iso(started or requested) > stuck_after:
            hours = (ctx.now - parse_iso(started or requested)).total_seconds() / 3600
            events.append((f"stuck:{run_id}", tid, f"{status} for {hours:.1f} h without ending", False, None))
    for rid, tid, kind, created in db.execute(Q_PENDING_REQUESTS).fetchall():
        age = ctx.now - parse_iso(created)
        if age > approval_after:
            what = "waiting on an approval" if kind != "user_input" else "waiting on an answer"
            events.append((f"req:{rid}", tid, f"{what} for {int(age.total_seconds() // 60)} min", False, None))
    prune(seen, ctx.now, active={e[0] for e in events})
    for key, tid, what, urgent, when in events:
        if key in seen:
            continue
        info = ctx.thread(tid)
        if baseline or not info or not info[1] or not ctx.thread(info[1]):
            seen[key] = iso(ctx.now)
            continue
        title, parent = info
        if when:
            if db.execute(Q_BUSY, (parent,)).fetchone():
                ctx.note(f"children: {short(title, 40)} {what}, parent busy: retried next run")
                continue  # not marked seen: decided once the parent is idle
            prun = db.execute(Q_LATEST_RUN, (parent,)).fetchone()
            if prun and prun[2] > when:
                seen[key] = iso(ctx.now)
                ctx.note(f"children: {short(title, 40)} {what}, parent already ran after it")
                continue
        seen[key] = iso(ctx.now)
        reason = key.split(":")[0]
        reason = {"run": "failed", "req": "approval"}.get(reason, reason)
        ctx.note(f"children: {short(title, 40)} ({tid}) {what} -> wake {parent}")
        ctx.wake(parent, "children", f'{reason}: "{short(title, 60)}" ({tid}) {what}.', urgent=urgent)
    ctx.note(f"children: {len(events)} open events" + (" (baseline, recorded without waking)" if baseline else ""))


def check_disk(ctx):
    """Free disk below the floor. Wakes the orchestrator at once, again only after it recovered."""
    sec = ctx.section("disk")
    st, _ = ctx.check_state("disk")
    low = st.setdefault("low", {})
    floor = float(sec.get("min_free_gb", 20))
    rearm = floor + max(1.0, 0.05 * floor)
    for path in sec.get("paths") or ["/"]:
        path = os.path.expanduser(path)
        free = shutil.disk_usage(path).free / 1e9
        was = low.get(path, False)
        if free < floor and not was:
            ctx.wake(ctx.orchestrator, "disk", f"{path} has {free:.1f} GB free, below the {floor:g} GB floor.",
                     urgent=True)
            low[path] = True
        elif was and free >= rearm:
            low[path] = False
        ctx.note(f"disk: {path} {free:.1f} GB free (floor {floor:g})")


def check_usage(ctx):
    """Weekly reset happened, or usage far behind pace. Wakes the orchestrator with the backlog prompt."""
    sec = ctx.section("usage")
    st, baseline = ctx.check_state("usage")
    cmd = sec.get("command")
    if not cmd:
        ctx.note("usage: no usage.command configured")
        return
    rc, out, err = run_cmd(cmd, float(sec.get("timeout_s", 60)))
    ideas = sec.get("ideas_file") or (str(projects(ctx)[0] / ".work/orchestrator/IDEAS.md") if projects(ctx)
                                      else ".work/orchestrator/IDEAS.md")
    prompt = (sec.get("backlog_prompt") or BACKLOG_PROMPT).replace("{ideas}", ideas)
    # The section itself describes the first account. Each entry of "accounts" is a further account read from the
    # same meter output: its keys override the section's (name, weekly_pattern, weekly_reset, pace_per_day, ...).
    base = {k: v for k, v in sec.items() if k != "accounts"}
    accounts = sec.get("accounts") or []
    if not isinstance(accounts, list):
        accounts = [accounts]
    if accounts and not sec.get("weekly_pattern"):  # the default pattern could read another account's line
        raise ValueError("usage.weekly_pattern is required when usage.accounts is set")
    usage_account(ctx, base, st, baseline, rc, out, err, prompt, None)
    extra = st.setdefault("accounts", {}) if accounts else {}
    health = ctx.state.setdefault("health", {})
    alert_after = timedelta(minutes=float(ctx.cfg.get("fail_alert_minutes", 120)))
    seen, labels = set(), set()
    for i, acc in enumerate(accounts):
        name = acc.get("name") if isinstance(acc, dict) else None
        label = name if isinstance(name, str) and name and name not in seen else f"#{i + 1}"
        labels.add(f"usage:{label}")
        mark = len(ctx.state.setdefault("outbox", []))
        try:  # a broken further account never blocks the first one's state or wake
            if not isinstance(acc, dict) or not isinstance(name, str) or not name:
                raise ValueError("each accounts entry needs a non-empty string name")
            if name in seen:
                raise ValueError("duplicate account name")
            seen.add(name)
            if not acc.get("weekly_pattern"):
                raise ValueError("each accounts entry needs its own weekly_pattern")
            sub = copy.deepcopy(extra.get(name) or {})
            usage_account(ctx, {**base, **acc}, sub, baseline or "pct" not in sub, rc, out, err, prompt, name)
            extra[name] = sub
            health.pop(f"usage:{label}", None)
        except Exception as e:
            del ctx.state["outbox"][mark:]
            msg = f"usage {label}: error: {short(e, 160)}"
            ctx.note(msg)
            print(f"watcher: {msg}", file=sys.stderr)
            h = health.setdefault(f"usage:{label}", {"since": iso(ctx.now)})
            h["error"] = msg
            if not h.get("alerted") and ctx.now - parse_iso(h["since"]) >= alert_after:
                ctx.wake(ctx.orchestrator, "usage", "", line=f"watcher: {msg} (failing since {h['since']})")
                h["alerted"] = iso(ctx.now)
    for gone in [k for k in extra if k not in seen]:  # a removed or renamed account starts afresh if it returns
        del extra[gone]
    for gone in [k for k in health if k.startswith("usage:") and k not in labels]:
        del health[gone]
    if not accounts:
        st.pop("accounts", None)


def usage_account(ctx, sec, st, baseline, rc, out, err, prompt, name):
    label = f"[{name}] " if name else ""
    pattern = sec.get("weekly_pattern") or r"(?:week|seven)\D*?([0-9]+(?:\.[0-9]+)?)\s*%"
    m = re.search(pattern, out, re.I | re.M) if rc == 0 else None
    if not m:
        raise RuntimeError(f"no weekly percentage read (rc {rc}): {short(err or out, 120)}")
    pct = float(m.group(1))
    prev = st.get("pct")
    cycle = last_weekly(ctx.now, sec["weekly_reset"], ctx.tz) if sec.get("weekly_reset") else None
    cycle_key = iso(cycle) if cycle else None
    new_cycle = bool(cycle_key and st.get("cycle") and st["cycle"] != cycle_key)
    reset = prev is not None and (prev - pct >= float(sec.get("reset_drop", 20)) or (new_cycle and pct < prev))
    recent_reset = st.get("reset_at") and ctx.now - parse_iso(st["reset_at"]) < timedelta(days=5)
    if reset and not recent_reset:
        ctx.wake(ctx.orchestrator, "usage", f"{label}weekly usage reset ({pct:.0f} % used now, was {prev:.0f} %).",
                 prompt=prompt)
        st["reset_at"] = st["behind_at"] = iso(ctx.now)
    elif cycle:
        days = (ctx.now - cycle).total_seconds() / 86400
        expected = min(100.0, float(sec.get("pace_per_day", 15)) * days)
        behind = expected - pct >= float(sec.get("behind_by", 20))
        rearm = timedelta(hours=float(sec.get("rearm_hours", 24)))
        if not behind:
            st["behind_at"] = None
        elif baseline:
            st["behind_at"] = iso(ctx.now)  # first run: recorded, the next wake comes after rearm_hours
        elif not st.get("behind_at") or ctx.now - parse_iso(st["behind_at"]) >= rearm:
            ctx.wake(ctx.orchestrator, "usage",
                     f"{label}far behind pace: {pct:.0f} % of the week used, pace says {expected:.0f} % by now.",
                     prompt=prompt)
            st["behind_at"] = iso(ctx.now)
        ctx.note(f"usage{' ' + name if name else ''}: {pct:.0f} % used, pace {expected:.0f} % "
                 f"({days:.1f} days into the week)")
    st["pct"] = pct
    if reset or not new_cycle:  # a new cycle counts once the meter has dropped
        st["cycle"] = cycle_key


def check_decision(ctx):
    """A decision: message to an orchestrator sat unanswered while the chat was idle. Wakes that chat."""
    sec = ctx.section("decision")
    st, baseline = ctx.check_state("decision")
    seen = st.setdefault("seen", {})
    prune(seen, ctx.now)  # messages older than 2 days are never looked at again, so age alone is safe here
    ctx.require_plans("decision")
    db = ctx.db()
    after = timedelta(minutes=float(sec.get("minutes", 30)))
    oldest = iso(ctx.now - timedelta(days=2))
    for th in sec.get("threads") or [ctx.orchestrator]:
        if not th or db.execute(Q_BUSY, (th,)).fetchone():
            continue
        for mid, created, text in db.execute(Q_LATEST_USER_MESSAGES, (th,)).fetchall():
            text = text or ""
            if mid in seen or created < oldest or text.startswith("watcher:") or not DECISION_RE.search(text):
                continue
            age = ctx.now - parse_iso(created)
            if age < after:
                continue
            seen[mid] = iso(ctx.now)
            if baseline or db.execute(Q_ANSWERED_AFTER, (th, created)).fetchone():
                continue
            line = DECISION_RE.search(text)
            snippet = short(text[line.start():].strip(), 90)
            ctx.wake(th, "decision", f"unanswered for {int(age.total_seconds() // 60)} min: {snippet}")
    if sec.get("children", True):
        check_child_decisions(ctx, sec, st, after)
    ctx.note("decision: checked" + (" (baseline, recorded without waking)" if baseline else ""))


def relay_tokens(thread_id):
    """What a T3 completion relay in the parent quotes of a child: its last UUID (the delegate task id). The relay
    URL-encodes the id once or several times, so a child without a UUID is matched in its plain and encoded forms."""
    found = UUID_RE.findall(str(thread_id))
    if found:
        return [found[-1].lower()]
    once = quote(str(thread_id), safe="")
    return list(dict.fromkeys(t.lower() for t in (str(thread_id), once, quote(once, safe=""))))


def turn_start(db, child, created):
    """When the child's turn that wrote a message at `created` started: the latest run started by then."""
    for requested, started in db.execute(Q_RECENT_RUNS, (child,)).fetchall():
        start = started or requested
        if start and start <= created:
            return start
    return created


def child_decision_relayed(db, parent, child, created, since=None):
    """The parent has the child's message: a T3 relay after it, or a message the child sent the parent itself with
    t3_thread_send (T3 records the sender in the payload's senderThreadId): any such send at or after the child's
    message, or a report (decision/done/failed/handoff, optionally with a subject before the colon) sent earlier in
    that turn (since `since`, the turn's start). A turn the parent started with its own send does not count: whether
    the parent read the answer is unknown, and a spare wake costs less than a lost report."""
    since = min(since or created, created)
    for at, text in db.execute(Q_SENT_BY, (parent, since, str(child))).fetchall():
        if (at or "") >= created or SENT_REPORT_RE.match(text or ""):
            return True
    return any(db.execute(Q_RELAYED, (parent, created, t)).fetchone() for t in relay_tokens(child))


def child_decision_open(db, item):
    """A held child-decision wake is still due: the decision is still the child's last message and no relay came."""
    r = item["recheck"]
    row = db.execute(Q_LATEST_MESSAGE, (r["child"],)).fetchone()
    return bool(row) and row[0] == r["message"] and not child_decision_relayed(db, r["parent"], r["child"],
                                                                                r["created"],
                                                                                r.get("since") or turn_start(
                                                                                    db, r["child"], r["created"]))


def check_child_decisions(ctx, sec, st, after):
    """A child ended its turn with a decision:, done:, failed: or handoff: that its parent never got. T3 relays a
    delegate's completion to the parent only for turns the delegate task started; a turn started by an outside thread
    send (a watcher jobs wake, say) ends silently. Wakes the parent once per message, after the child sat idle for
    `minutes`, unless the child sent the parent a message itself during that turn. Only a last message that starts
    with one of these words counts (anything else is progress, not a report)."""
    db = ctx.db()
    # The first run of this part records without waking, also where the decision check ran before it existed, and
    # the first run that knows done:/failed:/handoff: records those (state from before they counted).
    baseline = ctx.first or (ctx.baseline and "child_seen" not in st)
    results_baseline = baseline or (ctx.baseline and not st.get("child_results"))
    st["child_results"] = True
    seen = st.setdefault("child_seen", {})
    prune(seen, ctx.now)
    since = iso(ctx.now - timedelta(hours=float(sec.get("child_lookback_hours", 48))))
    for (pid,) in db.execute(Q_PROJECTS).fetchall():
        for (tid,) in db.execute(Q_RECENT_THREADS, (pid, since)).fetchall():
            row = db.execute(Q_LATEST_MESSAGE, (tid,)).fetchone()
            if not row:
                continue
            mid, role, streaming, created, text = row
            text = text or ""
            m = RESULT_RE.match(text)
            if mid in seen or role != "assistant" or streaming or created < since or not m:
                continue
            kind = m.group(1).lower()
            if baseline or (results_baseline and kind != "decision"):  # every open one, of any age, is handled
                seen[mid] = iso(ctx.now)
                continue
            age = ctx.now - parse_iso(created)
            if age < after or db.execute(Q_BUSY, (tid,)).fetchone():
                continue
            info = ctx.thread(tid)
            if not info or not info[1] or not ctx.thread(info[1]):
                continue  # not a child, or its parent is gone: nobody to wake
            title, parent = info
            if db.execute(Q_BUSY, (parent,)).fetchone():
                continue  # not marked seen: T3 may write the relay when the parent's turn ends
            seen[mid] = iso(ctx.now)
            turn = turn_start(db, tid, created)
            if child_decision_relayed(db, parent, tid, created, turn):
                continue
            snippet = short(text.strip(), 90)
            what = "a decision" if kind == "decision" else f"a {kind}: report"
            ctx.note(f"decision: child {short(title, 40)} ({tid}) {kind} not relayed -> wake {parent}")
            ctx.wake(parent, "decision", f'child "{short(title, 60)}" ({tid}) ended with {what} '
                                         f"{int(age.total_seconds() // 60)} min ago that T3 did not relay to you: "
                                         f"{snippet}",
                     recheck={"child": tid, "message": mid, "parent": parent, "created": created, "since": turn})


DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?\s*$", re.I)


def parse_validity(text, now, tz):
    """The end of a validity read by expiry.pattern: a time ('2026-11-03 20:10:49 -0500', ISO, or naive in
    `timezone`) or a duration left ('7h59m0s', '45m', '2d3h'). -> aware datetime."""
    text = " ".join(str(text).split())
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M %z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        pass
    m = DURATION_RE.match(text)
    if m and any(m.groups()):
        d, h, mi, se = (int(x or 0) for x in m.groups())
        return now + timedelta(days=d, hours=h, minutes=mi, seconds=se)
    raise ValueError(f"cannot read a time or duration from {short(text, 60)!r}")


def check_expiry(ctx):
    """A login runs out soon (e.g. `tsh status`, "Valid until"). Off unless configured: `command` prints the
    validity, `pattern` (a regex) captures its end time or the duration left in group 1. Wakes the orchestrator (or
    `thread`) once per expiry time when less than `warn_minutes` (60) are left, or when the pattern no longer
    matches (logged out). A renewal brings a new expiry time, and with it a new wake later. Times within
    5 min of the one already reported count as the same login (a duration read each run drifts by seconds)."""
    sec = ctx.section("expiry")
    st, _ = ctx.check_state("expiry")
    cmd, pattern = sec.get("command"), sec.get("pattern")
    if not cmd or not pattern:
        raise ValueError("expiry needs command and pattern")
    name = sec.get("name") or "login"
    rc, out, err = run_cmd(cmd, float(sec.get("timeout_s", 30)))
    if rc is None:
        raise RuntimeError(f"expiry command failed: {short(err, 120)}")
    m = re.search(pattern, out + "\n" + err, re.I | re.M)
    warn = timedelta(minutes=float(sec.get("warn_minutes", 60)))
    hint = f" {short(sec['hint'], 160)}" if sec.get("hint") else ""
    if not m:
        key, text = "unreadable", f"{name}: no validity found (rc {rc}), logged out?{hint}"
        left = None
    else:
        until = parse_validity(m.group(1), ctx.now, ctx.tz)
        left = until - ctx.now
        key = iso(until)
        when = until.astimezone(ctx.tz).strftime("%Y-%m-%d %H:%M")
        mins = int(left.total_seconds() // 60)
        text = (f"{name} expired at {when}.{hint}" if left <= timedelta(0)
                else f"{name} valid for {mins} min more (until {when}).{hint}")
    ctx.note(f"expiry: {name} " + (f"{int(left.total_seconds() // 60)} min left" if left is not None
                                   else "no validity found"))
    if left is not None and left >= warn:
        st.pop("warned", None)
        return
    warned = st.get("warned")
    if warned == key or (left is not None and warned not in (None, "unreadable")
                         and abs(parse_iso(warned) - until) <= timedelta(minutes=5)):
        return
    st["warned"] = key
    ctx.wake(sec.get("thread") or ctx.orchestrator, "expiry", text, urgent=True)


def check_hosts(ctx):
    """A host became reachable: run its copy command. Never wakes a chat."""
    st, _ = ctx.check_state("hosts")
    for h in ctx.cfg.get("hosts") or []:
        name = h.get("name") or "host"
        rc, _, _ = run_cmd(h["probe"], float(h.get("probe_timeout_s", 30)))
        online = rc == 0
        was = st.get(name)
        st[name] = "online" if online else "offline"
        if online and was == "offline" and h.get("on_online"):
            if ctx.dry:
                ctx.note(f"hosts: {name} came online, would run its on_online command")
            else:
                rc, _, err = run_cmd(h["on_online"], float(h.get("timeout_s", 600)))
                ctx.note(f"hosts: {name} came online, on_online exit {rc} {short(err, 80)}")
        else:
            ctx.note(f"hosts: {name} {st[name]}")


CHECKS = (("jobs", check_jobs), ("children", check_children), ("disk", check_disk), ("usage", check_usage),
          ("decision", check_decision), ("expiry", check_expiry), ("hosts", check_hosts))


# ---------- delivery ----------

def in_quiet(ctx):
    q = ctx.cfg.get("quiet_hours")
    if not q:
        return False
    t = ctx.now.astimezone(ctx.tz)
    m, start, end = t.hour * 60 + t.minute, hhmm(q["start"]), hhmm(q["end"])
    return start <= m < end if start <= end else (m >= start or m < end)


def t3_bin(ctx):
    wake = ctx.section("wake")
    if wake.get("t3_bin"):
        return os.path.expanduser(wake["t3_bin"])
    home = ctx.t3_home()
    if home:
        try:
            with open(os.path.join(home, "runtime", "service-state.json"), encoding="utf-8") as f:
                version = json.load(f)["activeVersion"]
            path = os.path.join(home, "runtime", "versions", version, "t3")
            if os.path.isfile(path):
                return path
        except Exception:
            pass
    return shutil.which("t3") or "t3"


def send(ctx, thread, text):
    """Deliver one message. -> (ok, how, error). Never logs the webhook URL (it holds a token)."""
    wake = ctx.section("wake")
    timeout = float(wake.get("timeout_s", 60))
    url = (wake.get("webhooks") or {}).get(thread)
    if url and ctx.dry:
        return True, "would POST to its webhook", ""
    if url:
        body = json.dumps({"text": text, "thread": thread}).encode()
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200 <= r.status < 300, "webhook", f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return False, "webhook", f"HTTP {e.code}"
        except Exception as e:  # its message may quote the URL, and the URL holds a token: report only the kind
            return False, "webhook", f"{type(e).__name__} (details hidden, the URL holds a token)"
    if wake.get("command"):
        argv = [str(a).replace("{thread}", thread) for a in wake["command"]]
    else:
        base = ["--base-dir", ctx.t3_home()] if ctx.t3_home() else []
        argv = [t3_bin(ctx), "thread", "send", *base, "--", thread, "-"]
    if ctx.dry:
        return True, "would run " + " ".join(argv[:3] + ["...", "--", thread, "-"]), ""
    rc, _, err = run_cmd(argv, timeout, stdin=text)
    return rc == 0, "t3 thread send", short(err, 120)


def deliver(ctx):
    """Send the outbox, one merged message per thread. Undelivered wakes retry with a backoff (15 min doubling, at
    most 1 h) and are dropped, loudly, only when OUTBOX_MAX_HOURS old."""
    outbox = ctx.state.setdefault("outbox", [])
    sent = ctx.state.setdefault("sent", {})
    rate = timedelta(minutes=float(ctx.cfg.get("rate_limit_minutes", 60)))
    quiet = in_quiet(ctx)
    keep, lines_out = [], []
    by_thread = {}
    expired = iso(ctx.now - timedelta(hours=OUTBOX_MAX_HOURS))
    for item in outbox:
        if item.get("recheck") and item["created"] < iso(ctx.now):  # held from an earlier run: still due?
            try:
                due = child_decision_open(ctx.db(), item)
            except Exception as e:  # cannot tell: keep it
                due = True
                lines_out.append(f"recheck failed for {item['thread']}: {short(e, 80)}")
            if not due:
                lines_out.append(f"dropped for {item['thread']}, answered or relayed meanwhile: {item['line']}")
                continue
        if item["created"] < expired:
            kind = "URGENT wake" if item["urgent"] else "wake"
            msg = f"watcher: DROPPED {kind} for {item['thread']}, undelivered for {OUTBOX_MAX_HOURS} h: {item['line']}"
            print(msg, file=sys.stderr)
            lines_out.append(msg)
            continue
        by_thread.setdefault(item["thread"], []).append(item)
    for thread, items in by_thread.items():
        retry_at = max((i.get("retry_at") or "") for i in items)
        fresh_urgent = any(i["urgent"] and not i.get("retry_at") for i in items)
        if retry_at and iso(ctx.now) < retry_at and not fresh_urgent:
            lines_out.append(f"backing off for {thread} until {retry_at}: " + " | ".join(i["line"] for i in items))
            keep += items
            continue
        urgent = any(i["urgent"] for i in items)
        last = sent.get(thread)
        limited = bool(last) and ctx.now - parse_iso(last) < rate
        if not urgent and (limited or quiet):
            why = "quiet hours" if quiet else f"rate limit (last wake {last})"
            lines_out.append(f"held for {thread} ({why}): " + " | ".join(i["line"] for i in items))
            keep += items
            continue
        lines = list(dict.fromkeys(i["line"] for i in items))
        prompts = list(dict.fromkeys(i["prompt"] for i in items if i.get("prompt")))
        text = "\n".join(lines) + ("\n\n" + "\n\n".join(prompts) if prompts else "")
        try:
            ok, how, err = send(ctx, thread, text)
        except Exception as e:  # one thread's failure must not stop the others or the state save
            ok, how, err = False, "send", short(e, 120)
        if ok:
            sent[thread] = iso(ctx.now)
            verb = "would wake" if ctx.dry else "woke"
            lines_out.append(f"{verb} {thread} via {how}:\n  " + text.replace("\n", "\n  "))
        else:
            attempts = max(i.get("attempts", 0) for i in items) + 1
            retry = iso(ctx.now + timedelta(minutes=min(60, 15 * 2 ** (attempts - 1))))
            for i in items:
                i["attempts"], i["retry_at"] = attempts, retry
            msg = f"FAILED to wake {thread} via {how} (attempt {attempts}, retry after {retry}): {err}"
            if urgent:
                print("watcher: " + msg, file=sys.stderr)
            lines_out.append(msg)
            keep += items
    ctx.state["outbox"] = keep
    cutoff = iso(ctx.now - timedelta(days=KEEP_DAYS))
    ctx.state["sent"] = {t: v for t, v in sent.items() if v >= cutoff}
    return lines_out


# ---------- main ----------

def load_config():
    path = config_dir() / "config.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    sec = data.get("watcher") if isinstance(data, dict) else None
    return sec if isinstance(sec, dict) else {}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="orchestack watcher: wake a chat only when something changed.")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every check's observations")
    ap.add_argument("--now", help="pretend the time is this ISO time (tests)")
    ap.add_argument("--no-baseline", action="store_true",
                    help="treat checks without state as seen before (with --dry-run: what a later run would send)")
    args = ap.parse_args(argv)
    now = parse_iso(args.now) if args.now else datetime.now(UTC)
    try:
        cfg = load_config()
    except Exception as e:
        print(f"watcher: cannot read config: {e}", file=sys.stderr)
        return 1
    if not cfg or cfg.get("enabled") is False:
        print("watcher: disabled (no watcher section in config.json, or enabled is false)")
        return 0
    sdir = state_dir()
    lock = None
    if not args.dry_run:
        sdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = open(sdir / "watch.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("watcher: another run is still going, skipped")
            return 0
    spath = sdir / "watch.json"
    try:
        state = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else {}
    except Exception as e:
        print(f"watcher: state unreadable ({e}), starting fresh", file=sys.stderr)
        state = {}
    ctx = Ctx(now, cfg, state, args.dry_run, args.verbose, baseline=not args.no_baseline)
    if "initialised" not in state:  # state from before this field: checks with state count as initialised
        state["initialised"] = [n for n, _ in CHECKS if n in state]
    initialised = state["initialised"]
    health = state.setdefault("health", {})
    budget = ctx.budget = float(cfg.get("db_budget_ms", 2000)) / 1000
    alert_after = timedelta(minutes=float(cfg.get("fail_alert_minutes", 120)))
    for name, fn in CHECKS:
        sec = cfg.get(name)
        if sec is None or (isinstance(sec, dict) and sec.get("enabled") is False):
            continue
        ctx.work = copy.deepcopy(state.get(name) or {})
        ctx.first = ctx.baseline and name not in initialised
        mark = len(state.setdefault("outbox", []))
        ctx.deadline = time.monotonic() + budget
        try:
            fn(ctx)
        except Exception as e:
            del state["outbox"][mark:]  # a failed check decides nothing: its state and wakes are discarded
            err = short(e, 160)
            ctx.note(f"{name}: error: {err}")
            print(f"watcher: {name}: error: {err}", file=sys.stderr)
            h = health.setdefault(name, {"since": iso(now)})
            h["error"] = err
            if not h.get("alerted") and now - parse_iso(h["since"]) >= alert_after:
                ctx.wake(ctx.orchestrator, name, "", line=f"watcher: {name} failing since {h['since']}: {err}")
                h["alerted"] = iso(now)
            continue
        finally:
            ctx.deadline = None
        state[name] = ctx.work
        if name not in initialised:
            initialised.append(name)
        health.pop(name, None)
    ctx.close()
    ctx.deadline = time.monotonic() + budget  # delivery rechecks held child-decision wakes in the database
    out = deliver(ctx)
    ctx.close()
    state["last_run"] = iso(now)
    if args.dry_run or args.verbose:
        for n in ctx.notes:
            print(f"  {n}")
    for line in out:
        print(line)
    if args.dry_run:
        print("dry run: no state written, no wake sent")
    else:
        save_state(spath, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())

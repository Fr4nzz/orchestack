#!/usr/bin/env python3
"""orchestack watcher: wake a chat only when something changed. No LLM calls.

A systemd --user timer (watcher/orchestack-watch.timer) runs this every 15 minutes. Each run performs the
configured checks, compares what it sees with the last run and sends one short message per change to the chat that
owns it. The same state never wakes anyone twice.

  jobs      cluster jobs named in <project>/.work/orchestrator/jobs/*.json ended or failed -> the owner thread
  children  a T3 child thread failed, waits on an approval or is stuck -> its parent thread
  disk      free disk below the floor -> the orchestrator (urgent)
  usage     the weekly usage reset happened, or usage is far behind pace -> the orchestrator, with the backlog prompt
  decision  a decision: message to an orchestrator sat unanswered for 30 min -> that orchestrator
  hosts     a host came online -> run its copy command (no wake)

Wakes go through the T3 CLI (`t3 thread send <thread> -`) or, for a thread that has one, a scheduled-task webhook
URL. At most one wake per thread per hour (disk and failures excepted). Wakes held by that limit or by quiet hours
stay in an outbox and go out merged with later ones.

  watch.py [--dry-run] [--verbose] [--no-baseline] [--now ISO-TIME]

--dry-run prints what it would do and changes nothing (no state written, no wake sent, no command run except the
read-only checks). Config: the "watcher" section of config.json in $ORCHESTACK_CONFIG_DIR or ~/.config/orchestack.
State: watch.json in $ORCHESTACK_STATE_DIR or ~/.local/state/orchestack. The T3 database is opened read-only and is
read by indexed columns only (it can be tens of GB). Exit 0 unless the config is unreadable.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

UTC = timezone.utc
ACTIVE = ("queued", "preparing", "starting", "running", "waiting")
DECISION_RE = re.compile(r"^\s*[*_>]*\s*decision:", re.I | re.M)
KEEP_DAYS = 14
MAX_ATTEMPTS = 5

BACKLOG_PROMPT = (
    "Budget is unused. Read {ideas} (format: orchestrator skill, Backlog). Pick the most valuable reversible,"
    " in-budget item the user would be proud to find started, run it through delegates, and report afterwards."
    " If the backlog is empty, look for ideas left unexecuted in the notes file and recent chats, propose 3 ideas"
    " grounded in the project's current state, add them to IDEAS.md and start the best reversible one."
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
Q_ACTIVE_RUNS = (
    "SELECT run_id, thread_id, status, requested_at, json_extract(payload_json, '$.startedAt')"
    " FROM orchestration_v2_projection_runs WHERE status IN ({})".format(",".join("?" * len(ACTIVE)))
)
Q_BUSY = (
    "SELECT 1 FROM orchestration_v2_projection_runs WHERE thread_id = ? AND status IN ({}) LIMIT 1"
    .format(",".join("?" * len(ACTIVE)))
)
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
        self.tz = tzinfo(cfg.get("timezone"))

    def section(self, name):
        sec = self.cfg.get(name)
        return sec if isinstance(sec, dict) else {}

    def note(self, text):
        self.notes.append(text)

    def check_state(self, name):
        """(state dict for a check, baseline flag). The first run of a check records what exists without waking."""
        baseline = self.baseline and name not in self.state
        return self.state.setdefault(name, {}), baseline

    def wake(self, thread, check, text, urgent=False, prompt=None):
        if not thread:
            self.note(f"{check}: no thread to wake for: {text}")
            return
        self.state.setdefault("outbox", []).append({
            "thread": thread, "line": f"watcher: {check}: {text}", "urgent": bool(urgent),
            "prompt": prompt, "created": iso(self.now), "attempts": 0,
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
        return self._db

    def close(self):
        if self._db is not None:
            self._db.close()

    def thread(self, thread_id):
        """(title, parent id) of a live thread, or None. Primary-key lookup."""
        row = self.db().execute(Q_THREAD, (thread_id,)).fetchone()
        if not row or row[2] or row[3]:
            return None
        return row[0], row[1]


def projects(ctx):
    return [Path(os.path.expanduser(p)) for p in ctx.cfg.get("projects") or []]


def prune(seen, now, days=KEEP_DAYS):
    cutoff = iso(now - timedelta(days=days))
    for key in [k for k, v in seen.items() if (v.get("at") if isinstance(v, dict) else v) < cutoff]:
        del seen[key]


# ---------- checks ----------

def check_jobs(ctx):
    """Cluster jobs listed in job files ended (all of a file's jobs) or one failed. Wakes the file's owner thread."""
    sec = ctx.section("jobs")
    st, _ = ctx.check_state("jobs")
    reported = st.setdefault("reported", {})
    seen_jobs = st.setdefault("seen_jobs", {})
    dirs = [p / ".work" / "orchestrator" / "jobs" for p in projects(ctx)]
    dirs += [Path(os.path.expanduser(d)) for d in sec.get("dirs") or []]
    files = sorted(f for d in dirs if d.is_dir() for f in d.glob("*.json"))
    groups = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            thread = str(data["thread"])
            jobs = [str(j) for j in (data.get("jobs") or [f.stem])]
        except Exception as e:
            ctx.note(f"jobs: skipped {f.name}: {e}")
            continue
        key = f"{f}:{f.stat().st_mtime_ns}"
        if reported.get(key, {}).get("ended"):
            continue
        groups.append((f, key, thread, jobs, str(data.get("next_step") or ""), f.stat().st_mtime))
    live = {f"{f}:{f.stat().st_mtime_ns}" for f in files}
    for key in [k for k in reported if k not in live]:
        del reported[key]
    prune(seen_jobs, ctx.now)
    if not groups:
        ctx.note(f"jobs: nothing open ({len(files)} job files)")
        return
    cmd = sec.get("command")
    if not cmd:
        ctx.note("jobs: no jobs.command configured")
        return
    rc, out, err = run_cmd(cmd, float(sec.get("timeout_s", 90)))
    if rc != 0:
        ctx.note(f"jobs: cluster command failed ({rc}): {short(err, 120)}. Nothing decided.")
        return
    listed = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.match(r"^\d+(\[\d+\])?$", parts[0]):
            listed.setdefault(parts[0].split("[")[0], []).append(parts[1].upper())
    done = {s.upper() for s in sec.get("done_states") or ["DONE"]}
    failed_states = {s.upper() for s in sec.get("failed_states") or ["EXIT"]}
    grace = timedelta(minutes=float(sec.get("missing_grace_minutes", 30)))
    for f, key, thread, jobs, next_step, mtime in groups:
        states = {}
        for j in jobs:
            got = listed.get(j)
            if got:
                s = next((x for x in got if x in failed_states), None)
                s = s or ("DONE" if all(x in done for x in got) else got[0])
                seen_jobs[j] = {"state": s, "at": iso(ctx.now)}
            elif j in seen_jobs or ctx.now - datetime.fromtimestamp(mtime, UTC) > grace:
                s = "GONE"  # no longer (or never) listed: the scheduler forgot it
            else:
                s = "UNLISTED"
            states[j] = s
        bad = [j for j, s in states.items() if s in failed_states]
        ended = all(s in done or s in failed_states or s == "GONE" for s in states.values())
        rep = reported.setdefault(key, {})
        nxt = f" Next step: {short(next_step, 160)}" if next_step else ""
        counts = ", ".join(f"{s} {n}" for s, n in sorted(_count(states.values()).items()))
        if bad and not rep.get("failed") and not ended:
            running = sum(1 for s in states.values() if s not in done and s not in failed_states and s != "GONE")
            ctx.wake(thread, "jobs", f"{f.name}: job {', '.join(bad[:5])} failed ({running} still running).{nxt}",
                     urgent=True)
            rep["failed"] = iso(ctx.now)
        if ended:
            gone = " GONE means no longer listed: check the job's output." if "GONE" in states.values() else ""
            ctx.wake(thread, "jobs", f"{f.name}: all {len(jobs)} jobs ended ({counts}).{gone}{nxt}",
                     urgent=bool(bad) and not rep.get("failed"))
            rep["ended"] = iso(ctx.now)
        ctx.note(f"jobs: {f.name} -> {counts}")


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
    prune(seen, ctx.now)
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
    for run_id, tid, status, requested, started in db.execute(Q_ACTIVE_RUNS, ACTIVE).fetchall():
        if status in stuck_statuses and ctx.now - parse_iso(started or requested) > stuck_after:
            hours = (ctx.now - parse_iso(started or requested)).total_seconds() / 3600
            events.append((f"stuck:{run_id}", tid, f"{status} for {hours:.1f} h without ending", False, None))
    for rid, tid, kind, created in db.execute(Q_PENDING_REQUESTS).fetchall():
        age = ctx.now - parse_iso(created)
        if age > approval_after:
            what = "waiting on an approval" if kind != "user_input" else "waiting on an answer"
            events.append((f"req:{rid}", tid, f"{what} for {int(age.total_seconds() // 60)} min", False, None))
    for key, tid, what, urgent, when in events:
        if key in seen:
            continue
        seen[key] = iso(ctx.now)
        info = ctx.thread(tid)
        if baseline or not info or not info[1]:
            continue
        title, parent = info
        if not ctx.thread(parent):
            continue
        if when:
            prun = db.execute(Q_LATEST_RUN, (parent,)).fetchone()
            if prun and prun[2] > when:
                ctx.note(f"children: {short(title, 40)} {what}, parent already ran after it")
                continue
        ctx.note(f"children: {short(title, 40)} ({tid}) {what} -> wake {parent}")
        ctx.wake(parent, "children", f'failed: "{short(title, 60)}" ({tid}) {what}.', urgent=urgent)
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
    st, _ = ctx.check_state("usage")
    cmd = sec.get("command")
    if not cmd:
        ctx.note("usage: no usage.command configured")
        return
    rc, out, err = run_cmd(cmd, float(sec.get("timeout_s", 60)))
    pattern = sec.get("weekly_pattern") or r"(?:week|seven)\D*?([0-9]+(?:\.[0-9]+)?)\s*%"
    m = re.search(pattern, out, re.I | re.M) if rc == 0 else None
    if not m:
        ctx.note(f"usage: no weekly percentage read (rc {rc}): {short(err or out, 120)}")
        return
    pct = float(m.group(1))
    prev = st.get("pct")
    cycle = last_weekly(ctx.now, sec["weekly_reset"], ctx.tz) if sec.get("weekly_reset") else None
    cycle_key = iso(cycle) if cycle else None
    ideas = sec.get("ideas_file") or (str(projects(ctx)[0] / ".work/orchestrator/IDEAS.md") if projects(ctx)
                                      else ".work/orchestrator/IDEAS.md")
    prompt = (sec.get("backlog_prompt") or BACKLOG_PROMPT).replace("{ideas}", ideas)
    new_cycle = bool(cycle_key and st.get("cycle") and st["cycle"] != cycle_key)
    reset = prev is not None and (prev - pct >= float(sec.get("reset_drop", 20)) or (new_cycle and pct < prev))
    if reset and st.get("reset_woken") != (cycle_key or iso(ctx.now)):
        ctx.wake(ctx.orchestrator, "usage", f"weekly usage reset ({pct:.0f} % used now, was {prev:.0f} %).",
                 prompt=prompt)
        st["reset_woken"] = cycle_key or iso(ctx.now)
        st["behind_at"] = iso(ctx.now)
    elif cycle:
        days = (ctx.now - cycle).total_seconds() / 86400
        expected = min(100.0, float(sec.get("pace_per_day", 15)) * days)
        behind = expected - pct >= float(sec.get("behind_by", 20))
        rearm = timedelta(hours=float(sec.get("rearm_hours", 24)))
        if not behind:
            st["behind_at"] = None
        elif not st.get("behind_at") or ctx.now - parse_iso(st["behind_at"]) >= rearm:
            ctx.wake(ctx.orchestrator, "usage",
                     f"far behind pace: {pct:.0f} % of the week used, pace says {expected:.0f} % by now.",
                     prompt=prompt)
            st["behind_at"] = iso(ctx.now)
        ctx.note(f"usage: {pct:.0f} % used, pace {expected:.0f} % ({days:.1f} days into the week)")
    st["pct"] = pct
    if reset or not new_cycle:  # a new cycle counts once the meter has dropped
        st["cycle"] = cycle_key


def check_decision(ctx):
    """A decision: message to an orchestrator sat unanswered while the chat was idle. Wakes that chat."""
    sec = ctx.section("decision")
    st, baseline = ctx.check_state("decision")
    seen = st.setdefault("seen", {})
    prune(seen, ctx.now)
    db = ctx.db()
    after = timedelta(minutes=float(sec.get("minutes", 30)))
    oldest = iso(ctx.now - timedelta(days=2))
    for th in sec.get("threads") or [ctx.orchestrator]:
        if not th or db.execute(Q_BUSY, (th, *ACTIVE)).fetchone():
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
    ctx.note("decision: checked" + (" (baseline, recorded without waking)" if baseline else ""))


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
          ("decision", check_decision), ("hosts", check_hosts))


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
        req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200 <= r.status < 300, "webhook", f"HTTP {r.status}"
        except Exception as e:
            return False, "webhook", short(getattr(e, "reason", e) or e, 80)
    if wake.get("command"):
        argv = [str(a).replace("{thread}", thread) for a in wake["command"]]
    else:
        base = ["--base-dir", ctx.t3_home()] if ctx.t3_home() else []
        argv = [t3_bin(ctx), "thread", "send", *base, thread, "-"]
    if ctx.dry:
        return True, "would run " + " ".join(argv[:3] + ["...", thread, "-"]), ""
    rc, _, err = run_cmd(argv, timeout, stdin=text)
    return rc == 0, "t3 thread send", short(err, 120)


def deliver(ctx):
    outbox = ctx.state.setdefault("outbox", [])
    sent = ctx.state.setdefault("sent", {})
    rate = timedelta(minutes=float(ctx.cfg.get("rate_limit_minutes", 60)))
    quiet = in_quiet(ctx)
    keep, lines_out = [], []
    by_thread = {}
    for item in outbox:
        by_thread.setdefault(item["thread"], []).append(item)
    for thread, items in by_thread.items():
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
        ok, how, err = send(ctx, thread, text)
        if ok:
            sent[thread] = iso(ctx.now)
            verb = "would wake" if ctx.dry else "woke"
            lines_out.append(f"{verb} {thread} via {how}:\n  " + text.replace("\n", "\n  "))
        else:
            for i in items:
                i["attempts"] = i.get("attempts", 0) + 1
            kept = [i for i in items if i["attempts"] < MAX_ATTEMPTS]
            lines_out.append(f"FAILED to wake {thread} via {how}: {err}"
                             + ("" if len(kept) == len(items) else f" (dropped {len(items) - len(kept)} after "
                                                                    f"{MAX_ATTEMPTS} attempts)"))
            keep += kept
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
    for name, fn in CHECKS:
        sec = cfg.get(name)
        if sec is None or (isinstance(sec, dict) and sec.get("enabled") is False):
            continue
        try:
            fn(ctx)
        except Exception as e:
            ctx.note(f"{name}: error: {short(e, 160)}")
            print(f"watcher: {name}: error: {short(e, 160)}", file=sys.stderr)
    ctx.close()
    out = deliver(ctx)
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

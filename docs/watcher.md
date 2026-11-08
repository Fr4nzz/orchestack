# Watcher reference

The README has the overview. This page holds every check, key, default and edge case.

Checks, each enabled by its section in `config.json` under `watcher`:

| Check | Wakes | When |
|---|---|---|
| `jobs` | the thread named in the job file | every job in a job file ended, or one failed (where job files live: below). Also once per job: still running at twice `expect_min` after the watcher first saw it run (a straggler), or still pending `max_pend_h` hours after the file was written. When the owner thread is deleted in T3, its parent (the file's `parent`, the lineage, else the orchestrator) gets one "orphaned jobs <ids> (thread gone)" wake and the file's later wakes |
| `children` | the child's parent | a T3 child thread's latest run failed (and the parent has not run since), it waits on an approval or answer, or a run is stuck |
| `disk` | the orchestrator, at once | free space on a path is below `min_free_gb` |
| `usage` | the orchestrator, with the backlog prompt | the weekly meter reset, or usage is `behind_by` points behind `pace_per_day`, or within `use_before_reset_h` (12) of the next `weekly_reset` less than `use_before_reset_below` (90) % is used (once per cycle: what is left is lost at the reset). With several plan accounts, list the others in `accounts`: each entry reads the same meter output and needs a unique `name` and its own `weekly_pattern`, and may set `weekly_reset`, `pace_per_day`, `behind_by`, `reset_drop` or `rearm_hours` (otherwise taken from the section). The section then needs an explicit `weekly_pattern` too. Their wakes start with `[name]`. A broken entry is skipped with an error on stderr and, after `fail_alert_minutes`, one alert. If the first account cannot be read, the whole check fails and no account is checked that run |
| `decision` | that orchestrator | a `decision:` message sat unanswered for 30 min while the chat was idle |
| `decision` | the child's parent | a child's last message starts with `decision:`, `done:`, `failed:` or `handoff:`, and T3 never relayed it to the parent (the turn was started by an outside send, such as a jobs wake) nor did the child send the parent a message with `t3_thread_send` after it, or a report (`handoff (subject): ...` counts) earlier in that turn, idle for 30 min (`"children": false` turns it off). A relay under the task id of a `t3_thread_send` that started the child's turn counts. `noted:` and `waiting:` never wake the parent, nor does a `handoff:` from a child that owns an open job file |
| `decision` | the child itself | a child's last message is a `waiting:` that names cluster jobs (`waiting: <ids>`, or `job 4101`, `Job <4101>`) that no job file (open, ended or in `done/`) lists, and the message names no background wait or `Monitor`: wakes the child once, asking it to run `watch.py add` for exactly those jobs |
| `expiry` | the owner of the oldest open job file, urgent | off unless configured: `command` prints a login's validity and `pattern` captures its end time or the time left in group 1. Runs only while a job file is open (not yet reported as ended), and wakes one thread, the owner of the oldest open job file, once per expiry time (two recipients would start two logins at once), when less than `warn_minutes` (60) are left or the pattern no longer matches (logged out). With no open job file the command does not run. `"only_with_jobs": false` restores the old behaviour: always checked, wakes the orchestrator (or `thread`). E.g. `{"command": ["tsh", "status"], "pattern": "Valid until:\\s+(\\S+ \\S+ [+-]\\d{4})", "name": "tsh", "hint": "Run tsh login."}` |
| `hosts` | nobody | a host's `probe` succeeds after failing: its `on_online` command runs (e.g. a copy) |

A job file is what a chat writes before it ends its turn on a long wait:
`{"thread": "<owner thread id>", "parent": "<owner's parent thread id>", "jobs": ["4711", "4712"], "next_step":
"read results/metrics.json"}` (`jobs` defaults to the file name, `parent` is optional). The simplest way, from any
project and without looking up an id, is the `add` command:

```bash
python3 ~/src/orchestack/watcher/watch.py add --job 4711 --job 4712 --next-step "read .work/state.md" --expect-min 90 --max-pend-h 3
```

It writes `~/.local/state/orchestack/jobs/4711.json` (`--name` sets the name, `--dir` another directory,
`--force` replaces a file of that name, `--expect-min N` and `--max-pend-h H` add the straggler and pending
limits) with the thread taken from `$T3CODE_THREAD_ID`, which T3 sets in the shell
of every chat, delegates included. Outside a T3 shell pass `--thread <id>`: the T3 tool `orchestrator_capabilities`
returns the calling chat's own id as `parentThreadId` (the parent its delegates would get). `--parent` is optional:
the watcher reads the parent from the thread's lineage. A chat that moves to another thread (e.g. to another account)
writes a new job file with its new id. The watcher reads job files, non-recursively (so `jobs/done/` is never read), from:
each configured project's `.work/orchestrator/jobs/`, each directory in `jobs.dirs`, the shared
`~/.local/state/orchestack/jobs/` (`$ORCHESTACK_STATE_DIR/jobs`), and every match of `jobs.globs` (e.g.
`"~/code/*/.work/orchestrator/jobs"`, where a matched directory means its `*.json`). A file reached twice counts once.
After the wake, the chat moves its file to `done/` next to it. T3 does not tell a parent
when a turn started by an outside send ends, so the wake tells a child owner to send its result to its parent with
`t3_thread_send` (mode "queue"), naming the parent from `parent` or else from the thread's lineage. T3 records no
lineage for a launched lead, so its parent runs `watch.py lead --thread <lead id>` right after the launch (the parent
comes from `$T3CODE_THREAD_ID`, or `--parent`). It writes `~/.local/state/orchestack/leads/<id>.json`, and the
`children` and `decision` checks then treat the lead as that parent's child. `--remove` deletes it when the lane is
done. `jobs.command` lists job states, one `<jobid> <state>` per line, e.g. for LSF over ssh:
`["ssh", "cluster-login", "bash -lc 'bjobs -a -o \"jobid stat\" -noheader'"]`. The command runs only while a job
file is open, and a failed command decides nothing. "No job found" counts as an empty listing. Once the cluster
answers, a job it no longer lists has ended (one never listed gets `missing_grace_minutes`, 30, from the file's
time). Finished jobs drop out of `bjobs -a` within hours, so after an outage `jobs.lookup_command` (optional,
`{jobs}` = the ids, `lookup_timeout_s` 180) asks how those jobs ended, in the same `<jobid> <state>` form. For LSF,
`watcher/lsf_job_states.sh` tries `bjobs -a`, then `bacct -l`, then `bhist -l` over the last 10 event files:
`"ssh cluster-login \"bash -l -s -- {jobs}\" < ~/src/orchestack/watcher/lsf_job_states.sh"` (double quotes keep the
quoted array ids such as `'123[4]'` from globbing on either side). A job the lookup
cannot place counts as `GONE` (ended, exit status unknown). An `on_online` command can run again after every outage, so make it idempotent (e.g. `rsync`, not a
plain append).

The backlog prompt points the orchestrator at `.work/orchestrator/IDEAS.md` (format in the orchestrator skill's
`references/backlog.md`). It picks the most valuable reversible, in-budget item the user would be proud to find
started, runs it through delegates and reports afterwards. With an empty backlog it proposes 3 ideas grounded in the
project's state and starts the best reversible one. Irreversible or outward-facing items wait for the user.

Rules: at most one wake per thread per `rate_limit_minutes` (60) except disk and failures, optional `quiet_hours`
(`{"start": "23:00", "end": "07:00"}` in `timezone`), and held wakes go out merged later. The first run of a check
(jobs, children, decision and the behind-pace part of usage) records what already exists without waking, and only
counts as done once it succeeded. Disk alerts even on the first run. On that first run, job files older than 14 days are taken as
handled (the woken chat moves its file to `jobs/done/`). A check that keeps failing (an expired cluster login, a T3 schema change
without the needed indexes, a broken usage command) wakes the orchestrator once after `fail_alert_minutes` (120).
Undelivered wakes retry with a backoff and are dropped, with a loud log line, only after 24 h. The T3 database is opened read-only and read by indexed columns only: before a check runs, `EXPLAIN QUERY PLAN`
must show no scan of a big table, or the check is skipped.

How it wakes a chat: the T3 CLI, `t3 thread send --base-dir <t3_home> <thread> -` (the message on stdin, and a busy
chat gets it as its next turn). With `t3_home` set, the watcher runs the T3 version named in
`<t3_home>/runtime/service-state.json`, so a `t3` on `PATH` from an older install is not used. For a thread that
has a webhook scheduled task (prompt `{{body.text}}`), put its URL in `wake.webhooks` and the watcher POSTs
`{"text": ...}` there instead. The URL holds a token: keep it in `config.json` only, never in a repo or a log.

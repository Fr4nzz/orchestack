# Watcher reference

The README has the overview. This page holds every check, key, default and edge case.

## Checks

Each check is enabled by its section in `config.json` under `watcher`. Details for the longer ones follow the table.

| Check | Wakes | When |
|---|---|---|
| `jobs` | the thread named in the job file (its parent if that thread is gone) | every job in a job file ended or one failed, a job straggles or pends too long, or the owner thread is gone |
| `children` | the child's parent | a T3 child's latest run failed (and the parent has not run since), it waits on an approval or answer, or a run is stuck |
| `disk` | the orchestrator, at once | free space on a path is below `min_free_gb` |
| `usage` | the orchestrator, with the backlog prompt | the weekly meter reset, usage is behind pace, or unused capacity will be lost at the next reset |
| `decision` | that orchestrator | a `decision:` message sat unanswered for 30 min while the chat was idle |
| `decision` | the child's parent | a child's report never reached the parent |
| `decision` | the child itself | a child waits on cluster jobs that no job file lists |
| `expiry` | the owner of the oldest open job file, urgent | a login is about to expire or has expired (off unless configured) |
| `expiry` | the orchestrator, urgent | with `supervised`: a login ends within 90 min, or overnight (asked in the evening) |
| `hosts` | nobody | a host's `probe` succeeds after failing: its `on_online` command runs (e.g. a copy) |

### `jobs`

- A job file wakes its thread when one of its jobs fails (once) and again when every job in it has ended (where job
  files live: below).
- A straggler wakes it once per job: still running at twice `expect_min` after the watcher first saw it run.
- A pending job wakes it once: still pending `max_pend_h` hours after the file was written.
- When the owner thread is deleted in T3, its parent (the file's `parent`, the lineage, else the orchestrator)
  gets one "orphaned jobs <ids> (thread gone)" wake and the file's later wakes.

### `usage`

- It wakes when the weekly meter resets, when usage is `behind_by` points behind `pace_per_day`, or once per cycle
  when, within `use_before_reset_h` (12) of the next `weekly_reset`, less than `use_before_reset_below` (90) % is
  used (what is left is lost at the reset).
- `pace: false` (per account too): the account is used as needed up to 100 % and reset by hand. It gets no
  behind or unused wakes, `pace` says "use it as needed", and a reset still wakes.
- Several plan accounts: list the others in `accounts`.
  - Each entry reads the same meter output and needs a unique `name` and its own `weekly_pattern`.
  - It may set `weekly_reset`, `pace`, `pace_per_day`, `behind_by`, `reset_drop` or `rearm_hours`, otherwise they come
    from the section.
  - With `accounts` set, the section itself needs an explicit `weekly_pattern`.
  - Their wakes start with `[name]`.
  - A broken entry is skipped with an error on stderr and, after `fail_alert_minutes`, one alert.
  - If the first account cannot be read, the whole check fails and no account is checked that run.

### `decision` for a child's report

- It fires when a child's last message starts with `decision:`, `done:`, `failed:` or `handoff:`, the child has
  been idle for 30 min, and the report never reached the parent:
  - T3 did not relay it (the turn was started by an outside send, such as a jobs wake).
  - The child did not send the parent a message with `t3_thread_send` after it.
  - The child did not send a report (`handoff (subject): ...` counts) earlier in that turn.
- A relay under the task id of a `t3_thread_send` that started the child's turn counts as reaching the parent.
- `noted:` and `waiting:` never wake the parent, nor does a `handoff:` from a child that owns an open job file.
- `"children": false` turns this check off.

### `decision` for a waiting child

- It fires when a child's last message is a `waiting:` that names cluster jobs (`waiting: <ids>`, or `job 4101`,
  `Job <4101>`) that no job file (open, ended or in `done/`) lists, and the message names no background wait or
  `Monitor`.
- It wakes the child once, asking it to run `watch.py add` for exactly those jobs.

### `expiry`

- Off unless configured: `command` prints a login's validity, and `pattern` captures its end time or the time left
  in group 1.
- It runs only while a job file is open (not yet reported as ended). With no open job file the command does not run.
- It wakes one thread, the owner of the oldest open job file, once per expiry time, when less than `warn_minutes`
  (60) are left or the pattern no longer matches (logged out). One recipient only, because two would start two
  logins at once.
- `"only_with_jobs": false` restores the old behaviour: always checked, and it wakes the orchestrator (or
  `thread`).
- Optional `supervised` (`{}` for the defaults) asks for a renewal a person approves (e.g. a phone push) while they
  are likely awake, before an unattended one can fail at night. It wakes the orchestrator (or `supervised.thread`),
  urgent, with `supervised.hint`:
  - once per expiry time when less than `minutes` (90) are left. The owner's own warning then waits one run, so
    two logins do not start at once;
  - once per evening from `evening` (`"21:30"`, in `timezone`) when the login ends before `morning` (`"08:00"`)
    the next day.
- Example: `{"command": ["tsh", "status"], "pattern": "Valid until:\\s+(\\S+ \\S+ [+-]\\d{4})", "name": "tsh", "hint": "Run tsh login."}`

## Job files

A job file is what a chat writes before it ends its turn on a long wait:

```json
{"thread": "<owner thread id>", "parent": "<owner's parent thread id>", "jobs": ["4711", "4712"], "next_step": "read results/metrics.json"}
```

`jobs` defaults to the file name, and `parent` is optional.

### Write one with `add`

The simplest way, from any project and without looking up an id:

```bash
python3 ~/src/orchestack/watcher/watch.py add --job 4711 --job 4712 --next-step "read .work/state.md" --expect-min 90 --max-pend-h 3
```

This writes `~/.local/state/orchestack/jobs/4711.json`.

- `--name` sets the name, `--dir` another directory, and `--force` replaces a file of that name.
- `--expect-min N` and `--max-pend-h H` add the straggler and pending limits.
- The thread comes from `$T3CODE_THREAD_ID`, which T3 sets in the shell of every chat, delegates included.
- Outside a T3 shell, pass `--thread <id>`. The T3 tool `orchestrator_capabilities` returns the calling chat's
  own id as `parentThreadId` (the parent its delegates would get).
- `--parent` is optional: the watcher reads the parent from the thread's lineage.
- A chat that moves to another thread (e.g. to another account) writes a new job file with its new id.

### Where the watcher reads them

Non-recursively (so `jobs/done/` is never read), from:

- each configured project's `.work/orchestrator/jobs/`
- each directory in `jobs.dirs`
- the shared `~/.local/state/orchestack/jobs/` (`$ORCHESTACK_STATE_DIR/jobs`)
- every match of `jobs.globs` (e.g. `"~/code/*/.work/orchestrator/jobs"`, where a matched directory means its
  `*.json`)

A file reached twice counts once.

### After the wake

- The chat moves its file to `done/` next to it.
- T3 does not tell a parent when a turn started by an outside send ends. So the wake tells a child owner to send
  its result to its parent with `t3_thread_send` (mode "queue"), naming the parent from `parent` or else from
  the thread's lineage.

### Leads

T3 records no lineage for a launched lead.

1. Right after the launch, the parent runs `watch.py lead --thread <lead id>` (the parent comes from
   `$T3CODE_THREAD_ID`, or `--parent`).
2. That writes `~/.local/state/orchestack/leads/<id>.json`, and the `children` and `decision` checks then treat
   the lead as that parent's child.
3. When the lane is done, the parent runs `watch.py lead --thread <lead id> --remove`.

### Job states from the cluster

`jobs.command` lists job states, one `<jobid> <state>` per line. For LSF over ssh:

```json
["ssh", "cluster-login", "bash -lc 'bjobs -a -o \"jobid stat\" -noheader'"]
```

- The command runs only while a job file is open, and a failed command decides nothing.
- "No job found" counts as an empty listing.
- Once the cluster answers, a job it no longer lists has ended. A job never listed gets
  `missing_grace_minutes` (30) from the file's time.

Finished jobs drop out of `bjobs -a` within hours. So after an outage, the optional `jobs.lookup_command` asks
how those jobs ended, in the same `<jobid> <state>` form (`{jobs}` = the ids, `lookup_timeout_s` 180).

- For LSF, `watcher/lsf_job_states.sh` tries `bjobs -a`, then `bacct -l`, then `bhist -l` over the last 10
  event files:

  ```json
  "ssh cluster-login \"bash -l -s -- {jobs}\" < ~/src/orchestack/watcher/lsf_job_states.sh"
  ```

  The double quotes keep quoted array ids such as `'123[4]'` from globbing on either side.
- A job the lookup cannot place counts as `GONE` (ended, exit status unknown).

## Hosts

An `on_online` command can run again after every outage, so make it idempotent (e.g. `rsync`, not a plain
append).

## Backlog

The backlog prompt points the orchestrator at `.work/orchestrator/IDEAS.md` (format in the orchestrator skill's
`references/backlog.md`). It picks the most valuable reversible, in-budget item the user would be proud to find
started, runs it through delegates and reports afterwards. With an empty backlog it proposes 3 ideas grounded in the
project's state and starts the best reversible one. Irreversible or outward-facing items wait for the user.

## Rules

- At most one wake per thread per `rate_limit_minutes` (60), except disk and failures.
- Optional `quiet_hours` (`{"start": "23:00", "end": "07:00"}` in `timezone`).
- Wakes held by the rate limit or quiet hours go out merged later.
- The first run of a check (jobs, children, decision and the behind-pace part of usage) records what already
  exists without waking, and only counts as done once it succeeded. Disk alerts even on the first run.
- On that first run, job files older than 14 days are taken as handled (the woken chat moves its file to
  `jobs/done/`).
- A check that keeps failing (an expired cluster login, a T3 schema change without the needed indexes, a broken
  usage command) wakes the orchestrator once after `fail_alert_minutes` (120).
- Undelivered wakes retry with a backoff and are dropped, with a loud log line, only after 24 h.
- The T3 database is opened read-only and read by indexed columns only. Before a check runs,
  `EXPLAIN QUERY PLAN` must show no scan of a big table, or the check is skipped.

## How it wakes a chat

- Through the T3 CLI: `t3 thread send --base-dir <t3_home> <thread> -`, with the message on stdin. A busy chat
  gets it as its next turn.
- With `t3_home` set, the watcher runs the T3 version named in `<t3_home>/runtime/service-state.json`, so a `t3`
  on `PATH` from an older install is not used.
- For a thread that has a webhook scheduled task (prompt `{{body.text}}`), put its URL in `wake.webhooks` and the
  watcher POSTs `{"text": ...}` there instead. The URL holds a token: keep it in `config.json` only, never in a
  repo or a log.

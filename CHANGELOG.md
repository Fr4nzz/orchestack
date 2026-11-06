# Changelog

## 0.2.0 (unreleased)

- `watcher/watch.py`: change-only watcher on a 15-minute systemd `--user` timer, no model calls. Checks for
  cluster jobs (job files, owner thread woken), failed, waiting or stuck T3 children (parent woken), low disk,
  weekly usage reset or behind pace (backlog prompt), unanswered `decision:` messages, and a host-online copy hook.
  Wakes through `t3 thread send` or a scheduled-task webhook, one per change, rate-limited, quiet hours, dry run.
- Watcher `usage.accounts`: further plan accounts read from the same meter output, each with a unique name, its own pattern, weekly
  reset, pace and state. Their wakes start with `[name]`, and a broken entry is skipped without blocking the first account.
- Watcher `decision` check: a child whose last message is a `decision:` that never reached its parent (T3 relays
  only turns the delegate task started, not turns started by an outside send such as a jobs wake) wakes the parent
  once after 30 min idle. Its first run records without waking. It also covers `done:`, `failed:` and `handoff:`
  (first run after the upgrade records those without waking), and a report the child sent the parent itself during
  that turn counts as delivered.
- Watcher `jobs` wakes tell a child owner to send its result to its parent with `t3_thread_send` (mode "queue"),
  naming the parent from the job file's optional `"parent"` key or the thread's lineage.
- Watcher `jobs`: a job the cluster no longer lists counts as ended at once (was: nothing decided for 12 h, then an
  `unknown:` wake), so jobs that finish during an outage still wake their owner once. Optional `lookup_command`
  (with `watcher/lsf_job_states.sh` for LSF: `bjobs -a`, `bacct`, `bhist`) reads how they ended.
- Watcher `decision`: a child's own `t3_thread_send` to its parent counts as delivered when it comes after the
  child's message, or earlier in the turn as a report with a subject (`handoff (s05): ...`).
  The subject may also come first when bold, dash-separated or a code-like token (`cls-freeze handoff: ...`,
  `**Retrain stage** - done:`). Free prose before the word (`Download done: now training`) and negations
  (`not yet done:`) stay progress, so they cannot silence the real report.
- Watcher `decision`: a T3 relay of a turn that a `t3_thread_send` started (`...%3Athread-send%3A<uuid> [completed]`)
  counts as delivered: the watcher matches the task ids of the messages that started the child's recent runs, not
  only the delegate task id (a false "not relayed" wake on 5 Nov).
- Watcher `decision`, rule for progress endings: `noted:` (a progress-only turn) and `waiting: <job ids>` (a long
  wait) never wake the parent. A `handoff:` from a child that owns an open job file is the old ending of a long wait
  and stays quiet too (the jobs wake restarts the child). Every other unrelayed `handoff:` still wakes the parent,
  whoever started the turn. The orchestrator skill's rules 7 and 8 now end long waits with `waiting:`.
- Watcher `decision`: a child whose last message is a `waiting:` or `handoff:` naming a cluster job (`waiting: <ids>`,
  `job 4101`, `Job <4101>`) that no job file lists (open, ended or moved to `done/`, any owner) is woken itself,
  once, and told to register the wait with `watch.py add`. A message naming no job, or a background wait, stays quiet.
- `watch.py add --expect-min N --max-pend-h H` (both optional): the jobs check wakes the owner once when a job still
  runs at twice its expected runtime (a straggler) or still pends H hours after registration. `jobs.running_states`
  and `jobs.pending_states` default to LSF and SLURM words.
- Watcher `jobs`: before waking a job file's owner, the watcher checks the T3 thread still exists. If it is gone, its
  parent (the file's `parent`, the lineage, else the orchestrator) is woken once with "orphaned jobs <ids> (thread
  gone)" and gets the file's later wakes.
- Watcher `expiry`: one alert per expiry, to the owner of the oldest open job file, instead of one to every owner
  (two lanes ran the login script at once).
- Watcher `expiry` check (opt-in): a login whose validity (`command` + `pattern`, e.g. `tsh status`) has less than
  `warn_minutes` left, or no longer matches the pattern, wakes the orchestrator once.
- Watcher `expiry` runs only while a job file is open and wakes the owners of those files instead of the
  orchestrator (`"only_with_jobs": false` keeps the old always-on check). It no longer wakes a large orchestrator
  every few hours when no job runs.
- Watcher job files from any project: the shared `~/.local/state/orchestack/jobs/` and `jobs.globs` join the
  project list and `jobs.dirs`. `watch.py add --job <id> ...` writes a job file there, taking the thread from
  `$T3CODE_THREAD_ID`, so a worker needs neither a configured project nor its own thread id.
- `bulk-image-review`: new section "Geometry checks are not label checks": for spatial correctness, measure parts not
  the outline, review the critical region at full resolution, build known-answer cases, and leave one page of crops
  for a human spot check, whose feedback overrides the agent's checks. One worked example.
- `sol-loop`: a recurring symptom means finding the shared cause, no "does not scale" decline without counting the
  items, a `Ground truth:` line in the brief, and spatial or domain-judgement output checked only by the worker's
  own metrics is labelled "unvalidated" (a label, not a stop).
- `orchestrator`: one owner per lane, other chats forward feedback to it instead of dispatching.
- `scripts/install.sh --watcher` installs and enables the timer (off by default). `--uninstall` removes it.
- `orchestrator` skill: the watcher replaces AI scheduled checks (one-off projected-finish wakes only), long waits
  write a job file, and a new Backlog section defines `.work/orchestrator/IDEAS.md` and the backlog prompt.

## 0.1.0 (2026-11-01)

- `orchestrator` skill: the orchestrator playbook, generalised, with a "Becoming an orchestrator" section and new
  rules from two usage audits: re-read the installed skill after every compaction or resume and before any brief,
  progress is not news (only `decision:`, `done:`, `failed:`, `handoff:` go up, FYIs to a notes file), one
  scheduled wake for cluster waits, no schedules just because children exist, fresh workers for big chats,
  `t3_thread_launch` for long waits as an opt-in, and a free-RAM check before local work.
- `sol-loop` skill: one review loop replacing a single-critic loop. Two
  independent critics every round (GPT Sol + a fresh Opus) for code, writing, analyses and UI.
- `bulk-image-review` skill: moved in and generalised.
- `hooks/role_card.py`: SessionStart role card for orchestrator and lead chats (thread-id registry or T3 title).
- `hooks/memory_gate.py`: PreToolUse deny on `Agent`, `delegate_task` and `t3_thread_launch` when free RAM is low.
- `scripts/install.sh` (with `--dry-run`, `--uninstall`, `--keep-old`), `scripts/check_drift.sh`, scrub check,
  lint, pytest suite.

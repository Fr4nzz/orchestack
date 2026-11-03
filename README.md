# orchestack

Skills and hooks for running coding-agent orchestrators in T3 Code (Claude Code through the Agent SDK, and Codex).

An orchestrator chat routes work to delegates, decides small things itself and waits cheaply. In practice
orchestrators forget that role after a compaction, wake themselves for nothing, and big chats burn the weekly
budget re-reading their own context. orchestack packs the rules that fixed this into three skills and two small
hooks.

## Why: what the usage audits found

Two audits of one user's week of T3 work (every local transcript, cost-weighted tokens, calibrated against the
plan's usage meter) found:

- Re-reading a context after the 1-hour cache expired was about **10 % of a week**. Half of one orchestrator's own
  cost was cache rewrites, mostly from a 3-hourly scheduled check.
- Background waits on cluster jobs that timed out after about 2 h returned to cold caches: about **4 % of a week**.
- A large writing chat did 40 long hands-on turns at about 505k context. Fresh workers would have saved about
  **60 % of that**.
- Delegates reused for unrelated follow-up tasks grew to 800 to 960k context.
- Approving a programme from token estimates instead of a measured pilot ran **about 8x over**, and that one
  programme took 38 % of all usage in its period.
- Images reviewed by small fresh workers cost about 2 % of a week per 300 items. The same kind of review in a few
  long agents cost about 55 %.
- An outdated copy of the rules pasted into a brief caused about 25 extra parent wakes.
- 30 AI scheduled checks that woke a large orchestrator every 3 to 6 h cost about **1.6 % of a week in 4 days**,
  and most of them found no change. The watcher below replaces them.

## What is in it

| Piece | Say or trigger | What it does |
|---|---|---|
| `skills/orchestrator` | "you are the orchestrator", "orchestrate this", "work autonomously", "decide for me", "parallelize", "I'm going to sleep", chats titled `Orchestrator:` or `Lead:` | The playbook: parallel lanes via delegates, decide reversible steps, no "want me to...?", pilot pricing, pace, waits, briefs, models. |
| `skills/sol-loop` | "sol loop", "dual critic", "critic loop" | One review loop with two independent critics per round (GPT Sol + a fresh Opus), smallest fixes, a ledger, a stop rule. |
| `skills/bulk-image-review` | before agents look at many images | Cost model, ask-first threshold, and the small-fresh-worker pattern. |
| `hooks/role_card.py` | Claude Code `SessionStart` (`startup`, `resume`, `compact`) | Prints a 7-line role card into context for orchestrator and lead chats, so the role survives compaction. |
| `watcher/watch.py` | systemd `--user` timer, every 15 min (opt-in) | Wakes a chat only when something changed: jobs ended, a child failed, low disk, weekly reset or behind pace, an unanswered `decision:`. No model calls. |
| `hooks/memory_gate.py` | Claude Code `PreToolUse` on `Agent`, `delegate_task`, `t3_thread_launch` | Denies a local dispatch when free RAM is under a threshold (default 3 GiB). Never counts agents. |

The hooks use only the Python standard library, exit 0 on every error, and take about 20 ms.

## Install

```bash
git clone <this repo> ~/src/orchestack
~/src/orchestack/scripts/install.sh --dry-run    # shows every step, changes nothing
~/src/orchestack/scripts/install.sh
~/src/orchestack/scripts/check_drift.sh          # ok / MISSING / COPY / DRIFT / OLD per skill and root
```

`install.sh`:

1. symlinks each skill into `~/.claude/skills` and `~/.codex/skills`, so one checkout feeds both harnesses and
   `git pull` updates them (copies in two places drift apart),
2. moves any existing folder of the same name (and, with `--retire=a,b`, your own older skills of those names)
   to `~/.local/share/orchestack/backups/<timestamp>/` with `mv` (never deletes). Backups live outside the skill
   folders because agents load every `SKILL.md` they find there,
3. merges the hook registrations into `~/.claude/settings.json` after a backup, keeping every existing key,
4. adds one line to `~/.claude/CLAUDE.md` (the only text proven to survive every compaction): "Orchestrator or lead
   chats (told 'you are the orchestrator', or titled Orchestrator:/Lead:): load the `orchestrator` skill at start
   and again after every compaction."

`--watcher` (off by default) also installs the change-only watcher (see below). `--uninstall` reverses all of it and restores the newest backups. `--keep-old` leaves superseded skills in place.
A Claude Code plugin manifest (`.claude-plugin/`, `hooks/hooks.json`) is included but less tested than the script.
Use one method, not both. Codex gets the skills only (the hooks are Claude Code hooks).

## Configure

Everything personal lives outside the repo, in `~/.config/orchestack/` (or `$ORCHESTACK_CONFIG_DIR`):

- `local.md`: your overlay. Every skill reads it first and it overrides their defaults. Put your name, time zone,
  usage meter command, pinned models, pace, ask threshold, weekly reset, cluster rules, orchestrator thread ids and
  standing permissions here. See `examples/local.example.md`.
- `roles.json`: `{"<T3 thread id>": "orchestrator" | "lead" | "none"}`. The role card hook reads the thread id from
  `T3CODE_THREAD_ID`. `none` silences a chat whose title would otherwise match.
- `config.json`: hook settings. See `examples/config.example.json`.
  - `memory_gate.min_available_gib` (default 3.0), `memory_gate.swap_weight` (default 0, counts that share of
    free swap as available), `memory_gate.enabled`.
  - `role_card.t3_state_db`: optional path to T3's state database. If set, a chat whose title starts with
    `Orchestrator:` or `Lead:` gets the card too. The hook opens it read-only and looks up one row by primary key.
    It never scans (the database can be tens of GB) and skips silently on any error.
  - `role_card.skill_path`: the installed orchestrator skill the card tells the chat to re-read.

## Watcher

`watcher/watch.py` replaces AI scheduled checks. It runs every 15 minutes from a systemd `--user` timer, calls no
model, and compares what it sees with its last run (`~/.local/state/orchestack/watch.json`). Each change wakes one
chat once, with a short message that starts with the reason, e.g. `watcher: jobs: train.json: all 4 jobs ended
(DONE 4). Next step: ...`. Checks, each enabled by its section in `config.json` under `watcher`:

| Check | Wakes | When |
|---|---|---|
| `jobs` | the thread named in the job file | every job in a `<project>/.work/orchestrator/jobs/<name>.json` ended, or one failed |
| `children` | the child's parent | a T3 child thread's latest run failed (and the parent has not run since), it waits on an approval or answer, or a run is stuck |
| `disk` | the orchestrator, at once | free space on a path is below `min_free_gb` |
| `usage` | the orchestrator, with the backlog prompt | the weekly meter reset, or usage is `behind_by` points behind `pace_per_day`. With several plan accounts, list the others in `accounts`: each entry reads the same meter output and needs a unique `name` and its own `weekly_pattern`, and may set `weekly_reset`, `pace_per_day`, `behind_by`, `reset_drop` or `rearm_hours` (otherwise taken from the section). The section then needs an explicit `weekly_pattern` too. Their wakes start with `[name]`. A broken entry is skipped with an error on stderr and, after `fail_alert_minutes`, one alert. If the first account cannot be read, the whole check fails and no account is checked that run |
| `decision` | that orchestrator | a `decision:` message sat unanswered for 30 min while the chat was idle |
| `decision` | the child's parent | a child's last message is a `decision:` that T3 never relayed to the parent (the turn was started by an outside send, such as a jobs wake), idle for 30 min (`"children": false` turns it off) |
| `hosts` | nobody | a host's `probe` succeeds after failing: its `on_online` command runs (e.g. a copy) |

A job file is what a child writes before it ends its turn on a long wait:
`{"thread": "<owner thread id>", "jobs": ["4711", "4712"], "next_step": "read results/metrics.json"}` (`jobs`
defaults to the file name). `jobs.command` lists job states, one `<jobid> <state>` per line, e.g. for LSF over ssh:
`["ssh", "cluster-login", "bash -lc 'bjobs -a -o \"jobid stat\" -noheader'"]`. The command runs only while a job
file is open, and a failed command decides nothing. "No job found" counts as an empty listing. Once a file is over
12 h old and none of its jobs are listed any more, its owner gets one `unknown:` wake (check `bhist` or the
outputs). An `on_online` command can run again after every outage, so make it idempotent (e.g. `rsync`, not a
plain append).

The backlog prompt points the orchestrator at `.work/orchestrator/IDEAS.md` (format in the orchestrator skill): pick
the most valuable reversible, in-budget item the user would be proud to find started, run it through delegates and
report afterwards. With an empty backlog it proposes 3 ideas grounded in the project's state and starts the best
reversible one. Irreversible or outward-facing items wait for the user.

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

```bash
watcher/watch.py --dry-run --verbose      # what it sees and would send, changes nothing
scripts/install.sh --dry-run --watcher    # then without --dry-run to enable the timer
journalctl --user -u orchestack-watch     # what it sent
```

## Development

```bash
git config core.hooksPath .githooks   # pre-commit runs the scrub check and lint
scripts/lint.sh                        # frontmatter, size budgets, JSON, bash -n, Python syntax, prose rules
scripts/scrub_check.sh                 # no personal data in the repo
python3 -m pytest tests                # or: uvx pytest tests
```

Put your own names, hosts, project names and ids in the git-ignored `.scrub-patterns.local` (one regex per line).

## Status

Version 0.1. The hooks are unit-tested and were run against a real T3 state database. The role card firing after
compaction under T3's resume path is not yet proven in a live chat. Pilot it on one lead first.

## Licence and credits

[MIT](LICENSE). `skills/sol-loop` is adapted from Theo Browne's `sol-loop` in
[tstack](https://github.com/t3dotgg/tstack) (MIT). His notice is kept in `skills/sol-loop/NOTICE.md`. Other ideas
are credited in [CREDITS.md](CREDITS.md).

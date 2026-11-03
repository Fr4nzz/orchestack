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

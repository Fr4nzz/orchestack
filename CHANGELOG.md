# Changelog

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

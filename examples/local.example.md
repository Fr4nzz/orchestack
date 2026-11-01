# orchestack local overlay (example)

Copy to `~/.config/orchestack/local.md` and edit. Skills read this file first, and it overrides their defaults.
Keep it out of any repo.

## Who and when
- User: Alex. Time zone for logs and schedules: UTC-5 (write times as "EST").
- Weekly usage reset: Monday 09:00 local.

## Budget
- Usage meter: `python3 ~/tools/usage_meter.py` (prints % of the 5-hour window and of the week).
- Pace about 15 % of the week per day, at most one full 5-hour window per night or burst.
- Ask before a programme above about 5 % of the week.

## Models (only the user changes these)
- Default: Opus 5.5. Other-vendor critic: GPT Sol at medium effort.
- Never move to a newer or pricier model on your own. Suggest it instead.

## Orchestrators
- Main orchestrator thread: `mcp:<thread id>` (project: web shop). Lead: `mcp:<thread id>` (project: mobile app).
- Never compact or replace an orchestrator. The user does that.
- `t3_thread_launch` for long-wait children: not permitted (opt-in only).

## Cluster
- Batch jobs run on the lab cluster (`lsf`), never heavy work on this laptop. Shared GPUs only.

## Deploys
- Deploy finished, tested work by default, with a rollback path. Push only your own commits.

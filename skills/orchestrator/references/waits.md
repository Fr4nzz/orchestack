# Waiting mechanics

The orchestrator skill says which wait to use (**Long waits**, **Short waits**). This file holds how to run each.
Read it before `watch.py add` and before a background wait of 10 to 45 min. The wait block in the skill's
**Briefs** carries the same rules for children, who never see this file.

## Long waits through the watcher (over about 45 min)

1. Submit. Scheduler jobs first wait on the short-wait path until they run or about 20 min pass (see the cluster
   skill).
2. Register them: `python3 <orchestack>/watcher/watch.py add --job <id> [--job ...] --next-step "..." --expect-min
   <runtime> --max-pend-h <e.g. 3>`. Always pass both limits. Without them the watcher sends no straggler or pending
   wake. The command works from any project and takes the thread id from `$T3CODE_THREAD_ID` (outside a T3 shell, pass
   `--thread <id>`, see `docs/watcher.md` in the repo).
3. Write a state file (outputs, next step) and end the turn with `waiting:` plus the job ids.
4. The watcher wakes that thread once when every job ended or one failed. Without the watcher, schedule ONE wake
   at the projected finish.
5. At that wake, read the state file, move the job file to the `done/` folder next to it and report `done:` or
   `failed:` (a child sends it with `t3_thread_send`, mode "queue").
6. On a straggler or pend-limit wake, keep the open job file, act if needed and end with `waiting:`, sending
   nothing.
7. After a one-failed wake, re-add the survivors (only unfinished jobs from the file you moved to `done/`, pending
   ones too) with both limits.

Why not one long background wait instead: a background wait that times out after 2 h returns to an expired cache.

## Short waits (under about 45 min)

`CAP` (seconds before the wait loop gives up and prints `CAP_REACHED`) and `PEND_S` (seconds a job may pend
before the loop prints `PEND_LONG`) are defined in the wait block. A suitable loop is in hpcstack's cluster-ops
`references/monitoring.md`.

- **Under 10 min:** one foreground command that blocks until the jobs end, with the Bash tool's `timeout`
  parameter set to 600000 (its default is 120000) and `CAP=420` to match. No extra wake.
- **10 to 45 min:** ONE background command with the Bash tool's `timeout` at least (CAP+180) x 1000 ms (3000000 for
  `CAP=2700`, maximum 7200000). Then call `t3_background_wait` with `taskIds: [id]` (never from a native subagent). It
  does not block: it delays this turn's relay until the command ends, so the parent gets two relays close together.
  End that turn with one short line and report in the turn the completion starts.
- Never re-arm a background loop, because each re-arm adds relays. Over 45 min, or if the loop ends with
  `CAP_REACHED` or `PEND_LONG`, switch to the watcher above.
- Codex: one foreground command within the tool timeout, never a series of `sleep` tool calls.

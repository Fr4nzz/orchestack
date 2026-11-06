---
name: orchestrator
description: Use when a chat coordinates other chats, delegates or subagents, when the user says "you are the orchestrator", "you are the lead", "orchestrate this", "parallelize this across agents", or "work autonomously and decide for me", when the chat is titled "Orchestrator:" or "Lead:", when starting, resuming or compacting as an orchestrator or lead, and before approving a batch, wave, fan-out or overnight run.
---

# Orchestrator

If `~/.config/orchestack/local.md` exists, read it now. It holds the user's personal values (name, time zone, usage
meter, pinned models, pace, cluster rules, orchestrator thread ids) and overrides the defaults here. Thresholds
marked *(default)* are chosen policy, not measurements. The README has the measurements behind the rules.

## Becoming an orchestrator

- **Split the work into independent lanes and run them in parallel** through delegates, one worker per lane. The
  orchestrator routes and decides. Multi-step work goes to a worker with a brief. A small single step (one lookup,
  one edit, one reply) you may just do.
- **Decide reversible, in-budget steps yourself and report afterwards,** whether the user is present or away.
- **End-of-reply check.** Before ending a reply, look at the plan. If approved work that needs no answer remains,
  start it in the same reply with the next tool call. Never end with "want me to...?" or "next I'll..." instead.
- **Re-read this installed file** (`~/.claude/skills/orchestrator/SKILL.md` or `~/.codex/skills/orchestrator/SKILL.md`)
  on every wake after a compaction or resume, and before writing any brief. Never rely on an in-context copy.

## Budget

1. **Price work in % of the week from a pilot.** Read the usage meter, run one small batch, read it again, and note
   which other chats were active (the meter is shared). Never approve from token estimates: they leave out cache
   reads.
2. **Ask the user before a programme (all its batches together) above about 5 % of the week** *(default)*, unless
   that measured spend is already approved. Within budget, decide reversible steps yourself (a merge, a deploy of
   tested work with a rollback, the next experiment, a cheap check). Ask only about irreversible or outward-facing
   steps and facts only the user has. Before the user leaves, ask everything at once and give every chat a queue
   of work that needs no answers. Never sit idle waiting for an answer.
3. **Pace about 15 % of the week per day,** with at most one full 5-hour window per night or burst *(default)*.
   Unused capacity can't be carried over, so the pace is also a target: when behind it, start queued valuable work.

## Shape of the work

4. **The orchestrator's context grows as needed.** Never compact or replace an orchestrator yourself (the user does
   that). Above about 400k, give its size and rough cost per wake in status replies.
5. **Lead chats per project area.** A lead handles its children's notes, deploy requests and small questions on
   its next real task. Leads and workers rely on harness auto-compaction. Around 500k *(soft guide)* the
   orchestrator may compact an idle lead or worker at a natural break by sending it exactly `/compact`, never to an
   orchestrator. Requests for the orchestrator are sent to it (`t3_thread_send`), not left in the requester's thread.
   A child reporting to its own parent follows rule 7 instead (send or end with it, never both).
   One owner per lane: a chat that gets feedback on a lane it does not own forwards it to the lane's owner instead
   of dispatching its own workers, because two owners dispatch duplicate work.
6. **Every wake re-reads the whole context,** about 0.1x its size within the cache lifetime (about 1 h) and about 2x
   after. So batch small items for a big idle chat into its next real task. Above about 400k, start a fresh worker
   for multi-step work instead of doing it in place (a fresh start pays back after 1 to 2 steps). Don't reuse a
   finished delegate for a new unrelated task once it is large.
7. **Progress is not news.** Children and sub-leads send up only messages that start with `decision:`, `done:`,
   `failed:` or `handoff:`. FYIs and progress go to a notes file (default `.work/orchestrator/NOTES.md` in the
   project) that the lead reads on its next real task. Answer a stray progress note in one line or not at all.
   A turn that only logs progress ends with `noted: ...`, a long wait with `waiting: <job ids> ...`. Both are turn
   endings, never sends: don't `t3_thread_send` them. The watcher never wakes anyone for them, but **T3 relays
   every turn end of an async delegate to its parent, whatever it says** (no setting turns this off). So the
   only way to wake the parent less is to end fewer turns: wait inside a turn (rule 9) or through the watcher
   (rule 8). An async delegate's report to its parent is either sent or the turn ends with it, never both: any
   turn the watcher did not start ends with it (T3 relays it). A turn the watcher started sends it (T3 does not
   relay those), and so does a turn a scheduled wake started (not verified: send to be safe). A launched child
   (rule 11) always sends.
   Don't send FYIs or notes to a running child: each one starts a child turn whose end is relayed back to you.
   Put them in its state or notes file and send only what changes its current step.

## Waiting

8. **Long waits (over about 45 min, e.g. cluster jobs) take precedence over everything below:** submit (for scheduler jobs, first wait on the short-wait path until
   the job runs or about 20 min pass, see the cluster skill), register the wait with
   `python3 <orchestack>/watcher/watch.py add --job <id> [--job ...] --next-step "..." --expect-min <expected runtime> --max-pend-h <limit, e.g. 3>`
   (always supply both: without them the straggler and pending wakes are off. It writes a job file to
   `~/.local/state/orchestack/jobs/`, works from any project, and takes the thread id from `$T3CODE_THREAD_ID`.
   `--parent` is optional, else the watcher uses lineage), write a state file (outputs, next step), and end the turn with `waiting:` plus the job ids. The watcher
   wakes that thread once when every job ended or one failed. Without the watcher, schedule ONE wake at the
   projected finish instead. At the wake, a fresh worker (or the child) reads the state file, moves the job file
   to the `done/` folder next to it and reports `done:` or `failed:`. A child woken by the watcher sends that report to its parent
   with `t3_thread_send` (mode "queue"), because the end of a turn the watcher started does not notify the parent.
   Send only a final `done:`/`failed:`/`decision:`. If jobs are still running (a straggler or pend-limit wake),
   leave the open job file in place, act if needed and end with `waiting:` without sending anything. Re-add with
   `watch.py add` only jobs whose file you moved to `done/` (e.g. the survivors after a one-failed wake). A background wait that times out after 2 h returns to an expired cache and costs more.
9. **Short waits (under about 45 min): as few turn ends as possible,** because every child turn end wakes its
   parent whatever it says.
   - Under 10 min: one foreground command that blocks until the jobs end, with the Bash tool's `timeout`
     parameter set to 600000 (its default is 120000) and a loop cap to match (`CAP=420`). No extra wake.
   - 10 to 45 min: ONE background command with the Bash tool's `timeout` parameter at least (CAP+180) x 1000 ms
     (3000000 for `CAP=2700`; the default slot is 30 min, the maximum 7200000), then `t3_background_wait` on it.
     `t3_background_wait` does not block: it only delays the relay of this turn's end until the command ends.
     The parent then gets two relays close together: end that turn with one short line and nothing to report,
     and give the report in the turn the completion starts.
   - Never re-arm a background loop: each re-arm adds relays (one child woke its orchestrator 12 times overnight
     this way). If it ends with `CAP_REACHED` or a pending report, switch to rule 8.
   - Over 45 min: rule 8. The only background loop there is its pre-wait until the job runs.
   - Codex: one foreground command within the tool timeout. Waiting on your own delegates or critics:
     `t3_thread_wait` inside the same turn. Never a series of `sleep` tool calls.
10. **Use the orchestack watcher instead of AI scheduled checks.** It runs every 15 min without a model and wakes a
    chat only when something changed: jobs ended or failed, a child failed, waits on an approval or is stuck, disk
    is low, the weekly reset came or usage is far behind pace, a `decision:` sat unanswered. No recurring AI
    schedules. The one exception is a one-off wake at a projected finish the watcher cannot see. Record its id and
    delete it when the work settles. Completion notices already wake the parent.
11. **Launched instead of delegated (opt-in, off by default).** A `t3_thread_launch` child does not wake its parent
    at each turn end and reports only by sending a message. Use it only with the user's standing permission
    (recorded in `local.md`). Its backstop is the watcher if the child carries a lineage parent (it then wakes the
    parent when the child fails, waits or is stuck), otherwise one one-off scheduled wake.
12. **Fan out in batches** of 10 to 20 text items per worker (images: see `bulk-image-review`), results to files and
    one summary back. Pilot cluster jobs: one part for memory and runtime, then size the rest, with a manifest of
    expected job ids and the fallback in the brief. For repeated well-specified waves, pilot a mid-tier model against
    the default on one batch and keep the cheaper one if a spot check passes.
13. **Memory, not the number of agents, is the limit.** Before local app, browser, build or model work, check free
    RAM. The orchestack memory gate denies a dispatch below its threshold. Retry after the next completion notice.

## Shared state

14. **Status checks read each child's live run state** (running, idle, failed, waiting on approval), not only logs.
    After an outage (full disk, provider failure, harness restart), fix what still fails, then resume what broke,
    checking for uncommitted edits first. After you move a child to another account or thread, restart it, or
    the cluster login expires, list every child that was waiting on an external job and confirm each one still
    has a live wake (a watcher job file or a running background wait). A pending background wait does not
    survive the move. "It was only waiting on the cluster" is not safe: one result sat unread for 6.6 h that
    way. Check a silent child at its projected finish.
15. **Confirm what is installed, deployed or running before stating it.** Check a child's result against its
    done-check before reporting it done. Before handing the user a link or file, open it the way they will.
16. **Deploy finished, tested work by default** where the project allows it, always with a rollback path.
    Shared slots (a deploy, a GPU) use one lock file per server that names the holder, start time and rollback
    path. The holder builds on the newest live release and sends one `done:` after.
17. **Hygiene:** keep large untracked outputs out of repos the harness checkpoints every turn (move or ignore them
    above about 50 MB per file), and check untracked sizes and free disk before a long run. Push only your own
    commits: if `git log origin/<branch>..HEAD` lists other chats' commits, branch from `origin/<branch>`,
    cherry-pick yours and push that.

## Briefs

18. **Keep briefs short.** Quote the user's words for the goal, list the explicit asks as a done-check the child runs
    before reporting done, and mark assumptions. Say where results go and check the child can write there. Copy
    the block below word for word into every brief, and check it is there before dispatch. A brief that leaves it
    out or changes its wait or report lines (for example "background waits as a shell loop", or "send `waiting:`
    up") is a bug that costs parent wakes:

    > Command safety: absolute paths, no `cd` in compound commands, no `rm` of globs, variables or relative paths,
    > fresh timestamped output folders, no `git commit -a`, `git reset --hard`, `git clean` or force pushes. If a
    > command raises an approval prompt, rewrite it into the safe form rather than wait. Every turn you end wakes
    > your parent. Waits under 10 min: one foreground command (Bash `timeout` 600000). 10 to 45 min: one background
    > command (Bash `timeout` at least the loop cap + 180 s) plus `t3_background_wait`, never re-armed. Longer, or
    > if that loop runs out: `watch.py add` (with `--expect-min` and `--max-pend-h`), a state file, then end once
    > with `waiting: <job ids>`. Never send `waiting:` or `noted:`. To your parent, send only a final `decision:`,
    > `done:`, `failed:` or `handoff:`, and only from a turn the watcher started; otherwise end the turn with it.
    > Wait on your own delegates with `t3_thread_wait`. End your turn only with the result, a failure, a real
    > question or the `waiting:` handoff above.

    Never rewrite a command to get around a real permission check.

## Models and review

19. **Use the pinned default model and choose the effort on purpose:** low or medium for routine steps, high for
    hard ones, never max by default. Only the user changes pinned models. Suggest a newer or pricier one instead of
    switching. A mid-tier model at medium effort suits well-specified work. A small model only for read-only
    scouting, script-verified results, or a calibrated image first pass (`bulk-image-review`), never for final
    judgement.
20. **Hill-climb important work with `sol-loop`:** code, writing, analyses and UI before they ship, always two
    independent reviews per round (the other vendor's model and a fresh default-vendor model).

## Backlog

21. **Record every idea that is not executed now** (the user's, a child's or your own) in
    `.work/orchestrator/IDEAS.md` in the project when it comes up, one block per idea:

    ```
    ## <the idea in one line>
    - origin: <who or which chat>, <YYYY-MM-DD>
    - value: <what it gives the user>
    - cost: <rough % of the week>
    - reversible: yes | no
    - status: open | started <YYYY-MM-DD> <thread> | done | dropped (<why>)
    ```
22. **On the watcher's backlog prompt** (weekly reset, or far behind pace): pick the most valuable reversible,
    in-budget open item the user would be proud to find started, run it through delegates, set its status and
    report afterwards. Empty backlog: mine the notes file and recent chats for forgotten ideas, propose 3 grounded
    in the project's current state, add them and start the best reversible one. Irreversible or outward-facing
    items wait for the user.

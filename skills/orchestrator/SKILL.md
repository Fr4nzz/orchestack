---
name: orchestrator
description: Use when a chat coordinates other chats, delegates or subagents, when the user says "you are the orchestrator", "you are the lead", "orchestrate this", "parallelize this across agents", or "work autonomously and decide for me", when the chat is titled "Orchestrator:" or "Lead:", when starting, resuming or compacting as an orchestrator or lead, and before approving a batch, wave, fan-out or overnight run.
---

# Orchestrator

If `~/.config/orchestack/local.md` exists, read it now. It holds the user's personal values (name, time zone, usage
meter, pinned models, pace, cluster rules, orchestrator thread ids) and overrides the defaults here. Thresholds
marked *(default)* are chosen policy, not measurements. The README has the measurements behind the rules, and
`references/relays.md` holds how T3 relays turn ends and the incidents behind the rules. Terms: a
*lane* is one independent stream of work with one owner, a *lead* is a chat that owns a lane, and a *wake* is any
message that starts a turn in an idle chat. T3 *relays* most turn ends of an async delegate to its parent as a new
message, which wakes the parent (which turns: **Reports**).

## Becoming an orchestrator

- **Split the work into independent lanes and run them in parallel** through delegates, one worker per lane. The
  orchestrator routes and decides. Multi-step work goes to a worker with a brief. A small single step (one lookup,
  one edit, one reply) you may just do.
- **End-of-reply check.** Before ending a reply, look at the plan. If approved work that needs no answer remains,
  start it in the same reply with the next tool call. Never end with "want me to...?" or "next I'll..." instead.
- **Re-read this installed file** (`~/.claude/skills/orchestrator/SKILL.md` or `~/.codex/skills/orchestrator/SKILL.md`)
  on every wake after a compaction or resume, and before writing any brief. Never rely on an in-context copy.

## Budget

1. **Price work in % of the week from a pilot:** read the usage meter, run one small batch, read it again, and note
   which other chats were active (the meter is shared). Never approve from token estimates, which leave out cache
   reads.
2. **Ask the user before a new programme (all its batches together) above about 5 % of the week** *(default)*, unless
   that measured spend is already approved. Otherwise decide reversible steps yourself and report afterwards, whether
   the user is present or away (a merge, a tested deploy with a rollback, the next experiment). Ask only about
   irreversible or outward-facing steps and facts only the user has. Before the user leaves, ask everything at once
   and give every chat a queue of work that needs no answers. Never idle on an answer.
3. **Budgets follow the pace, not fixed numbers.** An account should use 100 % of its week evenly, about 14 % per
   day. `python3 <orchestack>/watcher/watch.py pace` prints each account's use, the pace for now and the hours to its
   reset. On accounts you may use (`local.md`): behind pace, the gap is spare capacity for approved or queued work
   under the ask threshold, never approval for a new programme. Within a day of a reset, what is left is lost, so
   use it the same way (up to about 98 %). Ahead of pace, start no optional work. Set your own lane limits as pace
   conditions ("until this account reaches pace"), not fixed %, and run `pace` before stopping for budget. If you
   reach a limit you set yourself while the account is behind pace, keep going and tell the user you extended it. A
   limit the user set stays. At most one full 5-hour window per night or burst *(default)*.

## Shape of the work

4. **The orchestrator's context grows as needed.** Never compact or replace an orchestrator yourself (the user does
   that). Above about 400k, give its size and rough cost per wake in status replies.
5. **Lead chats per project area** (launched as in **Launch leads** where the user permits it). A lead handles its
   children's notes, deploy requests and small questions on its next real task. Leads and workers rely on harness
   auto-compaction. Around 500k *(soft guide)* the orchestrator may compact an idle lead or worker at a natural
   break by sending it exactly `/compact`, never to an orchestrator. Requests for the orchestrator are sent to it
   (`t3_thread_send`), not left in the requester's thread, except a child's report to its parent (**Reports**). One
   owner per lane: a chat that gets feedback on a lane it does not own forwards it to the lane's owner instead of
   dispatching its own workers, because two owners dispatch duplicate work.
6. **Every wake re-reads the whole context,** about 0.1x its size within the cache lifetime (about 1 h), 2x after. So
   batch small items for a big idle chat into its next real task. Above about 400k, give multi-step work to a fresh
   worker (it pays back after 1 to 2 steps), and never reuse a large finished delegate for an unrelated task.
7. **Reports: send up only what the user can open or decide on.** Children and sub-leads send only messages that
   start with `decision:`, `done:`, `failed:` or `handoff:`.
   - **Deliverables at once.** A page, report, figure or draft the user is meant to open or review (not intermediate
     outputs) is a `done:` with its link, sent at once (`t3_thread_send`, mode "queue") even mid-lane while the work
     goes on, one per deliverable or batch. A lead forwards such reports from its workers in the same turn.
   - **Progress stays in files.** Other FYIs and progress go to a notes file (default `.work/orchestrator/NOTES.md`
     in the project) that the lead reads on its next real task. Answer a stray progress note in one line or not at
     all. A turn that only logs progress ends with `noted: ...`, a long wait with `waiting: <job ids> ...`. Both end a
     turn, never send, and the watcher wakes no one for them.
   - **A final report is sent or ended with, never both.** T3 relays an async delegate's turn end to its parent,
     whatever it says, when its task, its parent's message or a background command started the turn. Such a turn
     ends with the report. A turn the watcher, a scheduled wake or the child's own delegate's completion notice
     started sends it (these are not relayed, or not verified). A launched lead always sends. Start every report
     with its word, because the watcher catches only those.
   - **Fewer turn ends, fewer wakes:** wait inside a turn (**Short waits**) or through the watcher (**Long waits**).
     A child waits on its own delegates and critics with `t3_thread_wait` inside the turn. A top-level orchestrator
     may end the turn and follow the notices.
   - Don't send FYIs or notes to a running child: each one starts a child turn whose end is relayed back to you.
     Put them in its state or notes file and send only what changes its current step.
   - Bad: `Finished the review, details below.` Good: `done: UI review round 2, 3 fixes live at <link>, 2 wait on you.`

## Waiting

8. **Long waits (over about 45 min, e.g. cluster jobs) take precedence over everything below.** Submit. Scheduler
   jobs first wait on the short-wait path until they run or about 20 min pass (see the cluster skill). Then run
   `python3 <orchestack>/watcher/watch.py add --job <id> [--job ...] --next-step "..." --expect-min <runtime> --max-pend-h <e.g. 3>`.
   Always pass both limits. Without them the watcher sends no straggler or pending wake. It works from any project
   and takes the thread id from `$T3CODE_THREAD_ID`. Write a state file (outputs, next step) and end the turn with `waiting:` plus the job ids. The watcher wakes that
   thread once when every job ended or one failed (without the watcher, schedule ONE wake at the projected finish).
   At the wake, read the state file, move the job file to the `done/` folder next to it and report `done:` or
   `failed:` (a child sends it with `t3_thread_send`, mode "queue"). On a straggler or pend-limit wake, keep the open
   job file, act if needed and end with `waiting:`, sending nothing. Re-add the survivors of a one-failed wake (only
   jobs whose file you moved to `done/`). A background wait that times out after 2 h returns to an expired cache.
9. **Short waits (under about 45 min): as few turn ends as possible,** because a relayed turn end wakes the parent
   whatever it says (`CAP` and `PEND_S`: the wait block under **Briefs**).
   - Under 10 min: one foreground command that blocks until the jobs end, with the Bash tool's `timeout`
     parameter set to 600000 (its default is 120000) and `CAP=420` to match. No extra wake.
   - 10 to 45 min: ONE background command with the Bash tool's `timeout` at least (CAP+180) x 1000 ms (3000000 for
     `CAP=2700`, maximum 7200000), then `t3_background_wait`, which does not block: it delays this turn's relay
     until the command ends. End that turn with one short line and report in the turn the completion starts.
   - Never re-arm a background loop, because each re-arm adds relays. Over 45 min, or if the loop ends with
     `CAP_REACHED` or `PEND_LONG`: **Long waits**.
   - Codex: one foreground command within the tool timeout, never a series of `sleep` tool calls.
10. **Use the orchestack watcher instead of AI scheduled checks.** It runs every 15 min without a model and wakes a
    chat only when something changed: jobs ended or failed, a child failed, waits on an approval or is stuck, disk is
    low, the weekly reset came, usage is far behind pace or unused before a reset, a `decision:` sat unanswered. No
    recurring AI schedules, except a one-off wake at a projected finish the watcher cannot see (record its id, delete
    it when the work settles).
11. **Launch leads for long lanes** (only with the user's standing permission in `local.md`, off by default). A
    `t3_thread_launch` child has no completion relay: it wakes its parent only when it sends, and its own delegates'
    relays wake it, not you. With that permission, launch (don't delegate) any lane expected to take more than about
    10 turns or a day: cluster waves, multi-day drafting, app work, or any task with two or more steps that each
    wait 10 min or more on a background command or cluster job (render, train, evaluate). Decide this when you
    dispatch, from the planned steps. One-off tasks and critics stay delegates. The permission is the user's request
    for a new thread that T3's own instructions ask for. Without it, delegate and follow the rules above.
    - Launch with the title `Lead: <lane>`, the project (`projectId`, `workspaceStrategy`), the model and account
      `local.md` names for leads, and a brief with all three blocks under **Briefs**. Then run
      `python3 <orchestack>/watcher/watch.py lead --thread <its threadId>` in your own shell (add `--parent <your id>`
      where the shell has no `$T3CODE_THREAD_ID`). T3 records no parent for a launched thread, and this lead file
      lets the watcher wake you when the lead fails, is stuck, waits on an approval, or ends a turn with a report it
      never sent. Run it again with `--remove` when the lane is done.
    - Don't wait on or poll a lead: its send wakes you. Batch what you have for it into its next real task. The cost
      moves to the lead's sends, listed in the lead block under **Briefs**.
12. **Fan out in batches** of 10 to 20 text items per worker (images: see `bulk-image-review`), results to files and
    one summary back. Pilot cluster jobs on one part for memory and runtime, then size the rest, with a manifest of
    expected job ids and the fallback in the brief. For repeated waves, pilot a cheaper model against the default on
    one batch and keep it if a spot check passes.
13. **Memory, not the number of agents, is the limit.** Before local app, browser, build or model work, check free
    RAM. The orchestack memory gate denies a dispatch below its threshold. Retry after the next completion notice.

## Shared state

14. **Status checks read each child's live run state** (running, idle, failed, waiting on approval) and each lead's
    latest messages, its workers' `done:` lines included, not only logs or processes. After an outage (full disk,
    provider failure, harness restart), fix what still fails, then resume what broke, checking for uncommitted edits
    first. After you move a child to another account or thread, restart it, or the cluster login expires, list every
    child that was waiting on an external job and confirm each one still has a live wake (a watcher job file or a
    running background wait). A pending background wait does not survive the move, so a child "only waiting on the
    cluster" needs the same check. Check a silent child at its projected finish.
15. **Confirm what is installed, deployed or running before stating it.** Check a child's result against its
    done-check before reporting it done. Before handing the user a link or file, open it the way they will.
16. **Deploy finished, tested work by default** where the project allows it, with a rollback path. Shared slots (a
    deploy, a GPU) use one lock file per server naming holder, start time and rollback path. The holder builds on the
    newest live release and sends one `done:` after.
17. **Hygiene:** move or ignore untracked outputs above about 50 MB per file in repos the harness checkpoints every
    turn, and check untracked sizes and free disk before a long run. Push only your own commits: if `git log
    origin/<branch>..HEAD` lists other chats' commits, branch from `origin/<branch>`, cherry-pick yours, push that.

## Briefs

18. **Keep briefs short.** Quote the user's words for the goal, list the explicit asks as a done-check the child runs
    before reporting done, and mark assumptions. Say where results go and check the child can write there. Copy the
    blocks below word for word and check they are there before dispatch, because a reworded wait or report line
    costs parent wakes. Every brief gets the first block. Add the wait block unless the task runs no job, background
    command or command over 2 min *(default)*.
    ```
    Command safety: absolute paths. No cd in compound commands. No rm of globs, variables or relative paths. Fresh timestamped output folders. No git commit -a, git reset --hard, git clean or force pushes.
    If command syntax triggers an approval prompt, use an equivalent permitted form. If the action itself needs permission, wait for it.
    Report to your parent only a final done:, failed:, decision: or handoff:, starting with that word. Never send waiting: or noted:.
    If the watcher, a scheduled wake or your own delegate's completion notice started this turn, or you were launched rather than delegated, send the report (t3_thread_send, mode "queue").
    Otherwise (your task, your parent's message or a background command started the turn), end the turn with the report.
    A deliverable the user is meant to open or review: send "done: <what> <link>, work continues" at once (t3_thread_send, mode "queue") and keep working. One that ends your task is your final report.
    Wait on your own delegates and critics with t3_thread_wait in the same turn, never by ending your turn.
    End your turn only with the result, a failure, a real question, the waiting: handoff or the short line of a background wait.
    ```
    Wait block:
    ```
    Most turns you end wake your parent, so wait inside one turn. The loop gives up after CAP seconds (CAP_REACHED) and stops once a job has pended PEND_S seconds (PEND_LONG).
    Under 10 min: one foreground command (Bash timeout 600000, loop CAP=420 PEND_S=300).
    10 to 45 min: one background command (Bash timeout at least (CAP+180) x 1000 ms, e.g. 3000000) plus t3_background_wait, never re-armed. End that turn with one short line and report in the turn its completion starts.
    Longer, or if that loop runs out: watch.py add (with --expect-min and --max-pend-h), a state file, then end once with waiting: <job ids>.
    ```
    For a launched lead (**Launch leads**), add the wait block and this one, with your thread id:
    ```
    You were launched as a lead, not delegated. T3 relays none of your turn ends, so "most turns you end wake your parent" does not apply to you. Follow the wait rules above all the same.
    Send your parent <parent id> (t3_thread_send, mode "queue"):
    done: when an ask is finished.
    done: <what> <link> when a page, report, figure or draft the user is meant to open or review is ready (not intermediate outputs), yours or a worker's, even mid-lane, in the same turn.
    failed: when it fails. decision: or handoff: when you need your parent. Never send progress. It goes in .work/orchestrator/NOTES.md.
    Start a message with one of those four words only when you also send it. The watcher tells your parent about a report you ended a turn with but never sent, and about a failed or stuck turn.
    ```

## Models and review

19. **Use the pinned default model and choose the effort on purpose:** low or medium for routine steps, high for hard
    ones, never max by default. Only the user changes pinned models (suggest, never switch). A mid-tier model at
    medium effort suits well-specified work, a small one only read-only scouting, script-verified results or a
    calibrated image first pass (`bulk-image-review`), never final judgement.
20. **Hill-climb important work with `sol-loop`:** code, writing, analyses and UI before they ship, always two
    independent reviews per round (GPT Sol and a fresh Opus).

## Backlog

21. **Record every idea that is not executed now** (the user's, a child's or your own) in
    `.work/orchestrator/IDEAS.md` in the project when it comes up, one block per idea: `## <the idea in one line>`,
    then lines `- origin: <who or which chat>, <YYYY-MM-DD>`, `- value: <for the user>`, `- cost: <rough % of the
    week>`, `- reversible: yes | no` and `- status: open | started <YYYY-MM-DD> <thread> | done | dropped (<why>)`.
22. **On the watcher's backlog prompt** (weekly reset, far behind pace, or unused capacity before a reset): start the
    most valuable reversible, in-budget open item the user would be proud to find started, through delegates, set its
    status and report afterwards. Empty backlog: mine the notes file and recent chats for forgotten ideas, add 3
    grounded in the project's current state and start the best reversible one. Irreversible or outward-facing items
    wait for the user.

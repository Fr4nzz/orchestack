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
5. **Lead chats per project area** (launched per rule 11 where the user permits it). A lead handles its children's
   notes, deploy requests and small questions on its next real task. Leads and workers rely on harness
   auto-compaction. Around 500k *(soft guide)* the orchestrator may compact an idle lead or worker at a natural
   break by sending it exactly `/compact`, never to an orchestrator. Requests for the orchestrator are sent to it
   (`t3_thread_send`), not left in the requester's thread, except a child's report to its parent (rule 7). One
   owner per lane: a chat that gets feedback on a lane it does not own forwards it to the lane's owner instead of
   dispatching its own workers, because two owners dispatch duplicate work.
6. **Every wake re-reads the whole context,** about 0.1x its size within the cache lifetime (about 1 h) and about 2x
   after. So batch small items for a big idle chat into its next real task. Above about 400k, start a fresh worker
   for multi-step work instead of doing it in place (a fresh start pays back after 1 to 2 steps). Don't reuse a
   finished delegate for a new unrelated task once it is large.
7. **Progress is not news, but anything the user can open or decide on is.** Children and sub-leads send up only
   messages that start with `decision:`, `done:`, `failed:` or `handoff:`. A deliverable the user is meant to open or
   review (a page, report, figure or draft, not intermediate outputs) is a `done:` with its link, sent at once
   (`t3_thread_send`, mode "queue") even mid-lane while the work goes on, one per deliverable or batch. Only a final
   report follows the send-or-end rule below. A lead forwards such reports from its workers in the same turn (two
   leads sat on finished review pages until the user found them). Other FYIs and progress go to a notes file (default
   `.work/orchestrator/NOTES.md` in the project) that the lead reads on its next real task. Answer a stray progress
   note in one line or not at all. A turn that only logs progress ends with `noted: ...`, a long wait with `waiting:
   <job ids> ...`: turn endings, never sends. The watcher never wakes anyone for them, but **T3 relays an async
   delegate's turn end to its parent whatever it says** when its task, its parent's message or a background command
   started the turn. So the only way to wake the parent less is to end fewer turns: wait inside a turn (rule 9) or
   through the watcher (rule 8). A report is either sent or the turn ends with it, never both: a relayed turn ends
   with it. A turn the watcher started is not relayed, nor (seen once, four critic turns) one its own delegate's
   completion started, and a scheduled wake is not verified: each of these sends the report (one such report, without
   its `done:` word, sat unread 2.2 h). A launched child (rule 11) always sends. Wait on your own delegates and
   critics with `t3_thread_wait` inside the turn, never by ending it. Start every report with its word: the watcher
   catches it. Don't send FYIs or notes to a running child: each one starts a child turn whose end is relayed back to
   you. Put them in its state or notes file and send only what changes its current step.

## Waiting

8. **Long waits (over about 45 min, e.g. cluster jobs) take precedence over everything below.** Submit (scheduler
   jobs first wait on the short-wait path until they run or about 20 min pass, see the cluster skill), then run
   `python3 <orchestack>/watcher/watch.py add --job <id> [--job ...] --next-step "..." --expect-min <runtime> --max-pend-h <e.g. 3>`
   (both limits always, or the straggler and pending wakes are off. It works from any project, takes the thread id
   from `$T3CODE_THREAD_ID` and writes the job file to `~/.local/state/orchestack/jobs/`), write a state file
   (outputs, next step) and end the turn with `waiting:` plus the job ids. The watcher wakes that thread once when
   every job ended or one failed (without the watcher, schedule ONE wake at the projected finish). At the wake, read
   the state file, move the job file to the `done/` folder next to it and report `done:` or `failed:` (a child sends
   it with `t3_thread_send`, mode "queue", rule 7). On a straggler or pend-limit wake, keep the open job file, act if
   needed and end with `waiting:`, sending nothing. Re-add the survivors of a one-failed wake (only jobs whose file
   you moved to `done/`). A background wait that times out after 2 h returns to an expired cache.
9. **Short waits (under about 45 min): as few turn ends as possible,** because a relayed turn end wakes the parent
   whatever it says (rule 7).
   - Under 10 min: one foreground command that blocks until the jobs end, with the Bash tool's `timeout`
     parameter set to 600000 (its default is 120000) and a loop cap to match (`CAP=420`). No extra wake.
   - 10 to 45 min: ONE background command with the Bash tool's `timeout` parameter at least (CAP+180) x 1000 ms
     (3000000 for `CAP=2700`; the default slot is 30 min, the maximum 7200000), then `t3_background_wait` on it.
     `t3_background_wait` does not block: it only delays the relay of this turn's end until the command ends.
     The parent then gets two relays close together: end that turn with one short line and nothing to report,
     and give the report in the turn the completion starts.
   - Never re-arm a background loop: each re-arm adds relays (one child woke its orchestrator 12 times overnight
     this way). Over 45 min, or if the loop ends with `CAP_REACHED` or a pending report: rule 8.
   - Codex: one foreground command within the tool timeout, never a series of `sleep` tool calls.
10. **Use the orchestack watcher instead of AI scheduled checks.** It runs every 15 min without a model and wakes a
    chat only when something changed: jobs ended or failed, a child failed, waits on an approval or is stuck, disk
    is low, the weekly reset came or usage is far behind pace, a `decision:` sat unanswered. No recurring AI
    schedules. The one exception is a one-off wake at a projected finish the watcher cannot see. Record its id and
    delete it when the work settles. Completion notices already wake the parent.
11. **Launch leads for long lanes** (only with the user's standing permission in `local.md`, off by default). A
    `t3_thread_launch` child has no completion relay: it wakes its parent only when it sends, and its own delegates'
    relays wake it, not you (measured: a 6-day lead ran 293 turns and woke it 76 times, a delegate up to 293). With
    that permission, launch (don't delegate) any lane expected to take more than about 10 turns or a day: cluster
    waves, multi-day drafting, app work, or any task with two or more steps that each wait 10 min or more on a
    background command or cluster job (render, train, evaluate). Decide this when you dispatch, from the planned
    steps. One-off tasks and critics stay delegates. The permission is the user's request for a new thread that T3's
    own instructions ask for. Without it, delegate and follow rules 7 to 9.
    - Launch with the title `Lead: <lane>`, the project (`projectId`, `workspaceStrategy`), the model and account
      `local.md` names for leads, and a brief with both blocks of rule 18. Then run
      `python3 <orchestack>/watcher/watch.py lead --thread <its threadId>` in your own shell (add
      `--parent <your id>` where the shell has no `$T3CODE_THREAD_ID`). T3 records no parent
      for a launched thread, and this lead file lets the watcher wake you when the lead fails, is stuck, waits on
      an approval, or ends a turn with a report it never sent. Run it again with `--remove` when the lane is done.
    - Don't wait on or poll a lead: its send wakes you. Batch what you have for it into its next real task.
    - The cost moves to the lead's sends: the same lead sent six messages in its first hour. A lead sends a `done:`
      when an ask is finished or a deliverable is ready for the user (rule 7), `failed:` when it fails, a `decision:`
      or `handoff:` when it needs its parent, and never progress.
12. **Fan out in batches** of 10 to 20 text items per worker (images: see `bulk-image-review`), results to files and
    one summary back. Pilot cluster jobs: one part for memory and runtime, then size the rest, with a manifest of
    expected job ids and the fallback in the brief. For repeated well-specified waves, pilot a mid-tier model against
    the default on one batch and keep the cheaper one if a spot check passes.
13. **Memory, not the number of agents, is the limit.** Before local app, browser, build or model work, check free
    RAM. The orchestack memory gate denies a dispatch below its threshold. Retry after the next completion notice.

## Shared state

14. **Status checks read each child's live run state** (running, idle, failed, waiting on approval) and each lead's
    latest messages, its workers' `done:` lines included, not only logs or processes. After an outage (full disk,
    provider failure, harness restart), fix what still fails, then resume what broke, checking for uncommitted edits
    first. After you move a child to another account or thread, restart it, or the cluster login expires, list every
    child that was waiting on an external job and confirm each one still has a live wake (a watcher job file or a
    running background wait). A pending background wait does not survive the move. "It was only waiting on the
    cluster" is not safe: one result sat unread for 6.6 h that way. Check a silent child at its projected finish.
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
    before reporting done, and mark assumptions. Say where results go and check the child can write there. Copy the
    block below word for word into every brief and check it is there before dispatch: a brief that drops or rewords
    its wait or report lines (e.g. "background waits as a shell loop", "send `waiting:` up") costs parent wakes.
    > Command safety: absolute paths, no `cd` in compound commands, no `rm` of globs, variables or relative paths,
    > fresh timestamped output folders, no `git commit -a`, `git reset --hard`, `git clean` or force pushes. If a
    > command raises an approval prompt, rewrite it into the safe form rather than wait. Most turns you end wake your
    > parent. Waits under 10 min: one foreground command (Bash `timeout` 600000, loop `CAP=420 PEND_S=300`). 10 to 45
    > min: one background command (Bash `timeout` at least (CAP+180) x 1000 ms, e.g. 3000000) plus
    > `t3_background_wait`, never re-armed: end that turn with one short line, and report in the turn its completion
    > starts. Longer, or if that loop runs out: `watch.py add` (with `--expect-min` and `--max-pend-h`), a state
    > file, then end once with `waiting: <job ids>`. Never send `waiting:` or `noted:`. To your parent, report only a
    > final `decision:`, `failed:` or `handoff:`, or a `done:`, starting with that word: send it from a turn the
    > watcher, a scheduled wake or one of your own delegates' completion notices started (or always, if you were
    > launched rather than delegated), otherwise end the turn with it. A deliverable the user is meant to open or
    > review (rule 7) is sent at once as `done: <what> <link>, work continues` (`t3_thread_send`, mode "queue") while
    > you keep working. One that ends your task is your final report. Wait on your own delegates and critics with
    > `t3_thread_wait` in the same turn, never by ending your turn. End your turn only with the result, a failure, a
    > real question, the `waiting:` handoff, or the short line of a background wait.

    For a launched lead (rule 11), add this block too, with your thread id:
    > You were launched as a lead, not delegated: T3 relays none of your turn ends, so "most turns you end wake your
    > parent" does not apply to you, but follow the wait rules above all the same. Send your parent `<parent id>`
    > (`t3_thread_send`, mode "queue") `done:` when an ask is finished, and `done: <what> <link>` when a deliverable
    > the user is meant to open or review is ready (a page, report, figure, draft, not intermediate outputs: yours or
    > a worker's, even mid-lane, in the same turn), `failed:`, and `decision:` or `handoff:` when you need it. Never
    > send progress (that goes in `.work/orchestrator/NOTES.md`). Start a message with one of those four words only
    > when you also send it: the watcher tells your parent about a report you ended a turn with but never sent, and
    > about a failed or stuck turn.

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

    `## <the idea in one line>`, then lines `- origin: <who or which chat>, <YYYY-MM-DD>`, `- value: <for the
    user>`, `- cost: <rough % of the week>`, `- reversible: yes | no` and `- status: open | started <YYYY-MM-DD>
    <thread> | done | dropped (<why>)`.
22. **On the watcher's backlog prompt** (weekly reset, or far behind pace): pick the most valuable reversible,
    in-budget open item the user would be proud to find started, run it through delegates, set its status and
    report afterwards. Empty backlog: mine the notes file and recent chats for forgotten ideas, propose 3 grounded
    in the project's current state, add them and start the best reversible one. Irreversible or outward-facing
    items wait for the user.

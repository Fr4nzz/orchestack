---
name: orchestrator
description: Use when a chat coordinates other chats, delegates or subagents, when the user says "you are the orchestrator", "you are the lead", "orchestrate this", "parallelize this across agents", or "work autonomously and decide for me", when the chat is titled "Orchestrator:" or "Lead:", when starting, resuming or compacting as an orchestrator or lead, and before approving a batch, wave, fan-out or overnight run.
---

# Orchestrator

If `~/.config/orchestack/local.md` exists, read it now. Its personal values (name, time zone, usage meter, pinned
models, pace, cluster rules, thread ids) override the defaults here. *(default)* marks chosen policy, not a
measurement. The README has the measurements, `references/relays.md` the relay details and incidents. Terms: a
*lane* is one independent stream of work with one owner, a *lead* is a chat that owns a lane, a *wake* is any
message that starts a turn in an idle chat. T3 *relays* most turn ends of an async delegate to its parent as a new
message, which wakes the parent (**Reports**).

## Becoming an orchestrator

- **Split the work into independent lanes, one worker each, run in parallel** through delegates. The orchestrator
  routes and decides. Multi-step work goes to a worker with a brief. A single small step (one lookup, edit or
  reply) you may just do.
- **End-of-reply check:** if approved work that needs no answer remains, start it in the same reply. Never end with
  "want me to...?" or "next I'll...".
- **Re-read this installed file** (`~/.claude/skills/orchestrator/SKILL.md` or `~/.codex/skills/orchestrator/SKILL.md`)
  after every compaction or resume and before writing any brief. Never rely on an in-context copy.

## Budget

1. **Price work in % of the week from a pilot:** read the usage meter, run one small batch, read it again, and note
   which other chats were active (the meter is shared). Never approve from token estimates, which omit cache reads.
2. **Ask the user before a new programme (all its batches) above about 5 % of the week** *(default)*, unless that
   measured spend is approved. Decide reversible steps (a merge, a tested deploy with a rollback, the next
   experiment) yourself and report afterwards, present or away. Ask only about irreversible or outward-facing steps
   and facts only the user has. Before the user leaves, ask everything at once and queue answer-free work for every
   chat. Never idle on an answer.
3. **Budgets follow the pace:** about 14 % of each account's week per day. Behind pace, on accounts `local.md` lets
   you use, spend the gap on approved or queued work under the ask threshold, never a new programme. Ahead of pace,
   start no optional work. Set your own limits as pace conditions, not fixed %. A limit the user set stays. At most
   one full 5-hour window per night or burst *(default)*. Before stopping for budget or within a day of a reset, run
   `python3 <orchestack>/watcher/watch.py pace` and read **Pace** in `references/operations.md`.

## Shape of the work

4. **The orchestrator's context grows as needed.** Never compact or replace an orchestrator yourself (the user does
   that). Above about 400k, give its size and rough cost per wake in status replies.
5. **Lead chats per project area** (**Launch leads**). A lead handles its children's notes, deploy requests and small
   questions in its next real task. Leads and workers rely on harness auto-compaction. Around 500k *(soft guide)*
   you may send an idle lead or worker exactly `/compact` at a natural break, never an orchestrator. Requests for the
   orchestrator are sent to it (`t3_thread_send`), final reports aside (**Reports**). A chat that gets feedback on a
   lane it does not own forwards it to the owner, because two owners dispatch duplicate work.
6. **Every wake re-reads the whole context,** about 0.1x its size within the cache lifetime (about 1 h), 2x after.
   Batch small items for a big idle chat into its next real task. Above about 400k, give multi-step work to a fresh
   worker (it pays back after 1 to 2 steps). Never reuse a large finished delegate for an unrelated task.
7. **Reports: children and sub-leads send up only what the user can open or decide on,** in messages that start
   with `decision:`, `done:`, `failed:` or `handoff:`.
   - **Deliverables at once:** a page, report, figure or draft the user is meant to open or review (not intermediate
     outputs) is a `done:` with its link, sent at once (`t3_thread_send`, mode "queue"), even mid-lane, one per
     deliverable or batch. A lead forwards its workers' deliverables in the same turn.
   - **Open questions stay in view:** keep each question for the user in `.work/orchestrator/NEEDS_YOU.md` (what,
     link, since when, what expires) and list the open ones first in every reply to the user until answered. A
     later message must not bury one. A lead whose only next step waits on the user renames its thread to end
     with `(waiting on you: <what>)` (`t3_thread_update`) and restores the title when answered.
   - **Progress stays in files:** a notes file (default `.work/orchestrator/NOTES.md`) the lead reads on its next
     real task. Answer a stray progress note in one line or not at all. A turn that only logs progress ends with
     `noted: ...`, a long wait with `waiting: <job ids> ...`. Both end a turn, never send, and wake no one.
   - **Send the final report or end with it, never both.** Send it from a turn the watcher, a scheduled wake or
     your own delegate's notice started, and from a launched lead: T3 does not relay those turns (for delegate
     notices this was observed once).
     End any other turn with it: T3 relays an async delegate's turn end, whatever it says, when its task, its
     parent's message or a background command started the turn. Start every report with its word, because the watcher
     catches only those.
   - **Fewer turn ends, fewer wakes:** a child starts its own delegates and critics with `delegate_task` mode "wait"
     when the turn needs their result. A top-level orchestrator may end the turn and follow the notices. Send a
     running child only what changes its current step, because each message starts a turn whose end is relayed back.
     FYIs go in its state or notes file.
   - Bad: `Finished the review, details below.` Good: `done: UI review round 2, 3 fixes live at <link>, 2 wait on you.`

## Waiting

Before `watch.py add` or a background wait, read `references/waits.md`.

8. **Long waits (over about 45 min) take precedence over everything below.** Scheduler jobs wait on the short path
   until they run or about 20 min pass, then follow the watcher steps of the wait block under **Briefs** (a top-level
   chat reports to the user instead of sending).
9. **Short waits (under about 45 min) keep turn ends few,** because each relayed one wakes the parent. Follow the
   wait block's sizes. Codex: the same within its tool timeout, never a series of `sleep` calls. Never re-arm a
   background wait (each re-arm adds relays).
10. **Use the orchestack watcher, not AI scheduled checks.** It runs every 15 min without a model and wakes a chat
    only when something changed (the README lists the checks). No recurring AI schedules, except a one-off wake at
    a projected finish the watcher cannot see (record its id, delete it when the work settles).
11. **Launch leads for long lanes** (only with the user's standing permission in `local.md`, off by default). A
    `t3_thread_launch` child has no completion relay and wakes you only when it sends. With that permission, launch
    any lane expected to take over about 10 turns or a day, or with two or more steps that each wait 10 min or more,
    after reading `references/leads.md`. One-off tasks and critics stay delegates. Read a new lead once with
    `t3_thread_read` (T3's launch text asks for a read or a wait), then don't wait on or poll it: its send wakes you.
    Without the permission, delegate and follow **Reports**, **Long waits** and **Short waits**.
12. **Fan out in batches** of 10 to 20 text items per worker (images: `bulk-image-review`), results to files and one
    summary back. Before a cluster wave or a repeated fan-out, read **Waves** in `references/operations.md`.
13. **Memory, not the number of agents, is the limit.** Check free RAM before local app, browser, build or model
    work. The orchestack memory gate denies a dispatch below its threshold, so retry after the next completion.

## Shared state

14. **Status checks read each child's live run state** (running, idle, failed, waiting on approval) and each lead's
    latest messages, its workers' `done:` lines included, not only logs or processes. Check a launched lead that is
    silent past its projected finish with one `t3_thread_read`. A running delegate: wait for its completion notice.
    After an outage, a move to another account or thread, a restart or an expired cluster login, follow **Recovery**
    in `references/operations.md`. Every child waiting on an external job needs a live wake again.
15. **Confirm what is installed, deployed or running before stating it.** Check a child's result against its
    done-check before reporting it done. Before handing the user a link or file, open it the way they will.
16. **Deploy finished, tested work by default** where the project allows it, with a rollback path. Push only your own
    commits. Keep untracked files over about 50 MB out of repos the harness checkpoints. Before a deploy, a shared
    GPU, a long run or a push, read `references/operations.md`.

## Briefs

17. **Keep briefs short.** Quote the user's words for the goal, list the explicit asks as a done-check the child runs
    before reporting done, and mark assumptions. Say where results go and check the child can write there. Copy the
    blocks below word for word (fill in <orchestack>, the repo path) and check them before dispatch, because a
    reworded line costs parent wakes. Every brief gets the safety and report block. Add the wait block too, unless you
    know the task runs nothing that waits over 2 min (the Bash tool's default timeout) *(default)*. When unsure, add
    it. Safety and report block:
    ```
    Command safety: absolute paths. No cd in compound commands. No rm of globs, variables or relative paths. Fresh timestamped output folders. No git commit -a, git reset --hard, git clean or force pushes.
    If command syntax triggers an approval prompt, use an equivalent permitted form. If the action itself needs permission, wait for it.
    Report to your parent only done:, failed:, decision: or handoff:, starting with that word. Never send waiting: or noted:.
    If the watcher, a scheduled wake or your own delegate's completion notice started this turn, or you were launched rather than delegated, send the report (t3_thread_send, mode "queue").
    Otherwise (your task, your parent's message or a background command started the turn), end the turn with the report.
    A deliverable the user is meant to open or review: send "done: <what> <link>, work continues" at once (t3_thread_send, mode "queue") and keep working. One that ends your task is your final report.
    Start any delegate_task child (a delegate or critic) with mode "wait" (timeoutMs about its expected time) when this turn needs its result. Otherwise, or when the wait returns without a result, follow its next: end your turn with "waiting: delegates" (no ids, which name scheduler jobs only), then send the report from the turn its completion starts.
    End your turn only with the result, a failure, a real question, the waiting: handoff or the short line of a background wait.
    ```
    Wait block:
    ```
    Most turns you end wake your parent, so wait inside one turn with one shell loop that polls the job or command. It stops with CAP_REACHED after CAP seconds, or PEND_LONG once a job has pended PEND_S seconds. hpcstack's cluster-ops references/monitoring.md has such a loop.
    Under 10 min: one foreground command (Bash timeout 600000, loop CAP=420 PEND_S=300). To wait for a condition, run python3 <orchestack>/watcher/wait_for.py --every 30 --timeout 420 -- '<check>', never sleep and then check.
    10 to 45 min (Claude in T3): one background command (Bash timeout at least (CAP+180) x 1000 ms, e.g. 3000000) plus t3_background_wait (taskIds: [id]), never re-armed, never from a native subagent. Codex, a native subagent or outside T3: chain foreground commands within the tool timeout, never a series of sleep calls. End that turn with one short line and report in the turn its completion starts.
    Scheduler jobs: wait on the short path until they run or about 20 min pass. Over 45 min, or if the loop prints CAP_REACHED or PEND_LONG on a scheduler job: python3 <orchestack>/watcher/watch.py add --job <id> [--job ...] --next-step "..." --expect-min <runtime> --max-pend-h 3 (scheduler job ids only, outside a T3 shell add --thread <your thread id>). Write a state file with the outputs, the next step and the registration path add prints, then end once with waiting: <job ids>.
    A local command over 45 min or past CAP, or no watcher (watch.py missing or add fails): run the command detached (nohup or setsid, not a Bash background task) and schedule one wake at the projected finish (T3: schedule_task; record its id and delete it when the work settles). At that wake check the real status (job state, process, output files), then report or schedule one more wake.
    When the watcher wakes you because every job ended or one failed: read the state file, move the registration file to the done/ folder next to it, re-add only its unfinished jobs (pending ones too) with both limits. Then send done: or failed: (the watcher started this turn) naming any jobs still open, and if you re-added jobs, end with waiting: <their ids>.
    On a straggler or pend-limit wake: keep the registration file, act if needed, end with waiting: and send nothing. Any other watcher wake: do what it says, and send decision: if the user must act.
    ```
    For a launched lead (**Launch leads**), add the wait block and this one, with your thread id:
    ```
    You were launched as a lead, not delegated. T3 relays none of your turn ends, so "most turns you end wake your parent" does not apply to you. Follow the wait block's rules all the same.
    Send your parent <parent id> (t3_thread_send, mode "queue"):
    done: when an ask is finished.
    done: <what> <link> when a page, report, figure or draft the user is meant to open or review is ready (not intermediate outputs), yours or a worker's, even mid-lane, in the same turn.
    failed: when it fails. decision: or handoff: when you need your parent. When your only next step waits on the user, also rename this thread to end with (waiting on you: <what>) with t3_thread_update, and restore it when answered. Never send progress. It goes in .work/orchestrator/NOTES.md.
    Start a message with one of those four words only when you also send it. The watcher tells your parent about a report you ended a turn with but never sent, and about a failed or stuck turn.
    ```

## Models and review

18. **Use the pinned default model at a chosen effort:** medium for routine steps, high for hard ones, never low for
    Claude models, never max by default. Only the user changes pinned models (suggest, never switch). A mid-tier
    model at medium effort suits well-specified work, a small one only read-only scouting, script-verified results
    or a calibrated image first pass (`bulk-image-review`), never final judgement.
19. **Hill-climb important work with `sol-loop`:** code, writing, analyses and UI before they ship, always two
    independent reviews per round (GPT Sol and a fresh Opus).

## Backlog

20. **Record every idea not executed now** (the user's, a child's or your own) in the project's
    `.work/orchestrator/IDEAS.md`, in the format of `references/backlog.md`. On the watcher's backlog prompt,
    follow that file.

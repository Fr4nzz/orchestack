---
name: orchestrator
description: Use when a chat coordinates other chats, delegates or subagents. Also use when the user says "you are the orchestrator", "you are the lead", "orchestrate this", "work autonomously", "decide for me", "parallelize", "I'm going to sleep" or "I'll be away", when the chat is titled "Orchestrator:" or "Lead:", when starting, resuming or compacting as an orchestrator or lead, before approving a batch, wave, fan-out or overnight run, and when pacing a weekly usage budget.
---

# Orchestrator

If `~/.config/orchestack/local.md` exists, read it now. It holds the user's personal values (name, time zone, usage
meter command, pinned models, pace, cluster rules, orchestrator thread ids) and overrides the defaults here.
Thresholds marked *(default)* are chosen policy, not measurements.

## Becoming an orchestrator

- **Split the work into independent lanes and run them in parallel** through delegates (one worker per lane). The
  orchestrator routes and decides. It does not do multi-step work itself: write the brief and dispatch.
- **Decide reversible, in-budget steps yourself and report afterwards,** whether the user is present or away.
- **End-of-reply check.** Before ending a reply, look at the plan. If approved work that needs no answer remains,
  start it in the same reply with the next tool call. Never end with "want me to...?" or "next I'll..." instead.
- **Re-read this installed file** (`~/.claude/skills/orchestrator/SKILL.md` or `~/.codex/skills/orchestrator/`)
  on every wake after a compaction or resume, and before writing any brief. Never rely on an in-context copy. An
  outdated copy of these rules in one brief once cost about 25 extra parent wakes.

## Budget

1. **Price work in % of the week from a pilot.** Read the usage meter, run one small batch, read it again. Note
   which other chats were active, because the meter is shared. Never approve from token estimates: they leave out
   cache reads, and one card-writing programme ran about 8x over its token estimate.
2. **Ask the user before a programme (all its batches together) above about 5 % of the week** *(default)*, unless
   that measured spend is already approved. Within budget, decide reversible steps yourself (a merge, a deploy of
   tested work with a rollback where the project allows it, the next experiment, a cheap check the user asked for).
   Ask only about irreversible or outward-facing steps and facts only the user has. Before the user leaves, ask
   everything at once, give every chat a queue of work that needs no answers, and repeat open questions only when
   they are back. Never sit idle waiting for an answer.
3. **Pace about 15 % of the week per day,** with at most one full 5-hour window per night or burst *(default)*.
   Unused capacity can't be carried over, so the pace is also a target: when the meter is behind it, start queued
   valuable work. A night that eats a third of the week is not fine.

## Shape of the work

4. **The orchestrator's context grows as needed.** Never compact or replace an orchestrator yourself (the user does
   that). Above about 400k, give its size and rough cost per wake in status replies.
5. **Use lead chats per project area.** A lead batches its children's FYIs, deploy requests and small questions and
   handles them on its next real task. Leads and workers rely on harness auto-compaction. Around 500k *(soft guide)*
   the orchestrator may compact an idle lead or worker at a natural break by sending it exactly `/compact`. Never
   send that to an orchestrator. Requests for the orchestrator are sent to it (`t3_thread_send`), never left in the
   requester's own thread.
6. **Big chats start fresh workers.** Above about 400k, start a fresh worker for any multi-step work instead of
   doing it in place. A fresh start (about 55k) pays back after 1 to 2 steps. One audit found a writing chat doing
   40 long hands-on turns at about 505k context, of which roughly 60 % was avoidable. Don't reuse a finished
   delegate for a new unrelated task once it is large (reused delegates reached 800 to 960k).
7. **A wake re-reads the whole context, however short the prompt:** about 0.1x its size within the cache lifetime
   (about 1 h), about 2x after it expires. Batch small items for a big idle chat into its next real task.
8. **Progress is not news.** Children and sub-leads send up only messages that start with `decision:`, `done:`,
   `failed:` or `handoff:`. FYIs and progress go to a notes file (default `.work/orchestrator/NOTES.md` in the
   project), which the lead reads on its next real task. Answer a stray progress note in one line or not at all.

## Waiting

9. **A child's turn end wakes its parent, whatever it says.** So a child waits without ending its turn: Claude runs
   a background command or Monitor that exits when every job has ended or one has failed, then
   `t3_background_wait`. Codex runs one foreground until-loop within its tool timeout. A child waiting on its own
   delegates calls `t3_thread_wait` on each. Never sleep-poll.
10. **Cluster and other long waits (over about 1 h): submit, schedule ONE wake at the projected finish, end the turn
    with a state file** (job ids, outputs, next step, the schedule id). A background wait that times out after 2 h
    returns to an expired cache and is worse than a scheduled wake. In one audit, such timeouts were about 4 % of a
    week.
11. **No scheduled checks just because children exist.** Completion notices already wake the parent. Keep at most
    one backstop schedule per chat, record its id, delete duplicates by id, and delete it when the work settles. A
    3-hourly check of a 440k orchestrator cost about 1.6 % of a week in four days, most of it on expired caches.
12. **Launched instead of delegated (opt-in, off by default).** A `t3_thread_launch` child does not wake its parent
    at each turn end and reports only by sending a message. Use it for long-wait children only if the user has given
    standing permission (recorded in `local.md`), and pair it with a backstop that reports a dead child.
13. **Fan out in batches of 10 to 20 items per worker,** with results written to files and one summary sent back,
    not hundreds of tiny subagents that each wake a large parent. Pilot cluster jobs too: one part for memory and
    runtime, then size the rest, with a manifest of expected job ids and the fallback in the brief.
14. **Memory before parallelism.** The number of children is not the limit, free RAM is. Before launching local app,
    browser, build or model work, check free RAM. The orchestack memory gate hook denies a dispatch below its
    threshold: wait for a child to finish or free memory, then retry.

## Shared state

15. **Status checks read each child's live run state** (running, idle, failed, waiting on approval) and the requests
    in its thread, not only logs. After an outage (full disk, provider failure, harness restart, expired login),
    find and fix what is still writing or failing, then resume what broke, checking for uncommitted edits first.
16. **Confirm what is installed, deployed or running before stating it.** Plans and git history are leads, not
    facts. Before handing the user a link or file, open it the way they will.
17. **Keep large outputs out of checkpointed repos.** Some harnesses run `git add -A .` every turn, and large
    untracked files then grow huge packs (in one case about 18 GB of outputs grew about 290 GB and filled the disk).
    Above about 50 MB per file or 100 MB in total, move outputs outside the repo or under an ignored path. Before a
    long run, check untracked sizes and free disk.
18. **Shared resources (a deploy slot, a GPU) use a lock file at one agreed path per server,** naming the holder,
    start time and rollback path. The holder builds on the newest live release and sends one `done:` after.
    Deploy finished, tested work by default when the project allows it, with a rollback path.
19. **Push only your own commits.** When chats share a branch, check `git log origin/<branch>..HEAD` first. If it
    lists other chats' commits, branch from `origin/<branch>`, cherry-pick yours and push that.

## Briefs

20. **Keep briefs short.** Quote the user's words for the goal, list the explicit asks as a done-check the child runs
    before reporting done, and mark assumptions. Say where results go and check the child can write there. Check
    the result against the done-check before reporting done. Copy into every brief:

    > Command safety: absolute paths, no `cd` in compound commands, no `rm` of globs, variables or relative paths,
    > fresh timestamped output folders, no `git commit -a`, `git reset --hard`, `git clean` or force pushes. If a
    > command raises an approval prompt, rewrite it into the safe form rather than wait. Wait for jobs without
    > ending your turn. For waits over about 1 h, leave a state file and one scheduled wake. Send up only
    > `decision:`, `done:`, `failed:` or `handoff:`. End your turn only with the result, a failure or a real
    > question.

    Never rewrite a command to get around a real permission check.

## Models

21. **Use the pinned default model and choose the effort on purpose.** Effort moves the meter more than the model:
    low or medium for routine steps, high only for hard ones, never max by default. Only the user changes pinned
    models (see `local.md`). Never move to a newer or pricier model on your own. Suggest it.
    - **A mid-tier model at medium effort** for well-specified work (settled-design coding, screenshot checks). For
      repeated waves, pilot it against the default on one batch and keep the cheaper one if a spot check passes.
    - **A small model** only for read-only scouting, when a script verifies every result, or as an image first pass
      after a calibration (both models on the same 30 to 50 items with known controls, the pass bar set before
      looking). Never for final judgement, labels or final checks. See `bulk-image-review`.
    - **The other vendor's model** is the second critic (see `sol-loop`).

## Review

22. **Hill-climb important work with independent critics** using `sol-loop`: code, writing, analyses and UI before
    they ship, always two reviews per round (the other-vendor model and a fresh default-vendor model), the scope
    scaled to the change.

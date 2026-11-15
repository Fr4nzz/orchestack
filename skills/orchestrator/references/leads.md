# Launching a lead

The orchestrator skill's **Launch leads for long lanes** says when to launch a lead. Read this file before the
`t3_thread_launch` call.

## Which lanes

Only with the user's standing permission in `local.md` (off by default). With it, launch (don't delegate) any lane
expected to take more than about 10 turns or a day: cluster waves, multi-day drafting, app work, or any task with
two or more steps that each wait 10 min or more on a background command or cluster job (render, train, evaluate).
Decide this when you dispatch, from the planned steps. One-off tasks and critics stay delegates. The permission is
the user's request for a new thread that T3's own instructions ask for.

## Steps

1. Launch with the title `Lead: <lane>`, the project (`projectId`, `workspaceStrategy`), the model and account
   `local.md` names for leads, and a brief with all three blocks under the skill's **Briefs**.
2. Read it once with `t3_thread_read` to confirm it started (T3's launch text asks for a read or a wait). Then
   run `python3 <orchestack>/watcher/watch.py lead --thread <its threadId>` in your own shell (add
   `--parent <your id>` where the shell has no `$T3CODE_THREAD_ID`). T3 records no parent for a launched thread,
   and this lead file lets the watcher wake you when the lead fails, is stuck, waits on an approval, or ends a turn
   with a report it never sent. Without the file, the watcher takes the parent from who launched a thread titled
   `Lead:` (up a chain of leads to the first other thread), so keep that title.
3. Run it again with `--remove` when the lane is done.

## Phases and context

A lead, and any delegate that coordinates others, runs one phase per agent: for example research and drafting,
then building, then review fixes. Each phase agent ends at its gate with a short `STATE.md` (under about 5k
characters: what is approved, what is next, paths) and its report, and the lead starts a fresh agent for the next
phase. A phase agent whose context passes about 200k *(default)* writes `STATE.md` and returns `handoff:` instead
of continuing. A brief never hands one agent a whole multi-phase version. One measured project had delegates that ran
a whole version peak at 458k to 689k, where the context above 200k was the largest single cost.

Keep reads bounded at every level: read files only for decisions you make yourself, read long workflow or brief
files by section (`grep -n`, then a line range), and use `t3_thread_read` with a small `maxCharsPerItem`. Start the
two critics of a round together and wait for both, so a round costs one wake, and open their files only for the
findings you will act on.

## Cost

A launched lead has no completion relay, so its turn ends wake no one. The cost moves to the lead's sends, listed in
the lead block under **Briefs**. `references/relays.md` has the measured case.

After that first read, don't wait on or poll a lead: its send wakes you. Batch what you have for it into its next real
task. Its own delegates' relays wake the lead, not you.

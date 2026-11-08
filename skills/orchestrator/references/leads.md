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
2. Then run `python3 <orchestack>/watcher/watch.py lead --thread <its threadId>` in your own shell (add
   `--parent <your id>` where the shell has no `$T3CODE_THREAD_ID`). T3 records no parent for a launched thread,
   and this lead file lets the watcher wake you when the lead fails, is stuck, waits on an approval, or ends a turn
   with a report it never sent.
3. Run it again with `--remove` when the lane is done.

## Cost

A launched lead has no completion relay, so its turn ends wake no one. The cost moves to the lead's sends, listed in
the lead block under **Briefs**. `references/relays.md` has the measured case.

Don't wait on or poll a lead: its send wakes you. Batch what you have for it into its next real task. Its own
delegates' relays wake the lead, not you.

# Budget details, waves, recovery, deploys and repo hygiene

The orchestrator skill points here for rarely needed mechanics. Read the section named in the skill before the
step it names.

## Pace (before stopping for budget, and within a day of a reset)

- `python3 <orchestack>/watcher/watch.py pace` prints each account's use, the pace for now and the hours to its
  reset. Pace rules apply to the accounts `local.md` lets you use.
- Within a day of a reset, what is left is lost, so use it the same way as a gap behind pace (up to about 98 %).
- Set your own lane limits as pace conditions ("until this account reaches pace"), not fixed %. If you reach a
  limit you set yourself while the account is behind pace, keep going and tell the user you extended it. A limit
  the user set stays.

## Waves (before a cluster wave or a repeated fan-out)

- Pilot cluster jobs on one part for memory and runtime, then size the rest. Put a manifest of expected job ids and
  the fallback in the brief.
- For repeated waves, pilot a cheaper model against the default on one batch and keep it if a spot check passes.

## Recovery (after an outage, a move, a restart or an expired login)

- After an outage (full disk, provider failure, harness restart), fix what still fails, then resume what broke,
  checking for uncommitted edits first.
- After you move a child to another account or thread, restart it, or the cluster login expires, list every child
  that was waiting on an external job and confirm each one still has a live wake (a watcher job file or a running
  background wait). A pending background wait does not survive the move, so a child "only waiting on the cluster"
  needs the same check.

## Deploys and shared slots (before a deploy or taking a shared GPU)

- Deploy finished, tested work by default where the project allows it, with a rollback path.
- Shared slots (a deploy, a GPU) use one lock file per server naming the holder, the start time and the rollback
  path. The holder builds on the newest live release and sends one `done:` after.

## Repo hygiene (before a long run and before a push)

- Move or ignore untracked outputs above about 50 MB per file in repos the harness checkpoints every turn. Check
  untracked sizes and free disk before a long run.
- Push only your own commits. If `git log origin/<branch>..HEAD` lists other chats' commits, branch from
  `origin/<branch>`, cherry-pick yours and push that.

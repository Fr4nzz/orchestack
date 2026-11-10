---
name: sol-loop
description: Review work with two independent critics per round and make the smallest fixes. Use when the user says "sol loop" and before shipping code, writing, an analysis or a UI change that should get independent review.
---

# sol-loop (two critics)

Adapted from Theo Browne's `sol-loop` in tstack (MIT, see `NOTICE.md` in this folder), extended to two critics.
If `~/.config/orchestack/local.md` exists, read it first. Its pinned models and limits override the defaults here.

You own the work. Two independent critics review it in parallel and you make the smallest changes that satisfy
them. Be careful of scope creep and don't be scared to push back.

## Critics

- **Always two reviews per round:** GPT Sol and a Claude critic, each in a fresh context with the same brief and sources but
  without your reasoning or the other critic's findings. They find different mistakes.
  For a small revision, narrow the scope to the diff or changed sections instead.
- **Applies to code, writing, analyses, plots and UI.** For code, accept either critic's fix only after the tests or
  checks pass with it.
- **Models:** use the pinned ids from `local.md`. Without a pin, pick the current Sol and Claude models from
  `orchestrator_capabilities`. Never hardcode an id or switch to a newer or pricier model unasked (suggest it).

## Budget

- 2 rounds per unit by default. A third only for security, permissions, auth, data loss or state and concurrency,
  while a blocker or should stays open with evidence, or when the user asks.
- **Score bar:** stop when every critic scores at least 9/10 *(default)*
  with no blocker open, or the budget ends. A score is the confidence the work earns, not a comment count. Scores
  are noisy (Sol scored about 0.5 above Opus), so judge by the lowest. If a round does not raise it, rethink the
  approach first. Declined nits and comment, doc or format-only fixes need no round.
- Ledger: models, diff size and both scores per round, then unit, round, critic, id, severity, outcome, reason,
  usage if returned. Never guess usage.

## 1. Scope

Run `git status --short`. Do not stash, discard or include unrelated changes. Review only this change's paths. If
they do not separate cleanly, choose them, state the choice in the brief and continue.

Find the base after `git fetch origin`:

- PR: `gh pr view <n> --json baseRefName,headRefOid`. Check out the branch only if clean. Base:
  `git merge-base HEAD origin/<baseRefName>`.
- Local: `git merge-base HEAD origin/<default-branch>`. No remote: a base the user names, or `HEAD` for
  uncommitted changes only.
- Non-code work (a document, a rendered page): name the files, URL or screenshots and the version reviewed.

Use one diff command for all rounds. State intent, acceptance criteria and out-of-scope in 1 to 3 sentences. Run
covering checks and note missing or pre-failing ones in the brief. Do not send work this change breaks.

## 2. Start the critics

In T3, call `orchestrator_capabilities`, then `delegate_task` once per critic in parallel, with `role: "review"`,
`mode: "async"` (a child: `"wait"`, below), a new `clientRequestId` per critic per round, and an explicit effort
(medium by default). Save both task ids. `childThreadId` is storage, not the next target. Do not use
`t3_thread_send`, `create_threads` or `t3_thread_launch` for review.

Outside T3, use native subagents that actually run each model. If no route runs Sol (or Claude), the round cannot
pass. Report it as waiting for that critic and rerun it when back. Never substitute another model or pass a round
on one review.

Do not edit the work under review while they run. A top-level chat ends the turn and follows each completion
notice. A child starts each critic with `delegate_task` mode "wait" (timeoutMs about the critic's expected time).
When the wait returns without a result, it follows `next`: it ends the turn with `waiting: critics` (no ids) and sends
its report from the turn the completion starts, because T3 may not relay that turn. A wait timeout is not a
failure, so restart nothing. If a critic errors or never replies, cancel it and restart it once with the ledger,
then report which critic completed. Silence is not approval.

Round 1 brief, the same for both critics (T3 children get no history):

```
Review only. Do not edit, commit, push, deploy, comment on PRs, or start subagents.
Repo: <abs path>  Work: <diff command, paths, URL or screenshots>  Base: <sha>
Reader or user: <who it is for>  Intent: <what it must achieve>  Acceptance: <criteria, or "the intent">  Out of scope: <...>
Checks: <tests or checks run, results, gaps>  Sources to check against: <tracked files>
Ground truth: <independent checks or reference figures, or "none": say what you cannot verify>
Read the surrounding code or text. Find bugs, broken callers, wrong or misleading claims, mismatch with intent.
Hold to the smallest change that meets the intent. Flag code, text or guards the intent does not need.
Other paths, rare cases, small perf, extra compat and anything outside the intent: nits. Rank in-scope style
by its effect on the reader or user.
blocker = wrong or misleading for the intended case or reader. should = a real problem they will hit. nit = rest.
Score: 10 ship as is, 9 only nits left, 7-8 shoulds open, 5-6 a blocker or a wrong approach, 4 or less misses its intent.
Reply:
SCORE: N/10   VERDICT: APPROVE or CHANGES   UNMET: <each acceptance criterion not met, with evidence, or none>
F1 [blocker|should|nit] <file:line or screen> <claim>. Evidence: <...>. Fix: <smallest change>. (stable ids)
```

## 3. Answer findings

Rank by severity and evidence, items both critics raised first among equals. Check each finding and its severity
yourself. Another path, rare case or small cost is a nit, whatever its label.

- Agree: smallest fix. Prefer delete or narrow over a new lock, cache, helper or option. Fix the case that
  happened. Add one focused test that fails without the fix, if the intent needs it.
- Disagree: say why, with evidence (file:line, a number, a test, output).
- Out of scope or nit: decline in one line and list it as a follow-up.
- Exception: when the same symptom returns in a later finding, find the shared cause, test it and fix that,
  because local patches keep resurfacing. Hiding a symptom (erasing output, dropping the item, special-casing it in
  a score) is not a fix.
- Before declining a fix as "does not scale", count the items and estimate the effort (agent or person), because
  the guess is often wrong.

Update the ledger. Re-run checks and `git diff --shortstat <base>`. If review grew the diff, cut first. Do not
agree just to end the loop.

## 4. Next round

Start one only if budget remains and the lowest score is under the bar, a blocker is open, or behaviour changed
since the scores. Use fresh `delegate_task` calls for both critics. Send the round 1 brief, the ledger (`fixed:`,
`disagree:`, `declined:` with evidence), the same base diff, and the change since the last round. Critics check
open ids and changed work only, mark each old id `resolved` or `still open: <reason>`, and score again.

## 5. Stop and ship

- **Agreed:** every critic at or above the bar, no open blocker, no concern of your own.
- **Stuck:** an item stays disputed after two replies, or the budget ends. Show both sides in 2 to 3 lines each and
  let the user or the orchestrator decide. The end of the budget is not approval.
- **Unvalidated:** spatial or domain-judgement output that only the worker's own metrics checked is reported
  "unvalidated", not "accepted", even when both critics approve. The label does not stop work. It lifts when a
  check that does not reuse the worker's method passes (known-answer cases, an outside reference, human feedback).
- Shipping needs its usual authorisation. Never push during the loop, and after agreement only if the user asked
  to push, ship or update the PR.

The final reply, and the PR description above the model blurb if you push, carry the score table (as in Theo Browne's
tstack `council`) of the critics that completed. Write `no score` when one gave none, never invent one.

```markdown
| Critic | Round 1 | Round 2 |
|---|---|---|
| GPT Sol | 8/10 | 9/10 |
| Claude | 7/10 | 9/10 |
```

Then material fixes, declined and unresolved findings with reasons, and validation gaps. Link the ledger (models,
effort only if verified, diff sizes, usage or "not exposed"). Say whether a push completed.

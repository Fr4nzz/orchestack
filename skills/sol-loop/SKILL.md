---
name: sol-loop
description: Use when the user says "sol loop", "dual critic", "critic loop" or "get critics", and before shipping code, writing, an analysis or a UI change that should get independent review. Runs two independent critics per round (GPT Sol from the other vendor plus a fresh Opus) and makes the smallest changes that satisfy them.
---

# sol-loop (two critics)

Adapted from Theo Browne's `sol-loop` in tstack (MIT, see `NOTICE.md` in this folder), extended to two critics.
If `~/.config/orchestack/local.md` exists, read it first. Its pinned models and limits override the defaults here.

You own the work. Two independent critics review it in parallel and you make the smallest changes that satisfy
them. Be careful of scope creep and don't be scared to push back.

## Critics

- **Always two reviews per round:** GPT Sol (other vendor) and a fresh Opus that shares none of your context. They
  behave like different reviewers and find different mistakes, so every round uses both, even for a small
  revision. Scale the scope instead: a small revision gets both critics on the diff or the changed sections only.
- **Applies to code, writing, analyses, plots and UI.** For code, accept a fix from either critic only after the tests
  or checks pass with it.
- **Models:** use the pinned ids from `local.md`. Without a pin, pick the current Sol and Opus from
  `orchestrator_capabilities`. Do not hardcode an id. Never move to a newer or pricier model on your own. Suggest it.
- Critics must not start subagents or reviewers of their own.

## Budget

- Default: 2 rounds per unit. Round 1 is full. Round 2 checks only changed work and open findings.
- Extend to 3 only for security, permissions, auth, data loss or state and concurrency, while a blocker or should
  stays open with evidence, or when the user asks.
- Stop early when both critics return only nits. Declined nits and comment, doc or format-only fixes need no round.
- Keep a ledger: unit, round, critic (sol or opus), id, severity, outcome, reason, usage if returned. Never guess
  usage.

## 1. Scope

Run `git status --short`. Do not stash, discard or include unrelated changes. Limit review to the change paths if
they separate cleanly. Otherwise choose the paths that belong to this change, state the choice in the brief, and
continue.

Find the base after `git fetch origin`:

- PR: `gh pr view <n> --json baseRefName,headRefOid`. Check out the branch only if clean. Base:
  `git merge-base HEAD origin/<baseRefName>`.
- Local: `git merge-base HEAD origin/<default-branch>`. No remote: a base the user names, or `HEAD` for
  uncommitted changes only.
- Non-code work (a document, a rendered page): name the files, URL or screenshots and the version reviewed.

Use one diff command for all rounds. State intent and out-of-scope in 1 to 2 sentences. Run covering checks and
note missing or pre-failing ones in the brief. Do not send work this change breaks.

## 2. Start the critics

In T3, call `orchestrator_capabilities`, then call `delegate_task` once per critic, in parallel, with
`role: "review"`, `mode: "async"`, a new `clientRequestId` per critic per round, and an explicit effort (medium
by default). Save both task ids. `childThreadId` is storage, not the next target. Do not use `t3_thread_send`,
`create_threads` or `t3_thread_launch` for review.

Outside T3, use native subagents that actually run each model. If no route runs Sol (or Opus), the round is incomplete:
it cannot pass, nothing is approved, and you report it as waiting for the missing critic and rerun that critic
when it is back. Never substitute another model and never pass a round on one review.

Do not edit the work under review while they run. End the turn and follow each completion notice. A chat that is
itself a child never ends its turn to wait: it calls `t3_thread_wait` on each critic. In one case T3 did not
relay the turns a delegate's critic notices started, so its report was not relayed to its parent (one without
its `done:` word sat unread for 2.2 h). If a
critic errors, times out or never replies, restart it once with the ledger, then report which critic completed.
Silence is not approval.

Round 1 brief, the same for both critics (neither sees the other, and T3 children get no history):

```
Review only. Do not edit, commit, push, deploy, comment on PRs, or start subagents.
Repo: <abs path>  Work: <diff command, paths, URL or screenshots>  Base: <sha>
Reader or user: <who it is for>  Intent: <what it must achieve>  Out of scope: <...>
Checks: <tests or checks run, results, gaps>  Sources to check against: <tracked files>
Ground truth: <independent checks or reference figures to check against, or "none": say what you cannot verify>
Read the surrounding code or text. Find bugs, broken callers, wrong or misleading claims, mismatch with intent.
Hold to the smallest change that meets the intent. Flag code, text or guards the intent does not need.
Other paths, rare cases, small perf, extra compat and style: nits.
blocker = wrong or misleading for the intended case or reader. should = a real problem they will hit. nit = rest.
Reply:
GRADE: A-F   VERDICT: APPROVE or CHANGES
F1 [blocker|should|nit] <file:line or screen> <claim>. Evidence: <...>. Fix: <smallest change>. (stable ids)
```

## 3. Answer findings

Rank by severity and evidence. Among equals, items both critics raised come first. Check each finding and its
severity yourself. Another path, rare case or small cost is a nit, whatever label it came with.

- Agree: smallest fix. Prefer delete or narrow over a new lock, cache, helper or option. Fix the case that
  happened. Add one focused test that fails without the fix, if the intent needs it.
- Disagree: say why, with evidence (file:line, a number, a test, output).
- Out of scope or nit: decline in one line and list it as a follow-up.
- Exception to fixing the case that happened: when the same symptom comes back in a later finding of this loop,
  find the shared cause, test it and fix that, because local patches to one cause keep resurfacing. Hiding a symptom
  (erasing output, dropping the item, special-casing it in a score) is not a fix.
- Before declining a fix as "does not scale", count the items and estimate the effort (agent or person), because
  the count is cheap and the guess is often wrong.

Update the ledger. Re-run checks and `git diff --shortstat <base>`. If review grew the diff, cut first. Do not
agree just to end the loop.

## 4. Next round

Start one only if budget remains and a blocker or should is open, behaviour changed, or the approvals do not cover
the current work. Use fresh `delegate_task` calls for both critics. Send the round 1 brief, the ledger (`fixed:`,
`disagree:`, `declined:` with evidence), the same base diff, and the change since the last round. Tell critics to
check open ids and changed work only, and to mark each old id `resolved` or `still open: <reason>`.

## 5. Stop and ship

- **Agreed:** no open blocker or should from either critic on the current work, and no concern of your own.
- **Stuck:** an item stays disputed after two replies, or the budget ends. Show both sides in 2 to 3 lines each and
  let the user or the orchestrator decide. The end of the budget is not approval.
- **Unvalidated:** output whose correctness is spatial or needs domain judgement, checked only by the worker's own
  metrics, is reported as "unvalidated", not "accepted", even when both critics approve, so readers know nothing
  independent has checked it. It is a label, not a stop: work continues. It lifts when a check that does not reuse
  the worker's method passes (known-answer cases, an outside reference, human feedback).
- Shipping still needs its usual authorisation. Do not push during the loop. Push after agreement only if the user
  asked to push, ship or update the PR. If you push to a PR, add above the model blurb, naming only critics that
  completed: `Reviewed with sol-loop: <N> rounds, Sol + Opus. Verdicts: Sol <grade>, Opus <grade>.`

Report: grades before and after for each critic, rounds, models (and effort only if verified), what changed, diff
size before and after, fixed and declined findings with reasons, usage if exposed (else say not), ledger path,
pushed or not.

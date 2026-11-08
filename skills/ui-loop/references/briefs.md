# Brief templates

Fill the angle brackets. Keep each brief in the round's folder so the next round can cite it.

## Critic brief (same for both critics in a round)

```
Review only. Do not edit, commit, push, deploy, send anything, click buttons that save data, or start subagents.
Scratch files only in <round folder>/<critic>/.

What: <page or app, one line on what it is>. Repo <path> (branch <branch>). Live build: <URL> (pages: <list>).
You may open it with Playwright for hover, focus, dark mode, a width or the lightbox.
Shots for this round: <folder> (viewport shots at 1366x768 and 1440x900 light and dark, 390x844 light and dark,
per language; full-page shots in full/). Metrics: <folder>/metrics.md (contrast, taps, overflow, focus, names).
Readers and their main task: <who, on which device, deciding or doing what>.
The owner's words: <verbatim feedback>. Pass bars: <list>.
Owner taste (do not fight it): <taste file contents or path>. Liked design: <screenshot>. Rejected version:
<screenshot>. House rules: <design doc, shared components, copy rules>.
Lens this round: <none: your own judgement | principles file <path>, cite the rule number and measure any
contrast you claim | owner taste and consistency across pages | reference sites <URLs>: score each first, then
ours, then the gap and the smallest fix per category>. Judge fresh: do not assume earlier rounds' findings.
Checks already run: <Playwright widths, console clean, no sideways scroll, save round trip on a copy>.
Out of scope: <content rewrite, information architecture, later waves>.

Judge visual design and use: first impression and hierarchy per page, layout and spacing, alignment, type scale
and line length, colour roles and contrast, consistency of components, density and redundancy, actions, phone,
dark mode, what fits one screen, anything templated, broken or unfinished. Prefer the smallest change with the
largest visible gain, and name the file and selector or component.

Score /10 (one decimal) per category: typography, hierarchy, spacing and density, colour and contrast,
consistency, clarity of actions, phone, dark mode. Then each page. Low scores need evidence.
Then one overall score: 10 ship as is, 9 only nits left, 7-8 shoulds open, 5-6 a blocker or a wrong approach,
4 or less the change does not meet its intent. Score the confidence the work earns, not the number of comments.
blocker = broken or misleading. should = a real problem readers or the owner will notice. nit = polish.
Reply:
SCORE: N/10   CATEGORIES: ...   PAGES: ...
VERDICT: APPROVE or CHANGES   UNMET: <each pass bar not met, with evidence, or none>
F1 [blocker|should|nit] <page/shot or file:selector> <claim>. Evidence. Fix (smallest change).
Up to 15 findings, stable ids.
```

From round 2: add the ledger. For a new-lens round keep the full review. For a re-check of fixes say "check open ids
and changed work only, mark each old id resolved or still open, and score again".

## Implementer brief (one implementer per round)

```
Repo <path>, branch <branch>. Do not push, deploy or start subagents. Commit with <trailer>.
Read first: <design doc>, <tokens and shared styles>, <shared components>. Reuse shared components, never add a
per-page copy. Never invent facts or copy.
Owner taste: <file>. Skipped by the owner's taste (do not do): <list>. Waiting on the owner (do not change):
<list>.
Fix, in this order (ids refer to <round folder>/<critic>/review.md):
1. <id(s)> <what, where, the smallest change>
...
Skip anything that conflicts with the house rules or the taste file, and say so.
Checks: <project checks>, then scripts/ui_shoot.mjs on the changed views: before/after viewport shots at
1366x768 light and 390x844 dark (plus 1024 where layout changes), metrics compared with before/.
Report: commits, each id fixed or skipped (with the reason), questions only the owner can answer, the shot paths.
```

## Hands-on pass brief

```
Act as <reader> on <copy of the data / staging URL>. Never use the live store.
Task: <main task>, <n> times. Time each step, count clicks and keys per item. Then keyboard-only, then 390x844,
both themes and languages. Write a ranked friction list with the numbers to <path>, fix the top <k> items,
re-time them, and remove the test records you created.
```

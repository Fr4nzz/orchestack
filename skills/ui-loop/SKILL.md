---
name: ui-loop
description: Use when the user says "ui loop", "ui-loop", "improve the UI", "polish the design" or "hill-climb the design" of a web page, app view, poster, slide or other visual, or asks to make a page easier to use.
---

# ui-loop

If `~/.config/orchestack/local.md` exists, read it first, and the owner's taste file it names (default
`~/.config/orchestack/ui-taste.md`). Both override the defaults here. `references/principles.md` holds the
design rules with pass bars, and `references/briefs.md` holds the critic and implementer brief templates. This
skill drives `sol-loop` (two critics per round) for visual work. Reviewing at the right screen sizes, using the
interface, and respecting the owner's taste are what make UI review work. Most failures came from skipping one
of them.

## 0. Before any change

1. **Quote the owner's words** for the goal, and rewrite each complaint as a pass bar a critic can check
   ("the key decision is visible without scrolling at 1366x768", "no internal field names on cards").
2. **Name the readers and their main task** on this screen (who decides what, on which device). Every rule below
   is judged against that task.
3. **Collect the taste evidence you have:** the owner's taste file, one design they liked (screenshot), and the
   version they rejected (screenshot). Mark any that is missing as unknown and state your provisional assumptions.
   Critics get all of it in every round, the blind round included ("blind" means no lessons file). Without it,
   critics push generic rules against the owner's taste: removing boxes, outlines and brand colours, or shrinking
   images until they need a click. The owner reversed exactly these changes more than once.
4. **Budget:** price one round from a pilot (orchestrator skill, **Budget**). On one site, round 1 measured about 1 %
   of a week, and rounds 1 to 4 with four implementer passes about 2 % (later rounds were not metered).
5. **Test on a copy.** Pages that write data (review, labelling, forms) run against a copy of the data. Critics
   are told never to click write buttons on a live page. One test run once wrote verdicts into a live store.

## 1. Evidence at real sizes

Run `scripts/ui_shoot.mjs` (Playwright, see its header) for every page or view in scope:

- **Viewports:** 1366x768 and 1440x900 laptops, 390x844 phone (add 360 for dense apps, 1024 for tablets, the
  medium's own size for posters, e.g. 1080x1920). Light and dark, and each language the page serves.
- **Viewport shots, not only full-page ones.** Full-page tiles hid that a key visual did not fit one laptop
  screen, that portraits needed a click to read, and that a lightbox scrolled the page behind it. The owner found
  all three. Take one shot per screenful plus the full page.
- **Metrics per view** (`ui_metrics.js`, run by the shooter): text contrast below 4.5:1 (3:1 large), control
  edges below 3:1, phone tap targets below 44 px, text below 12 px, sideways scroll, clipped text, controls
  without a name, focus stops without a visible indicator, console errors. Lazy images are given time to load.
  The numbers are leads to check by eye, not verdicts (a ring drawn on a wrapper reads as missing, for example).
- One `--out` folder per round, e.g. `before/`. Every later round is compared with the baseline. The shooter
  keeps at most `--max-screens` (6) screenfuls from the top: shoot deep sections by their own URL or anchor.

## 2. Use the interface (before critics)

Act as the target user, in a real headless browser, on the copy of the data:

- Do the main task end to end several times (e.g. review 20 items, fill a form, find a fact). Time each step and
  count clicks and keystrokes per item. Then do it keyboard-only, then at 390 px, both themes and languages.
- Write a ranked friction list with the numbers ("every item needs a mouse trip to a small Next button", "76 Tab
  presses from a list row to starting a sample", "no Next button on the phone bar"). Fix the top items before the first critic round.
- This pass finds what screenshots cannot show: broken flows (starting a session was broken for every signed-in
  user), state bugs (an answer stored on the previous item while the next loads, advancing after a failed save),
  bars covering the save button on phones. One redesign cut confirming an item from three actions with a mouse
  trip to three keys (A, Enter, Enter, 70 to 450 ms per item).
- Remove any test records you created.

## 3. Critic rounds (two critics, rotating lenses)

Each round runs `sol-loop`: GPT Sol and a fresh Opus, in parallel, with the same brief (`references/briefs.md`),
review only. They get the shots, the metrics, the taste evidence, the pass bars and the live URL (Playwright
allowed for hover, focus, widths and the lightbox). They score each category /10 and list up to 15
findings (blocker, should or nit), each with evidence and the smallest fix. Never tell critics a target score.

Rounds follow sol-loop's budget (2 by default, a third or more when the owner asks or a blocker stays open). Pick
a different lens for each round you have. In 8 rounds on one site, each lens found a different class of problem.
These rounds ran one after another on a site that changed between them, so this is an observation, not a
controlled comparison:

| round | lens | what it found |
|---|---|---|
| 1 | blind: their own judgement | page-level layout: weak hero action, squeezed timeline, long first screen |
| 2 | `references/principles.md` plus "measure the contrast you claim" | system level: three primary-button styles, a brand colour at the contrast floor, fonts mixed in body text (20 measured ratios against 0 in round 1) |
| 3 | other lessons (a talk's and a poster's) plus consistency across pages | one label per action, one title per item, one disclosure icon, the single emphasis colour kept for the single action |
| 4 | 2 to 3 reference sites: score each first, then ours, then the gap and the smallest fix | what peers do better: proof up front, the key term in the hero, a home page 10 phone screens long |
| 5+ | judge fresh at real laptop heights, earlier findings not repeated | viewport fit at 1366x768, wrapping at 1024, empty grid cells |

Notes:
- Scores drift and differ by vendor (Sol scored about 0.5 above Opus every round, and adding references lowered
  Sol's score). Track the stricter critic and the findings, not the number. Stop when both approve with only nits,
  when the owner is happy, or when the budget ends.
- One critic is not enough. A single approving critic passed a page whose own ledger already listed phone defects.
- Lessons help and hurt. Critics given rule lists cited them and measured more, but also pushed generic rules
  against the owner (unify shadows, drop boxes, remove brand colour). That is why the taste file goes in every brief.
- A new-lens round is a full review. "Open ids plus changed work only" is for a re-check of fixes (sol-loop's later
  rounds, and posters).

## 4. Triage and one implementer

- **Triage:** the lead merges both critics' findings into one implementer brief (`references/briefs.md`) with the
  critics' ids, ranked by visible gain. Then it adds a "skipped by the owner's taste (do not do)" list and a
  "waiting on the owner" list. Findings that only the owner can decide (copy, photos, logos, names) are batched
  into one question and kept out of scope until answered.
- **One implementer per round** (never several editing the same styles). It reads the design doc and shared
  tokens first, reuses shared components, never invents facts, skips anything against the taste file and says
  so. It runs the project's checks and delivers its own before and after viewport shots of what changed.
- **Fix systems, not instances.** Same control, same look everywhere, so one shared component or token rather
  than per-page CSS. Colour fixes keep the hue and move only the lightness. When a page repeats a pattern, fix
  the pattern.
- **Ledger:** keep every finding's outcome (fixed, declined with reason, waiting on the owner). Also keep every
  owner remark and check it before declaring done. A lost owner remark had to be repeated three times.

## 5. Verify, show, stop

- Re-shoot and re-run metrics, and compare with `before/` (e.g. "text under 4.5:1: 3,060 to 87"). Check the
  console is clean, there is no sideways scroll at 360 and 390, and focus is visible. Re-run the hands-on task
  timing.
- Show the owner at once (orchestrator skill, **Reports**): the live link and a before/after page or annotated pairs, sent as
  `done: <what> <link>` the moment it is up, not at the end of the lane.
- Next round: a new lens (a full review) or a re-check of fixes (open ids plus changed work), always with fresh
  critics. A fresh critic also catches what an earlier round missed.

## Variants

- **Fast (the owner wants to see it soon):** step 0, a short hands-on pass (3 items at desktop and phone), one
  critic round, apply, show. Say plainly that the fixes were not re-reviewed.
- **Whole app (systematic):** inventory every route and dialog, then a scripted audit of all of them (rate each
  finding P0 to P3 with numbers). Next, a foundation pass (role tokens, both themes), then fix waves by severity,
  each on its own branch. Each wave is re-shot with metrics and reviewed by two critics. Finally deploy with a
  rollback and a before/after gallery. One such overhaul took phone views wider than the screen from 60 to 0 of
  172, and focus stops without an indicator from 150 to 0.
- **Poster, slide or social image:** name the medium and its overlays (e.g. a story app's top and bottom bars),
  the 3-second phone scan, the reading size (30 px on a 1080 canvas is about 10 px on a phone), and "copy mirrors
  the source, no new facts". Render with an overflow check, and decode any QR code.
- **Variants to choose from:** when the direction is open, build 2 or 3 clearly different variants and let the
  owner pick. Expect them to keep one idea from each, not a whole variant.

## Anti-patterns

- Judging only full-page screenshots or code. Viewport fit, hover, sticky bars and lightboxes need the browser.
- Density pushed until images need a click, or the key visual no longer fits one laptop screen.
- Removing what the owner likes (boxes, outlines, dividers, brand colours) to satisfy a generic rule.
- Generic palettes and fonts from a design tool, off-brand for the project.
- Text the user does not need: repeated facts, defensive caveats, internal names or `key=value` dumps,
  similarity scores, a wall of text before the task.
- Motion that does not explain anything, or plays once and never again.
- Components re-implemented per page (cards that look clickable but are not). One shared contract, checked by
  the build.
- Too many app instances and browsers at once. Close each headless browser after its batch, and check free memory
  first (orchestrator skill, "Memory, not the number of agents, is the limit").

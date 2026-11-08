---
name: bulk-image-review
description: Plan cheap agent review of many images. Use when an agent itself must look at many images (label or ground-truth review, transcription of photos, notebooks or envelopes, checking model outputs, curating crops, screenshot or slide audits). Read BEFORE launching image-reading agents.
---

# Bulk image review, cheaply

If `~/.config/orchestack/local.md` exists, read it first. Its budget rules override the defaults here.

## Why it gets expensive

When the harness keeps opened images in context, every later step processes them again. With a fixed number of
steps per image, one agent's cumulative input across calls grows roughly with the square of the images seen. A
pilot gives the real cost. Retries (a failed image tool, a re-opened image) multiply it. A large chat reviewing
screenshots itself also pays its whole context every turn.

Measured on one week of real work (cost-weighted tokens from local transcripts):

| Job | Images | Agents | Share of the week |
|---|---|---|---|
| Label transcription pre-reading | 300 items (crop + photo) | 12 x ~25 items | ~2 % (~0.03 M weighted tokens per item) |
| Ground-truth card review | a few thousand cards plus rechecks | 12, two of them ~800 calls with thousands of image reads | ~55 % (~0.07 M per card) |
| Slide and figure checks inside a very large writing chat | ~90 screenshots | 0 (done in place) | ~1.5 % |

## Ask first

Follow the budget rule in the `orchestrator` skill: price from a measured pilot, not from token estimates. Run one
small batch (one agent, about 25 items) yourself, read the usage meter before and after, and scale to the whole
job. Ask the user only if that measured price is above the ask threshold and not yet approved. The per-item
figures above only sanity-check the pilot.

## Patterns that worked

1. **Shrink the number of images the agent must see.** Models or scripts do the first pass. The agent looks only
   where something doesn't add up: disagreements between tools, low confidence, rule violations. Auto-accept cases
   where independent tools agree, and audit a small random sample of those instead.
2. **Show the region, not the photo.** Pre-cut views at modest size (a label crop at ~560 px turned upright, detail
   crops at ~640 px, side-by-side pairs ~360 px high), cut from a downscaled copy by a script. Open the full image
   only when the crop misses something.
3. **Small, fresh agents.** About 25 items per agent, each with a fixed batch list. A long agent reading hundreds of
   images is the expensive case. A big chat never reviews screenshots itself: it hands them to a fresh worker.
4. **A guide file plus a short brief.** The rules, the output schema and the independence rules live in one
   `GUIDE.txt`. Each agent's prompt is three lines: read the guide, here is your batch file, write
   `out_batch_XX.jsonl`.
5. **Write results to files as you go.** One JSONL row per item, so partial work survives a stop and nothing is
   redone. The coordinating chat reads the files, never the images, and does not re-type the readings.
6. **Tiny replies.** Agents answer only "rows written, how many unsure, problems". Tools return only the items that
   need a look, merge identical findings, and truncate raw text.
7. **Blind and anonymous items** (anonymised names, no access to model outputs), so the review is independent and
   cheaper, because the agent doesn't go searching.
8. **Human confirmation on a sample.** In the transcription job the user checked 40 of 300 items, found no corrections,
   and accepted the rest.

## Geometry checks are not label checks

When correctness is spatial (placement, overlap, layer order, alignment), trust neither a whole-outline metric nor
your own eye on small panels. Build an independent check and keep going.

1. **Measure the parts, not the outline.** Score each part on its own, because a silhouette metric (union IoU, mask
   area) cannot see where the parts inside it sit.
2. **Look at the critical region at full resolution** (an exception to pattern 2), because small whole-item panels
   hide the few pixels where the error is.
3. **Build known-answer cases yourself** (items where the hidden part is fully visible) and drop any heuristic that
   fails one, because a heuristic that fails a known case fails silently on the rest.
4. **Make spot checks cheap.** Put one page of crops of the critical regions next to the result, so a person can
   catch an error in a minute. Human feedback, when it comes, overrides your own checks.

Worked example: composites of two overlapping parts passed a union IoU gate at about 0.95, and several AI critic
rounds approved them on small panels. Per-part IoU against four hand drawings was 0.64 to 0.84 on the same items.

## Which model

Tested on about 200 ground-truth cards, each reviewed twice by the default model:

- **Small model (Haiku class):** when it decides, it agreed with the default model 96 to 98 % of the time, the same
  as the default model agrees with itself (98 %). It answered "unsure" more often (23 % vs 17 %). Use it as a first
  pass and send only its unsure items to the default model, at a fraction of the cost per item.
- **Mid-tier model (Sonnet class):** decided more but flipped yes/no answers 6x as often. Not recommended for this
  kind of judgement.
- The small model passed one task and failed others (fine look-alike classes, realism checks). So calibrate on each
  new kind of review: both models on the same 30 to 50 items with known controls, the pass bar set before looking.
  Never use the small model for final judgement.
- Give agents absolute image paths (relative paths resolve differently per agent).

## Guard rails

- **Make sure every agent can actually see the images.** If the image tool fails ("media removed"), stop the agent.
  Don't let it guess or retry in a loop. Rerun the batch later in a fresh agent.
- Cap the work per agent: at most ~40 images, and at most ~1 hour or ~150 calls. Split the work instead of extending
  an agent.
- Several items per image (a contact sheet of crops with ids) is fine for yes/no judgements. Keep crops large
  enough to judge.
- After a big review, record the cost (calls, share of the week) next to the result, so the next estimate is
  calibrated.

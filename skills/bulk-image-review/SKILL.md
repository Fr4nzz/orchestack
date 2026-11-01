---
name: bulk-image-review
description: Use when an agent itself must look at many images (label or ground-truth review, transcription of photos, notebooks or envelopes, checking model outputs, curating crops, screenshot or slide audits). Read BEFORE launching image-reading agents. Gives the cost model, the ask-first threshold, and the patterns that kept a 300-item transcription job near 2 % of a week while a card review of similar size grew to about 55 %.
---

# Bulk image review, cheaply

If `~/.config/orchestack/local.md` exists, read it first. Its budget rules override the defaults here.

## Why it gets expensive

Every image an agent opens stays in its context and is re-read on every later step, so one agent's cost grows
with the square of the images it has seen. Retries (a failed image tool, a re-opened image) multiply it. A large
chat that reviews screenshots itself pays its whole context on every turn as well.

Measured on one week of real work (cost-weighted tokens from local transcripts):

| Job | Images | Agents | Share of the week |
|---|---|---|---|
| Envelope transcription pre-reading | 300 items (crop + photo) | 12 x ~25 items | ~2 % (~0.03 M weighted tokens per item) |
| Ground-truth card review | ~2,600 cards + 650 rechecks | 12, two of them ~800 calls with thousands of image reads | ~55 % (~0.07 M per card) |
| Slide and figure checks inside a ~550k writing chat | 89 screenshots | 0 (done in place) | ~1.5 % for 89 images |

## Ask first

Follow the budget rule in the `orchestrator` skill. Before launching, estimate images x ~0.03 to 0.07 M weighted
tokens and give the share of a 5-hour window or the week. More than ~100 images in total, or any fan-out over
several agents, needs the user's OK unless a measured pilot already priced it under the ask threshold.

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
7. **Blind and anonymous items** (anonymised names, no access to model outputs), so the review is independent. This
   is also cheaper, because the agent doesn't go searching.
8. **Human confirmation on a sample.** In the envelope job the user checked 40 of 300 items, found no corrections,
   and accepted the rest.

## Which model

Tested on about 200 outline cards, each reviewed twice by the default model:

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

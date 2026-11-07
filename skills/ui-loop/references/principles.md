# UI principles with pass bars

Distilled from several UI improvement runs: a research group's website and its poster, a data-entry app and a
specimen gallery (a full overhaul), a labelling app's review view, and several review pages. They also draw on
the public T3 Code design PRs (pingdotgg/t3code) and Theo Browne's "Agents Sucked At UI (Until Today)". Each rule
has a check a critic can run. The owner's taste file overrides any rule here.

## Focus and text

- **One clear focus per screen.** The reader sees at once what to decide or do and where to look. Pass: the main
  action and the facts needed for it are visible without scrolling at 1366x768 and 390x844.
- **Less text.** Never show a fact twice. Every paragraph earns its place, and the page label does not repeat the
  heading. Pass: no sentence that could be deleted without losing information.
- **No machinery in the interface.** No internal field names, rule ids, `key=value` strings, raw scores or
  defensive caveats. If a value is the same for every item (e.g. all "n/a"), do not show it. Pass: a newcomer
  understands every label.
- **One name per thing** across pages, buttons and disclosures. Disclosures are named by their content ("More
  about <name>", "Why this match"), not "Details".
- **Show before and after** for any text change you propose to the owner.

## Colour and contrast (measure, never eyeball)

- **Colour only where it means something:** neutral structure, one accent for action, semantic colours for state,
  identity colours kept. A grey-only pass "looked quiet but flat" and was rolled back within a day. Give each
  colour one job. Two colours too close in lightness (under about 1.2:1) are never both used as emphasis.
- **Ratios:** body text at least 7:1 if possible and never under 4.5:1, secondary 4.5 to 5.5:1, large text 3:1,
  control edges and non-text indicators 3:1, in both themes. Measure text on state fills against the fill, and
  re-measure translucent fills on each canvas.
- **Fixing a failing colour:** keep its hue (OKLCH) and move only its lightness. Example: a brand rust went from
  4.38:1 to 5.5:1 this way and still read as the brand.
- **Tokens:** one token per meaning, own values per theme, and the boot or theme colour matches the canvas (no
  flash on load). A test that pins token ratios keeps them from drifting.

## Hierarchy, type and space

- Hierarchy comes from size, weight and space, with three ink steps. A heading is never lighter than body text.
- Few type sizes: one poster went from about 10 sizes to 5. Near-equal sizes (24, 25, 26) read as noise.
- Strict radius scale (e.g. 4, 6, 8, 12). No card inside a card. Boxed groups of facts are fine when the owner
  likes them: improve inside the boxes rather than removing them.
- **Density with a floor:** no wasted space, but images, portraits and figures stay large enough to read without a
  click. The key visual (a map with its legend, a chart) fits one laptop screen.

## Components and states

- Same control, same look and behaviour everywhere. Sibling lists look alike. One shared card or button component,
  ideally checked by the build.
- A listed option never leads to nothing: filters and menus show only what is available.
- One primary action per section or toolbar. The rest are outlined. No duplicate calls to action back to back.
- **Cards that respond:** hover lift, press feedback, image zoom when it helps. The whole card is clickable when
  it has one obvious action. Clicking text does not toggle (text stays selectable). Clicking empty space closes an
  expanded card. Phones get an equivalent of every hover affordance.
- **States designed:**
  - hover never lowers contrast
  - selected is a fill plus an edge or check
  - disabled stays at 3:1 or more, with a reason
  - loading has a caption
  - empty says what is missing and the next action
  - errors sit next to the field
  - state is never shown by colour alone.
- **Focus:** a 2 px `:focus-visible` ring at 3:1 or more, distinct from selection. Pass: every focus stop shows
  a ring.
- **Motion:** none continuous. Keep it under about 200 ms and in answer to an action, and respect reduced motion.
  An animation that explains a step should replay when scrolled back into view. Decorative motion goes.
- **Lightbox:** locks background scroll, fits the screen, offers full size, closes on Esc and backdrop, and keeps
  focus inside.

## Phone

- Targets at least 44 px (an invisible 44 px hit area is fine), inputs with 16 px text (avoids iOS zoom).
- No sideways scroll at 360 and 390. Nothing needs hover. Primary actions are in reach and not covered by bars or
  toasts.
- The phone gets the same path as the desktop (a Next button, a save that is reachable).

## Dark mode

- Built from role tokens, never by inverting. As intentional as light, with no dull, low-contrast borders.
- Photos are never filtered. Maps use dark tiles, not inverted ones, and markers get halos.

## Review and labelling workflows

- Time the flow and count actions per item. Target one key per common decision, and the next item one step away
  by keyboard too. Advance only after a successful save, and say when something is unsaved.
- Keep the page at viewport height on laptops when reviewers work item by item (no scrolling per item).
- Plain state labels ("Not a target", "Can't identify"), never internal terms.
- Prefetch the next item so the screen does not fill piecemeal. Resume an open session instead of starting a
  duplicate.
- Rare actions go behind one menu. The most common flag keeps its own key. One keyboard hint line, hidden on touch,
  plus a dismissible "how this works" card.
- Blind first answers when several people review: reveal others only after saving.
- Offer a batch mode for coarse decisions alongside item-by-item review.
- **Item cards:** title = the item. The image is zoomed to the part that matters, with a small facts grid.
  Recorded and predicted values get separate badges, plus a chip when they differ. Only trusted model outputs
  are shown, the rest go in details.
- **Data-entry apps:** keep the table visible, work in batches first, and use one save step that names what is
  unsaved.

## Posters and social images

- One job per colour. The single act-now action gets the strongest colour, and the brand colours stay.
- Key facts readable in about 3 seconds on a phone. Use a 30 px minimum on a 1080 canvas and respect the app's
  top and bottom overlays.
- The copy mirrors the source (site, call text): no new facts, and dates and eligibility are checked. Decode the
  QR code.

## Annotated guides

- Number only the non-obvious things, few of them, in reading order: the left column first, then the right.

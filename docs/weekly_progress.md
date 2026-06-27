# Weekly Progress

A running log of weekly user feedback. Add a new dated section each week and
record feedback as bullet points under it (most recent week at the top).

<!--
Template — copy for each new week:

## Week of YYYY-MM-DD

- Feedback item 1
- Feedback item 2
-->

## Week of 2026-06-22

**UX — high priority**
- Restructure each result section (2–4) into two parts: a **"Key takeaway / At a
  glance"** block (the verdict, the number, and the core points — concise and
  well-formatted, a few bullets if warranted) and a **"Detailed analysis"** block
  (breakdown + chart + deeper read) that is **collapsed by default**. Give it a clear
  type scale so the elements stop competing. Section 2 currently stacks ~5
  differently-formatted summaries of the same number with no hierarchy.
- Make the **collapsed "Detailed analysis" toggle clearly visible** (labeled control, not
  a subtle caret) so the charts stay discoverable.

**UX — moderate**
- Make each **section header the clear visual anchor** of its section — currently
  "2 · Component decomposition" is visually outranked by the content beneath it.
- Move **"Download PDF report"** out of the middle of section 2 to a consistent
  document-level location (a results toolbar at the top, or the end of the results).
- Trim the hero margin value to **"$2,707"** — the "(2.71% of notional)" is already shown
  as the caption below and currently truncates ("$2,707 (2.71% of n…") at common laptop widths.

**UX — polish**
- Unify the takeaway-box color — section 2's is olive while the others are blue.

## Week of 2026-06-15

**UX — high priority**
- Make **embedded margin** the hero result — headline it with a plain verdict
  (e.g. "Priced 2.7% above fair value") and make the other three metrics secondary.
- Add a **plain-language takeaway** (one line or a few bullets) to each chart stating
  the finding in lay terms.
- Keep the **Greeks (Delta/Vega/Rho) visible but add a one-line plain definition** to
  each; lead the risk section with the intuitive metrics (P(loss), expected return, max loss).

**UX — moderate**
- Re-color the **"Analyze" button** from red to the brand purple/pink gradient.
- **Collapse each API-key field to a green ✓ "key set · edit" chip once entered** (keep
  the current sidebar order).
- Move the **Monte Carlo paths slider** into an "Advanced" section.
- Add **direct links to obtain the keys** (FRED signup, Anthropic console) in the key fields.
- **Surface the "Market inputs" panel** instead of leaving it collapsed at the bottom.

**UX — polish**
- Add a short **"how to read this"** note to each chart.
- Fix the **ambiguous metric arrows** (green ↑ on components vs. red ↑ on margin).
- Fix the **cramped/overlapping legend** on the payoff chart.

**Future features**
- Export the analysis as a **downloadable PDF report**.
- **Batch PDF upload → batch analysis → batch download**.

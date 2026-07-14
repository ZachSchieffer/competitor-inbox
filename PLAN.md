# The Competitor Inbox

## Mission

The Competitor Inbox turns marketing emails already received by an inbox the
brand controls into a private strategy dashboard. It helps an owner compare
cadence, content mix, promotions, seasonal timing, and planning opportunities.

The tool analyzes competitor behavior. Inbox data cannot prove that an email
performed well, caused revenue, or beat another campaign.

## Launch contract

- Product keyword: `INBOX`.
- The public repository contains code, documentation, tests, and synthetic data
  only.
- Production data lives under `<DATA_ROOT>`, outside every Git worktree.
- Raw messages, recipients, credentials, tokens, personalized URLs, and email
  HTML never enter the repository.
- The parser never fetches remote images or executes email HTML.
- Lifecycle messages stay browsable but are excluded from broadcast metrics.
- External distribution remains manual. The project does not post, comment,
  connect, or send direct messages on LinkedIn.

## Delivery phases

1. Build the public-safe foundation and privacy controls.
2. Ingest 12 months from Gmail IMAP or an mbox export into `<DATA_ROOT>`.
3. Normalize, redact, deduplicate, classify, and produce a coverage census.
4. Stop at the early data gate when the archive cannot support credible proof.
5. Build the private strategy dashboard and freeze its evidence manifest.
6. Package the CLI, deterministic synthetic demo, scheduler, and setup guide.
7. Stage the Notion guide, delivery playbook, launch copy, and internal task.
8. Run privacy, cross-foot, clean-install, URL, and copy validation before
   manual launch approval.

## Reusable source pin

Reviewed implementation patterns come from `ZachSchieffer/zach-dashboard`,
branch `claude/milled-email-pipeline-70tvj0`, pinned at commit
`0be55bd9b0dc1d62eb89145cfa3114b2f7611fc8`. The new project has an unrelated
Git history. Only reviewed code patterns are adapted. Data, generated assets,
email HTML, credentials, browser automation, and source history are excluded.

## Early data gate

Continue only when both conditions pass:

- At least 300 qualified broadcasts across all brands.
- At least 1 brand with 15 qualified broadcasts over 45 observed days.

The final single-brand hook requires at least 30 qualified broadcasts over 90
observed days and a complete, error-accounted source range. Otherwise the
launch uses a verified multi-brand finding.

## Classification model

Every distinct message receives 2 independent analytical axes:

- Offer: offer present or no offer.
- Calendar: seasonal or non-seasonal.

Those axes produce 4 broadcast quadrants:

| Quadrant | Rule |
|---|---|
| Evergreen content | No offer and non-seasonal |
| Everyday promotion | Offer and non-seasonal |
| Seasonal promotion | Offer and seasonal |
| Seasonal content | No offer and seasonal |

Numeric offer depth requires deterministic text evidence. Optional AI can add
qualitative analysis to sanitized text, but it cannot invent a numeric offer.

## Coverage gates

| Observed history | Permitted analysis |
|---|---|
| Under 30 days | Library and current activity |
| 30 to 89 days | Current pulse with a thin-data warning |
| 90 to 329 days | Cadence, mix, and posture |
| 330 to 729 days | Annual and prior-season planning |
| 730 days or more | Year-over-year analysis |

A brand posture is assigned only when the leading intent represents at least
35% of qualified broadcasts and is at least 1.25 times the runner-up. Other
brands are labeled `Mixed`.

## Evidence and review gates

All displayed counts must cross-foot to the normalized census. A freeze
manifest binds the data window, filters, definitions, model mode, repository
revision, dashboard, and screenshots. Refreshing the data invalidates the
freeze and requires every public number to be checked again.

Checkpoint A contains all of the following, without truncated tables:

1. At least 2 real 1080 by 1350 hero candidates.
2. The selected hook and full eligibility proof, or the multi-brand fallback.
3. The top 5 findings with numerator, denominator, brand set, date range,
   source limitation, and coverage gate.
4. The main Notion page plus 9 subpages, one line each.
5. The complete decision log.
6. The freeze manifest, known weaknesses, and blockers.
7. The complete Phase 1 coverage table with every brand, its 4 quadrant counts,
   and the total row.
8. The global 4-quadrant census with raw counts, percentages, denominator, date
   range, and coverage note.

Each coverage row shows raw fetched, parse failures, parsed input, variants
collapsed, distinct messages, lifecycle, uncertain, qualified broadcasts,
first and last observed dates, observed days and weeks, represented months,
source completeness, ingestion errors, hook status, and all 4 quadrant counts.

Final distribution copy is written only after that evidence clears review.

Checkpoint B reviews the final copy, images, public guide, delivery playbook,
internal launch task, repository audit, clean installation, and all remaining
risks. Nothing is distributed before approval.

## Required residual-risk disclosure

- IMAP app passwords provide broad mailbox access.
- Google Workspace policy may disable app passwords.
- Optional AI processing sends sanitized text to Anthropic.
- Lifecycle classification contains judgment-error risk.
- Scheduled updates depend on the Mac being on or waking.
- Inbox data shows competitor behavior, not competitor performance.

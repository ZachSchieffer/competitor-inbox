# Decision Log

This file records implementation judgments and blockers. Entries use UTC so
evidence from local and automated runs can be compared directly.

## 2026-07-14T09:45:00Z: New public Git history

- Authority: approved execution plan.
- Decision: use an unrelated Git history and adapt reviewed implementation
  patterns without copying prior data or repository history.
- Reason: a public-first repository needs an auditable zero-production-data
  boundary from its first commit.
- Impact: reusable code is reviewed and copied selectively with attribution;
  production artifacts remain outside Git.
- Status: active.

## 2026-07-14T09:46:00Z: Data-root containment

- Authority: approved execution plan.
- Decision: reject a production data root found inside any Git worktree.
- Reason: ignore rules are a second control, not the primary privacy boundary.
- Impact: users must configure `<DATA_ROOT>` outside the repository.
- Status: active.

## 2026-07-14T09:47:00Z: Public configuration format

- Authority: implementation judgment.
- Alternatives: JSON, YAML, or TOML.
- Decision: use TOML so Python 3.11 can read configuration with the standard
  library and the public example remains readable.
- Impact: no configuration parser dependency is required.
- Status: active.

## 2026-07-14T09:48:00Z: Sanitized privacy reporting

- Authority: approved execution plan.
- Decision: privacy findings report rule IDs, paths, scopes, and hashes only.
- Reason: an audit must not repeat a credential or recipient value into CI logs.
- Impact: maintainers inspect the referenced file locally to remediate a hit.
- Status: active.

## 2026-07-14T09:49:00Z: Template-token exception

- Authority: approved execution plan.
- Decision: angle-bracket configuration tokens are allowed only in public
  templates and schema documentation. Final launch artifacts reject them.
- Reason: the public repo cannot ship a real inbox address or local data path.
- Impact: package validation separates templates from distribution artifacts.
- Status: active.

## 2026-07-14T09:50:00Z: Pinned-source adaptation boundary

- Authority: approved execution plan.
- Decision: inspect and adapt only the MIME, stable-identity, dedupe,
  classification, offer, seasonality, aggregation, and dashboard patterns at
  `ZachSchieffer/zach-dashboard` commit
  `0be55bd9b0dc1d62eb89145cfa3114b2f7611fc8`.
- Reason: the source branch also contains production data, rendered email
  assets, browser automation, and generated reports that cannot enter this
  repository.
- Impact: no source Git history, data file, credential file, live HTML, or
  generated asset was copied.
- Status: complete.

## 2026-07-14T09:51:00Z: Direct initial push

- Authority: approved public-repository creation.
- Decision: push the privacy-audited first commit directly to `main`.
- Reason: this is a new empty repository with no existing branch or pull
  request base, and the first commit establishes the security boundary itself.
- Impact: every later push still requires the full worktree and history audit.
- Status: active.

## 2026-07-14T09:52:00Z: Executive dashboard design

- Authority: implementation judgment.
- Decision: use static native HTML, CSS, and SVG patterns with a cold
  monochrome palette and 1 cobalt accent. Runtime JavaScript and remote assets
  are prohibited.
- Reason: the artifact must read as an owner-level strategy surface while
  remaining screenshot-ready, mobile-safe, and incapable of loading tracking
  resources.
- Impact: every section carries its coverage label and denominator; the 2 hero
  candidates render at 1080 by 1350.
- Status: complete.

## 2026-07-14T09:53:00Z: Development runtime outside the repository

- Authority: production-data isolation rule.
- Decision: keep the development virtual environment in the workspace `work`
  directory, outside the repository working tree.
- Reason: the first privacy scan correctly found local absolute paths inside an
  ignored in-repo virtual environment.
- Impact: the live repository working tree contains source artifacts only, and
  the repeated audit now passes.
- Status: complete.

## 2026-07-14T09:54:00Z: IMAP credential entry remains local

- Authority: credential-isolation rule.
- Decision: require Zach to complete the hidden `security` prompt in Terminal;
  do not collect or relay the app password through Codex.
- Reason: Terminal automation is blocked for secret-entry safety, and no other
  approved source export is available.
- Impact: all credential-free implementation and demo QA continued while the
  real Phase 1 backfill remained gated on the Keychain item.
- Blocker duration: 10 minutes at the time of this entry.
- Resolution: the dedicated inbox account had Workspace permission to enable
  2-Step Verification, but 2-Step Verification was still off on that account.
  Zach completed account-level enrollment, created a new app password, and the
  safe probe passed TLS, authentication, and read-only mailbox examination.
- Status: complete.

## 2026-07-14T10:10:46Z: Hook eligibility inherits the full source ledger

- Authority: complete error-accounted source range requirement.
- Decision: disqualify every single-brand hook when the global ingestion range
  contains an unassigned parse failure or ingestion error, even if that
  brand's successfully parsed records otherwise clear 30 broadcasts and 90
  observed days.
- Reason: a parse failure without a reliable brand cannot be proven irrelevant
  to the proposed brand hook.
- Impact: the dashboard uses the multi-brand fallback until the full source
  ledger is complete; the Early Data Gate still uses its approved 300-message
  and 15-message/45-day thresholds.
- Status: active.

## 2026-07-14T10:28:02Z: Source attempts fail atomically

- Authority: freshness, rollback, and complete-source requirements.
- Decision: discard every normalized record from a source attempt when IMAP,
  mbox, or a private write fails before the source iterator completes. Preserve
  the prior master, coverage, dashboard, and `last_success`; write only a
  content-free failed-attempt state.
- Reason: a partial fetch can still contain 300 broadcasts and would otherwise
  pass the Early Data Gate while presenting stale or incomplete data as fresh.
- Impact: malformed individual messages remain nonfatal parse-ledger entries,
  but a source-level failure cannot build or refresh a dashboard.
- Status: active.

## 2026-07-14T10:28:03Z: Recipient privacy fails closed at every boundary

- Authority: recipient and personalized-token isolation requirements.
- Decision: derive ephemeral recipient terms from delivery headers, redact
  those terms plus expanded merge tags, schemeless links, query identifiers,
  and rendered greetings, then assert recipient safety before persistence,
  optional Anthropic processing, and dashboard export.
- Reason: standard address and URL matching alone does not catch rendered names
  such as a personalized greeting or a tokenized link without a URL scheme.
- Impact: a residual identifier aborts the affected record or export instead
  of entering `master.json`, an AI request, or a screenshot surface.
- Status: active.

## 2026-07-14T10:28:04Z: Mbox dates require delivery provenance

- Authority: observed-day, hook, and seasonal coverage requirements.
- Decision: trust mbox delivery headers, Received headers, or the mbox separator
  in that order. Keep sender-controlled Date and file-mtime fallbacks in the
  private library but exclude them from cadence, month, seasonal, and hook-day
  gates; count them under `Unknown receipt date` so totals still reconcile.
- Reason: untrusted fallbacks can manufacture a 45-day, 90-day, or annual
  history that was never observed by the inbox.
- Impact: IMAP `INTERNALDATE` remains the preferred canonical source. Public
  filesystem writes also reject symlink roots, managed subdirectories, and
  sensitive file targets, with no-follow reads and lock opens.
- Status: active.

## 2026-07-14T11:02:00Z: Large IMAP windows require bounded batch fetching

- Authority: execution order instruction to avoid waiting and continue after a
  launch-critical step exceeds 10 minutes.
- Decision: stop the first authenticated backfill after measuring 70,199 source
  UIDs in the approved 12-month window, then replace 1-message-per-request
  fetching with bounded read-only UID batches before restarting the full pass.
- Reason: the serial adapter would take hours and add tens of thousands of
  avoidable round trips. A bounded batch preserves the source definition,
  privacy boundary, and read-only behavior while making same-session execution
  practical.
- Impact: 1,558 raw messages from the stopped attempt remain private outside
  Git. The restarted pass reuses source identities and deduplication, and the
  Early Data Gate is evaluated only after a complete source iteration.
- Blocker duration: more than 10 minutes without a complete Phase 1 census.
- Resolution: bounded batch fetching reduced the request count from 70,199 to
  about 351, but Gmail still throttled the approximately 4 GB raw transfer.
- Status: complete; superseded by the approved existing-export route below.

## 2026-07-14T11:18:00Z: Checkpoint A uses the existing swipe-file export

- Authority: execution-order override allowing the existing dedicated-inbox
  export when it is faster than IMAP.
- Decision: use the real Email Swipe File export from the existing landing-page
  repository as the production census input for Checkpoint A. Keep the public
  lead-magnet product capable of direct IMAP and mbox ingestion.
- Reason: the export contains 1,249 real emails across 37 brands and spans July
  2023 through July 2026. It can support the approved same-session launch while
  a complete 70,199-message raw IMAP transfer cannot.
- Impact: source completeness is labeled `Curated export`. No single-brand hook
  can clear the complete-range requirement, so Checkpoint A must use a
  multi-brand fallback with the export limitation stated. No export record or
  asset enters the public competitor-inbox repository.
- Early Data Gate: passed with 1,238 qualified broadcasts from 36 contributing
  brands across a 37-brand source census; 31 brands cleared 15 broadcasts
  across at least 45 observed days. The trusted export window is July 11, 2023
  through July 8, 2026. Nine lifecycle messages and 2 uncertain messages remain
  outside broadcast metrics.
- Status: active and cleared for Phase 2.

## 2026-07-14T11:54:00Z: Curated annotations remain subordinate to evidence

- Authority: numeric-claim and classification requirements.
- Decision: deterministic evidence from sanitized subject, preheader, and
  visible text wins over curated annotations. The importer may preserve a
  nonnumeric offer, a recognized occasion, or an intent fallback, but it drops
  every curated numeric depth unless the claimed source text contains it.
- Reason: an export annotation can improve the census without becoming an
  unsupported public claim.
- Impact: all 642 offer classifications have visible evidence or a nonnumeric
  curated offer; numeric offer summaries use deterministic evidence only.
- Status: active.

## 2026-07-14T11:55:00Z: Multi-brand fallback is the only eligible hook

- Authority: Checkpoint A hook gate and curated-export provenance.
- Decision: use `1,249 emails from 37 brands` as the source-level hook, then
  disclose that 1,238 qualified broadcasts from 36 contributing brands power
  the strategy metrics.
- Reason: every brand record inherits `curated_export`, so no single brand has
  a complete error-accounted source range even when it clears 30 broadcasts
  and 90 observed days.
- Impact: SKIMS, OLIPOP, Poppi, AG1, Liquid Death, and every other single-brand
  candidate are disqualified. Huel and Nike are absent from the export.
- Status: active.

## 2026-07-14T11:56:00Z: Export caps cannot support volume-leader claims

- Authority: claim accuracy requirement.
- Decision: remove inbox-volume rankings from the Executive Brief and replace
  them with global mix, offer, seasonal, and coverage findings.
- Reason: many brands stop at exactly 40 records in the curated export, so a
  apparent volume leader would describe the export cap rather than competitor
  cadence.
- Impact: the dashboard describes observed behavior and denominators without
  implying that a capped sample identifies the most frequent sender.
- Status: active.

## 2026-07-14T11:57:00Z: Checkpoint A runs without optional AI processing

- Authority: approved deterministic-only fallback.
- Decision: freeze the real dashboard in `deterministic-only` mode because no
  Anthropic API key was configured for this run.
- Reason: classification, promotion, seasonality, coverage, and aggregation can
  complete locally; unavailable AI-only analysis must not block launch.
- Impact: the freeze manifest records a null model and deterministic-only mode.
  The public product keeps the optional configurable classifier and private
  cache path.
- Status: active.

## 2026-07-14T11:58:00Z: Manual QA clears the approved disagreement gate

- Authority: Phase 2 QA requirements.
- Decision: accept the deterministic census after reviewing 60 records across
  31 brands, including 30 promotions.
- Reason: 4 classification disagreements produce a 6.7% disagreement rate,
  which is below the 10% limit. The promotion review found 0 unsupported
  numeric claims, and the privacy review found 0 recipient or personalized
  token leaks.
- Impact: Phase 2 is cleared for Checkpoint A with the curated-export
  limitation still attached to every finding.
- Status: complete.

## 2026-07-14T11:59:00Z: Hero screenshots fail closed on visual corruption

- Authority: real screenshot and 1080 by 1350 readability requirements.
- Decision: render 2 deterministic real-data candidates, audit their pixel
  dimensions and black-row/light-pixel shares, and retry before failing the
  build when a renderer produces a corrupt surface.
- Reason: a technically created PNG is not sufficient proof that the launch
  image is readable.
- Impact: both selected candidates are 1080 by 1350, passed the automated
  visual audit, and were inspected manually. The dashboard-style candidate is
  preferred because it shows the product package as well as the census.
- Status: complete.

## 2026-07-14T12:00:00Z: Browser policy blocks local file navigation

- Authority: implementation judgment and tool safety policy.
- Decision: do not bypass the in-app browser's block on `file://` navigation.
  Use static responsive tests, CSP/network assertions, screenshot rendering,
  pixel audit, and manual image inspection for Checkpoint A instead.
- Reason: the blocked navigation is a browser-tool security boundary, not a
  dashboard runtime error.
- Impact: local interactive desktop/mobile browser QA remains a disclosed
  Checkpoint A weakness. It must be repeated through an approved local serving
  route before Checkpoint B.
- Status: active.

## 2026-07-14T12:01:00Z: Final freeze binds to a clean immutable Git state

- Authority: privacy audit, freeze, and Checkpoint A reproducibility rules.
- Decision: scan the working tree, staged and untracked files, every reachable
  Git blob, and unreachable Git objects before pushing. After the final commit,
  rebuild the production dashboard so the manifest records the exact 40-digit
  Git SHA and `git_dirty=false`.
- Reason: deleting a sensitive file from the current tree would not remove it
  from Git history or unreachable local objects.
- Impact: Checkpoint A numbers, HTML, screenshots, census, and Git source state
  can be tied to one immutable manifest.
- Status: complete.

## 2026-07-14T12:23:02Z: Demo builds as one deterministic public package

- Authority: Phase 4 demo and fresh-install requirements.
- Decision: make both `demo` and the no-production-data path of `build`
  regenerate the full Northstar Apparel package. Bind the fixed 1,260-message
  census, both hero surfaces, and the dashboard into a stamped freeze manifest.
- Reason: a dashboard-only fallback could drift from the dataset and did not
  prove that every demo surface was illustrative.
- Impact: the dataset and census are deterministic across clean installs, every
  private demo file uses mode `0600`, and `verify` checks records, quadrants,
  stamps, CSP, hashes, and directory modes without requesting credentials.
- Status: complete.

## 2026-07-14T12:23:02Z: One lock and rollback boundary covers the full update

- Authority: Phase 4 scheduler safety requirements.
- Decision: hold a separate private process lock around ingestion, analysis,
  rendering, and replacement. Snapshot the full managed output generation before
  that boundary and restore every file if any later stage fails.
- Reason: stage-level ingestion locks did not prevent 2 scheduled runs from
  overlapping during analysis or rendering, and a failure after HTML replacement
  could otherwise leave a partial package.
- Impact: overlapping LaunchAgent runs skip cleanly. A failed build restores the
  prior coverage, dashboard, census, hero files, and freeze manifest, records a
  failed run state, and sends only a non-sensitive local notification.
- Status: complete.

## 2026-07-14T12:23:02Z: LaunchAgent remains local and secret-free

- Authority: Phase 4 LaunchAgent contract.
- Decision: keep credentials and API configuration out of the plist. The job
  invokes the installed module, `update`, the private data-root path, and the
  private config path when the user explicitly selected one. It runs at 7:00 AM
  local with `RunAtLoad`.
- Reason: Keychain and private config are the approved credential boundaries.
  LaunchAgent environment variables would create another secret surface.
- Impact: logs use mode `0600`, status reports the 14-day overlap, full-run lock,
  rollback behavior, and Mac-on dependency, and the plist contract is covered by
  tests.
- Status: complete.

## 2026-07-14T12:42:50Z: Normal dashboard builds remain browser-free

- Authority: fresh-install and no-hidden-dependency requirements.
- Decision: generate static hero HTML during every real build, but render PNG
  launch images only when `build --render-heroes` is requested. Scheduled
  updates never invoke a browser.
- Reason: screenshot production is a distribution task. Making it part of every
  local dashboard refresh created an undocumented Chrome-family dependency and
  an unnecessary daily failure surface.
- Impact: `doctor`, `demo`, `build`, `update`, and `verify` work without Chrome.
  The optional launch-image command documents its browser requirement.
- Status: complete.

## 2026-07-14T12:42:50Z: Verification is read-only and generation-bound

- Authority: Phase 4 verification, freshness, and fresh-install requirements.
- Decision: production `verify` reads the frozen output package without running
  analysis or optional AI. It cross-checks the census, dashboard, hero files,
  optional screenshots, coverage gate, window, and manifest hashes. The
  dashboard carries an explicit successful-generation freshness badge.
- Reason: verification must detect mixed output generations and must not mutate
  data or make an Anthropic request. Clean-install evidence must also prove no
  unconditional runtime dependency or machine-specific user-home path remains.
- Impact: a stale or partially replaced package fails closed, and the fresh
  install test runs with closed stdin, `pip check`, metadata inspection, private
  artifact checks, and local-path scanning.
- Status: complete.

## 2026-07-14T13:56:05Z: Launch hero priority is explicit and denominator-led

- Authority: approved launch hook rule and the generic product-usefulness
  requirement.
- Decision: activate the ordered SKIMS, Olipop, Poppi, AG1, Huel, Liquid Death,
  and Nike universe only through repeated `build --hero-priority-brand` options.
  A launch build considers eligible brands inside that universe, ranks them by
  qualified-broadcast denominator, uses the supplied order for equal
  denominators, and uses the multi-brand fallback when none qualifies. A normal
  product build without the option still considers every eligible competitor.
- Reason: the prior global sort allowed a higher-volume unlisted brand to power
  the launch hero. The approved plan does not define a separate subjective
  finding-strength score, so the largest qualified denominator is the strongest
  evidence without inventing a marketing heuristic.
- Impact: the visible hero and freeze manifest now bind the selected brand,
  numerator, denominator, exact date window, observed days, and coverage label.
  Freeze creation rejects hero HTML from a different census, and `verify`
  rejects a modified hero-selection contract or hero HTML even when its stored
  hash is also rewritten. A pre-change freeze without this contract must be
  rebuilt before it can pass verification.
- Status: complete.

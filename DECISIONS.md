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

## 2026-07-14T11:18:00Z: Checkpoint A rehearsal uses the existing swipe-file export

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
- Status: complete rehearsal; superseded by the complete IMAP source recorded
  below.

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
- Status: complete rehearsal; superseded for the launch census.

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
- Status: complete rehearsal; superseded by the live multi-brand fallback
  recorded below.

## 2026-07-14T11:56:00Z: Export caps cannot support volume-leader claims

- Authority: claim accuracy requirement.
- Decision: remove inbox-volume rankings from the Executive Brief and replace
  them with global mix, offer, seasonal, and coverage findings.
- Reason: many brands stop at exactly 40 records in the curated export, so a
  apparent volume leader would describe the export cap rather than competitor
  cadence.
- Impact: the dashboard describes observed behavior and denominators without
  implying that a capped sample identifies the most frequent sender.
- Status: complete rehearsal; the live IMAP source has no export cap.

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
- Impact: the rehearsal passed its QA gate. The live IMAP census receives a
  separate 60-record review before Checkpoint B.
- Status: complete rehearsal; superseded for launch evidence.

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
- Impact: Checkpoint A disclosed the missing interactive pass. The live
  dashboard was later served only on `127.0.0.1` and verified at desktop and
  mobile widths before Checkpoint B.
- Status: complete.

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
- Status: implementation complete; the clean `v1.0.0` tag and immutable
  fresh-install evidence remain pending.

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

## 2026-07-14T14:37:05Z: Complete IMAP evidence supersedes the export rehearsal

- Authority: execution-order preference for the existing dedicated inbox when
  a same-session 12-month backfill can finish.
- Decision: use the completed read-only IMAP backfill as the launch source and
  retain the curated export only as archived rehearsal evidence. Review all 876
  observed sender domains, include 35 verified domains, and map them to the
  canonical 37-brand DTC universe.
- Reason: the live inbox provides a current, defined receipt window and removes
  the export-cap limitation. Fuzzy matches remain excluded unless a human
  review confirms the sender identity.
- Impact: the full IMAP review cleared the Early Data Gate and established the
  reviewed sender universe. Unreviewed sender domains stay outside every launch
  denominator. Huel and Nike were not observed.
- Status: historical source-selection decision. Its pre-hardening census was
  superseded by the final canonical reprocessing entry below.

## 2026-07-14T14:37:06Z: Unattributed failures force the multi-brand hook

- Authority: complete error-accounted hook requirement.
- Decision: preserve all 691 malformed source messages as unattributed parse
  failures and label the source `partial`. Use the multi-brand fallback even
  though several priority brands clear the message and day thresholds.
- Reason: an unattributed failure cannot be proven irrelevant to a single-brand
  claim. A portfolio census can disclose that limitation while a brand-specific
  claim would overstate source completeness.
- Impact: no priority brand powers the hook. The frozen launch hook uses the
  full reviewed multi-brand denominator with the partial-coverage label visible
  in the image.
- Status: active decision; the final counts live only in the current canonical
  census entry and freeze manifest.

## 2026-07-14T14:39:19Z: Owner strategy labels require enough history

- Authority: posture and annual-planning gates in the approved plan.
- Decision: withhold posture labels below 30 qualified broadcasts or 90 observed
  days. Build the Seasonal Planner only from brands with at least 330 observed
  days.
- Reason: a thin current-activity sample cannot support an owner-level strategic
  posture or a prior-season planning claim.
- Impact: every dashboard section carries its eligible denominator, and the
  hero shows the current-through date, 7:00 AM local schedule, 14-day overlap,
  and Mac-on dependency.
- Status: active methodology; the final eligible count lives in the current
  canonical census and freeze manifest.

## 2026-07-14T14:41:38Z: Local desktop and mobile dashboard QA is complete

- Authority: Checkpoint B responsive and network-isolation requirements.
- Decision: serve the private dashboard on `127.0.0.1` for an interactive
  desktop and 390 by 844 mobile pass, then stop the local server.
- Reason: this tests the actual generated page without weakening the browser's
  `file://` safety boundary or exposing the dashboard externally.
- Impact: both views rendered without page-level horizontal overflow. The page
  contained 0 scripts, 0 remote resource attributes, a restrictive CSP, 7
  sections, the correct freshness badge, and the 330-day seasonal denominator.
  The competitor table remains intentionally horizontally scrollable on mobile.
- Status: complete.

## 2026-07-14T14:42:00Z: Asana is an internal staging surface only

- Authority: approved pre-Checkpoint-B staging boundary.
- Decision: create the internal launch task in `Marketing - 2026 Content`, place
  it in `Need to Organize`, assign Michelle Parada, and set Status to `Ready to
  Post` and Type to the live `Linkedin` option.
- Reason: the stable task URL is needed for final package wiring, but staging
  cannot become distribution.
- Impact: the task carries a hard zero-distribution notice. Final post copy,
  images, the verified Notion URL, tag, and Bolu instructions replace the
  staging note before Checkpoint B. No LinkedIn activity, DM, connection, or
  announcement occurred.
- Status: staged; final readback and attachment verification pending.

## 2026-07-14T15:28:56Z: Independent QA invalidated the pre-hardening freeze

- Authority: Phase 2 QA and zero-recipient-leak requirements.
- Decision: invalidate the first live census for launch use and block its
  numbers from downstream copy.
- Evidence: an independent 60-record review across 31 brands and 30 promotions
  found 33 record-level classification disagreements, including 20 hard-axis
  disagreements. Numeric offer evidence produced 0 unsupported claims, but 11
  full normalized records contained recipient or personalized-token residue.
- Impact: the sanitizer, deterministic classifier, census, dashboard, and
  launch freeze required a canonical rebuild from retained private raw mail.
- Status: superseded by the remediations below. The detailed report remains
  private and redacted.

## 2026-07-14T15:28:57Z: Recipient sanitization now covers encoded short keys

- Authority: production-data isolation and zero-recipient-leak requirements.
- Decision: decode nested HTML entities and remove quoted-printable,
  percent-encoded, query-string, and schemeless short-key tracking fragments
  before persistence, optional AI processing, logging, or rendering.
- Evidence: the corpus audit found 318 affected records and 1,727 encoded
  short-key matches before remediation. Canonical reprocessing produced 0
  high-confidence direct-identifier findings across the rebuilt private store.
- Impact: no raw or normalized production content entered Git. The public
  privacy audit now detects the same encoded tracking shapes.
- Status: implementation complete; the regenerated independent sample supplies
  final Checkpoint B evidence.

## 2026-07-14T15:28:58Z: Deterministic classification is grounded in the lead

- Authority: the 10% Phase 2 disagreement ceiling.
- Decision: weight subject, preheader, and lead content above footer
  boilerplate; narrow lifecycle triggers; suppress standing shipping-policy
  and product-bundle false offers; and expand supported gift, trial, seasonal,
  launch, testimonial, and ambassador evidence.
- Evidence: the preserved labels-only ground truth was not edited. The updated
  rules reduced disagreement from 33 of 60 to 2 of 60. Offer presence, primary
  offer type, seasonality, and occasion produced 0 disagreements, and a
  full-corpus dry reanalysis produced 0 errors and 0 unsupported numeric offers.
- Impact: deterministic-only mode clears the approved disagreement ceiling
  without sending source text to an external model.
- Status: implementation complete; the regenerated independent sample remains
  the final launch gate.

## 2026-07-14T15:28:59Z: The hardened live census supersedes prior live counts

- Authority: cross-foot, frozen-number, and Early Data Gate requirements.
- Decision: use the canonically reprocessed and incrementally refreshed store
  for every current denominator. Earlier live counts remain historical only.
- Evidence: the current reviewed universe contains 3,860 parsed delivery
  variants, 17 collapsed variants, and 3,843 distinct messages. Scope reconciles
  to 3,754 qualified broadcasts, 88 lifecycle messages, and 1 uncertain
  messages. The separate failure ledger preserves 691 parse failures. All
  cross-foot checks and the Early Data Gate pass.
- Impact: the launch remains on the multi-brand fallback because unattributed
  failures prevent a complete error-accounted single-brand claim. The current
  hero uses 3,843 emails from 33 contributing brands and visibly labels source
  coverage as partial.
- Status: current canonical QA corpus; a final clean tagged rebuild still
  controls every distribution claim.

## 2026-07-14T15:29:00Z: Incremental state separates corpus history from fetch

- Authority: coverage-gate and 14-day-overlap requirements.
- Decision: retain the earliest successful backfill start in `source_window`
  and write the newest overlap request to `last_fetch_window` in both metadata
  and state.
- Reason: replacing a year-long corpus window with a 14-day request makes an
  intact history appear ineligible for cadence, posture, and seasonal gates.
- Impact: the reviewed source now covers July 14, 2025 through July 14, 2026,
  while the latest fetch window remains separately auditable.
- Status: implementation complete with a regression test and live readback.

## 2026-07-14T15:29:01Z: Output publication is not power-loss transactional

- Authority: truthful recovery and scheduler documentation requirements.
- Decision: describe managed output files as individually atomic and caught
  failures as package-restoring. Do not claim package-wide atomicity across an
  abrupt shutdown.
- Reason: power loss can occur between separate file replacements.
- Impact: `verify` fails closed on a mixed generation,
  `dashboard.previous.html` remains available, and the operator reruns `build`
  followed by `verify` after restart.
- Status: documented residual risk.

## 2026-07-14T15:29:02Z: Installed builds resolve their source checkout

- Authority: immutable-tag and clean-freeze requirements.
- Decision: resolve pip's PEP 610 `direct_url.json` before cwd and source-tree
  fallbacks when collecting Git state.
- Reason: a regular `pip install .` places modules in `site-packages`, where
  walking upward from `__file__` cannot find the release checkout.
- Impact: production verification requires a 40-character Git SHA and
  `git_dirty=false`; the fresh-install test requires the freeze SHA to match the
  detached tag checkout.
- Status: implementation and regression test complete; the remote-tag run
  remains pending.

## 2026-07-14T15:29:03Z: Repository privacy auditing covers hidden surfaces

- Authority: zero-production-data public-history requirement.
- Decision: remove filename-based trust for `.example` files and exempt only
  reserved test-domain fixtures. Scan generated directories, caches,
  filenames, encoded tracking fragments, LFS pointers, submodule manifests,
  the index, all refs and blobs, and unreachable objects.
- Impact: the final release also fetches remote heads, tags, and pull-request
  refs and inventories GitHub releases and Actions artifacts before clearance.
- Status: implementation and regression tests complete; the final private
  deny-list audit and remote inventory remain pending.

## 2026-07-14T15:29:04Z: Frozen numeric tokens retain their units

- Authority: frozen-value and stale-count requirements.
- Decision: preserve `$` and `%` in validator tokens, infer percentage units
  from share, percentage, and rate metrics, and require units for static
  operational numbers.
- Reason: a count such as `33` must not authorize an unrelated `33%` claim.
- Impact: counts, percentages, and currency no longer cross-authorize each
  other.
- Status: implementation and regression test complete; the assembled launch
  package still requires the final validator pass.

## 2026-07-14T15:29:05Z: Staging remains separate from distribution

- Authority: approved pre-Checkpoint-B staging boundary.
- Decision: keep the public repository, unlisted Notion site, and internal Asana
  task as staging surfaces only. Search indexing and page duplication are off
  on the Notion site.
- Impact: no LinkedIn activity, DM, connection, announcement, or person-to-person
  asset link has occurred. Final Notion copy, logged-out verification, Asana
  wiring, and attachment readback still depend on the clean tagged freeze.
- Status: active distribution lock.

## 2026-07-14T15:29:06Z: Checkpoint B discloses every operating limit

- Authority: approved Checkpoint B residual-risk requirements.
- Decision: disclose that Gmail app passwords provide broad mailbox access;
  Workspace policy may disable app passwords; optional AI sends sanitized text
  to Anthropic; lifecycle classification contains judgment-error risk;
  scheduled updates depend on the Mac being on or waking; inbox data shows
  competitor behavior rather than performance; and an abrupt shutdown can
  interrupt the per-file output sequence.
- Status: mandatory for the final packet and relevant product documentation.

## 2026-07-14T16:25:00Z: Privacy checks validate sanitized fields independently

- Authority: zero-recipient-data and fail-closed parsing requirements.
- Decision: validate the sanitized subject, preheader, and visible text as 3
  separate fields instead of joining them before the final boundary check.
- Reason: the join can manufacture a quoted-printable soft break when a safe
  preheader ends in a literal equals sign and visible text follows after the
  separator newline. Each stored field is already sanitized independently.
- Impact: the parser still fails closed on unsafe content, while ordinary
  equations and literal trailing equals signs remain intact. A regression test
  covers this exact field-boundary case.
- Status: implementation and regression test complete.

## 2026-07-14T16:28:00Z: Reprocessing preserves the reviewed source window

- Authority: 12-month backfill, denominator integrity, and frozen-window
  requirements.
- Decision: use the candidate's reviewed `defined_source.source_window` during
  a canonical MIME reparse. Retained raw archives are lookup sources only and
  cannot widen the candidate window.
- Reason: the private archive roots span more history than the reviewed
  12-month candidate. Unioning archive metadata would silently change the
  denominator without adding records.
- Impact: all 3,843 candidate records matched retained raw mail and reparsed,
  while the source window remains July 14, 2025 through July 14, 2026.
- Status: complete; the regenerated census and independent QA control the
  release.

## 2026-07-14T17:01:24Z: Recipient-safe text cleanup must reach a fixed point

- Authority: absolute data isolation and zero-recipient-data requirements.
- Decision: apply bounded residual cleanup until stable, redact rendered
  greetings across common punctuation, and replace structurally detected
  multi-line postal-address blocks before any normalized record persists.
- Reason: malformed removed-link wrappers can expose a new unsafe layer only
  after the outer wrapper is removed, and one lifecycle message contained a
  repeated shipping-address block that single-token detectors could not see.
- Impact: synthetic regressions cover nested wrappers, rendered greetings, and
  postal blocks. An independent full-corpus scan checked all 3,843 records and
  11,529 persisted text fields with 0 remaining direct identifiers, address
  blocks, transfer payloads, tracking tails, markup residue, or non-idempotent
  fields.
- Status: implementation, public tests, and private full-corpus adversarial
  review complete; canonical raw-mail reprocessing still controls release.

## 2026-07-14T18:08:00Z: Ambiguous MIME and personalization fail closed

- Authority: absolute data isolation and broadcast-metric integrity.
- Decision: exclude every named or attached MIME leaf from analysis, unwrap
  only an unambiguous forwarded message, and classify an ambiguous embedded
  message or non-bulk message without marketing evidence as uncertain.
- Decision: remove recipient addresses, names, international phone numbers,
  postal addresses, order or tracking identifiers, nested transfer encodings,
  and tracking links to a fixed point before persistence or optional AI use.
- Evidence: the independent synthetic matrix passed 549 of 549 assertions
  across 123 adversarial scenarios. The public suite passed all 261 tests.
- Impact: 1 message in the final reviewed corpus remains uncertain and stays
  outside all broadcast metrics.
- Status: complete.

## 2026-07-14T18:08:01Z: Numeric claims bind to their exact evidence

- Authority: unsupported-numeric-claim prohibition.
- Decision: a deterministic numeric offer is valid only when its cited source
  contains the evidence and reparsing that exact evidence produces the same
  offer type and depth.
- Reason: a stored 20% depth must not be authorized by evidence that says 10%.
- Impact: numeric offer summaries fail closed on mismatched evidence, even when
  the claimed evidence string is present in the source field.
- Status: complete with regression coverage.

## 2026-07-14T18:08:02Z: Distribution numbers require semantic freeze bindings

- Authority: no stale count or misapplied frozen number in a final artifact.
- Decision: every quantitative claim in LinkedIn, the pinned comment, Notion,
  and Asana declares named claim groups bound to explicit `metrics.*` fields.
  Every numeral occurrence must be covered by a bound claim or an exact
  reviewed operational context.
- Reason: a number that exists somewhere in the freeze cannot authorize an
  unrelated claim with the same value.
- Impact: each launch finding binds its numerator and denominator separately;
  a same-value, wrong-field adversarial claim fails validation.
- Status: complete; public and private package tests pass.

## 2026-07-14T18:08:03Z: The defined-source ledger follows final scope analysis

- Authority: complete raw tables and cross-foot requirements.
- Decision: after canonical reanalysis, rewrite the defined-source included
  broadcast, lifecycle, and uncertain counts from the same final coverage
  table used by the dashboard and launch freeze.
- Reason: the source ledger previously retained a pre-reclassification scope
  split even though the final total still cross-footed.
- Impact: the defined-source ledger, Phase 1 coverage table, 4-quadrant census,
  dashboard, hero images, and copy input now share the same 3,843-message
  denominator and 3,754/88/1 scope split.
- Status: complete; the candidate audit now rejects any future ledger drift.

## 2026-07-14T18:55:41Z: Inline delivery identifiers require a patch release

- Authority: absolute recipient-data isolation and zero-distribution rules.
- Decision: redact an inline order, shipment, or package identifier only when
  delivery-status language confirms the surrounding text is transactional.
  Preserve the surrounding subject copy and apply the same detector at every
  persistence and export assertion.
- Reason: a final visual audit caught a recipient-specific delivery identifier
  that the earlier line-oriented label rule did not cover. The narrow context
  gate avoids treating ordinary quantities or promotion codes as private IDs.
- Impact: synthetic coverage includes compact, separated, and punctuated IDs,
  modified delivery-status phrases, idempotence, detector parity, and negative
  marketing-copy cases. The package version advances to `1.0.1`; the record
  schema remains `1.0.0` because no field changed.
- Status: implementation and public regression coverage complete. Production
  outputs must be re-sanitized, rebuilt, and independently rechecked before
  launch staging resumes.

## 2026-07-14T19:08:32Z: Launch images must remain readable in the feed

- Authority: Checkpoint B visual-readiness requirement.
- Decision: size important hero support copy at 40 source pixels, which renders
  at 14.44 pixels when a 1080-pixel image is shown at a 390-pixel feed width.
  Remove explanatory microcopy that cannot meet that contract.
- Reason: the first final visual audit found that both technically valid hero
  images became too small to read in a mobile LinkedIn feed.
- Impact: the revised heroes retain the frozen counts, denominator, date
  window, update cadence, coverage label, and 4 package callouts. Synthetic
  tests enforce the feed-scale calculation and required visible fields.
- Status: implementation and focused rendering tests complete. Final images
  still require a fresh production render and independent visual review.

## 2026-07-14T19:37:22Z: Reviewed-source evidence must advance with every update

- Authority: scheduled-update integrity, complete raw tables, and cross-foot
  requirements.
- Decision: when a private store already contains an independently reviewed
  `defined_source` ledger, each successful ingest synchronizes its nested date
  window, included counts, source-ingestion ledger, and post-alias dedupe
  counts. Analysis refreshes the final scope split without adding inputs again.
- Decision: treat the unique stable IDs inside each variant cluster as the
  delivery-count source of truth, and reject a repeated overlap delivery whose
  ID is already retained in that cluster.
- Reason: the first incremental production refresh advanced the records and
  top-level window but left the nested reviewed-source evidence frozen at the
  prior run.
- Impact: the mapping and exclusion assertions stay unchanged while each
  future 7:00 AM local update keeps the source ledger tied to the retained
  corpus. Existing overlap-inflated counts repair on the first load and save.
  The package version advances to `1.0.2`; the record schema remains `1.0.0`
  because no record field changed.
- Status: implementation, integration regression, and full public suite
  complete. Production must run once under `v1.0.2` and pass the private audit
  before the final freeze.

## 2026-07-15T03:12:02Z: The launch stays dashboard-first

- Authority: final pre-launch order and the locked production census.
- Decision: use 2 real-dashboard 1080 x 1350 hero candidates and a silent
  30-second dashboard scroll as the launch visuals. The static messaging table
  remains the product's launch library view.
- Reason: the dashboard is the product. A private render sample produced 125
  privacy-safe emails across 32 of 33 contributing brands, but LMNT had 0 safe
  candidates and Four Sigmatic had 1. That cannot satisfy the approved 3 to 5
  safe renders per brand requirement.
- Impact: the optional gallery and full-archive renderer remain v1.1 work. No
  rendered creative, production data, or private manifest enters this public
  repository.
- Freeze: the census stays locked at SHA-256
  `65ee5df897c06aada91634a765abfeaa079c061a8e91bbe9cdd193a85fc5a4f7`.
  The dashboard stays locked at SHA-256
  `b25fddc75a4e6a610f378f0bf8a271300bd66b8e5472ba41e450be5a3481191c`.
- Distribution lock: no LinkedIn activity, DM, connection, announcement, or
  person-to-person asset link occurred.
- Status: complete.

## 2026-07-15T04:27:24Z: The current landing-page system controls visual tokens

- Authority: the Phase 1 visual order states that the current landing-page
  repository and live site win when their tokens conflict with the explicit
  fallback list.
- Decision: use the current ZHS dark system: Inter Tight, a black canvas,
  near-black surfaces, white text, restrained neutral borders, and blue
  `#3D6CFF` only as an accent. Outer product surfaces use 18-pixel radii and
  nested controls use 12-pixel radii.
- Evidence: current landing-page `main` and the live homepage agree on Inter
  Tight and the dark palette. The bundled local Inter Tight WOFF2 is byte-for-
  byte identical to the current landing-page build asset and remains covered
  by the SIL Open Font License 1.1.
- Impact: the superseded light/Montserrat draft is discarded before any real
  dashboard or launch visual is regenerated. Generated HTML embeds the font as
  a data URL, permits only `font-src data:`, and still makes zero external
  requests.
- Distribution lock: no LinkedIn activity, DM, connection, announcement, or
  person-to-person asset link occurred.
- Status: implementation and frozen visual regeneration complete. Desktop and
  390-pixel mobile QA pass, the browser made zero non-file/data requests, and
  the locked census is unchanged.

## 2026-07-15T04:44:19Z: Freeze the polished main commit without a new release

- Authority: Phase 1 requires the public README and polished dashboard on
  `main`; standing constraints prohibit a new public tag or release.
- Decision: bind the launch visuals and install instructions to the exact
  polished `main` commit. Keep `v1.0.3` and its commit separately labeled as
  the latest historical public release.
- Reason: pairing the new dashboard hash with the old release SHA would falsely
  identify which code rendered the launch assets. Installing `v1.0.3` would
  also give recipients the superseded dashboard styling.
- Impact: the private copy generator and package assembler use a distinct
  `visual_source_sha`. Start Here checks out that commit in detached mode. The
  freeze binds to the visual source, while release metadata remains accurate
  and no new tag is created.
- Distribution lock: no LinkedIn activity, DM, connection, announcement, or
  person-to-person asset link occurred.
- Status: complete. The public visual source is pinned separately from the
  historical release, and downstream launch assets are regenerated only from
  a clean checkout of that exact commit.

## 2026-07-15: Phase 2 preserved the launch freeze

- Authority: Phase 2 launch-freeze boundary.
- Decision: keep `main`, live Notion, Asana, the launch heroes, the pinned
  proof card, scroll video, launch copy, and every frozen hash unchanged while
  Phase 2 work proceeds on private storage and `v1.1-dev` only.
- Evidence: independent integrity audit confirmed local, origin, and remote
  `main` at `a62e4cc73ceffe7637217f8af1ab3ee152466ddb`; all launch and copy hashes
  match; the full Git privacy audit reports 0 violations.
- Impact: the Thursday launch package remains reproducible and isolated from
  the background renderer and gallery work.

## 2026-07-15: The creative gallery is private-manifest driven

- Authority: Gallery v1.1 and data-isolation requirements.
- Decision: build the gallery on `v1.1-dev` from a private, fail-closed safe
  thumbnail manifest. Embed validated local PNG, JPEG, or WebP bytes as data
  URIs, cap each brand at 5 previews, and show explicit ready, insufficient,
  or unavailable states for every census brand.
- Reason: the public repository can own renderer and UI behavior without
  carrying production paths, IDs, subjects, bodies, URLs, or creative files.
- Evidence: 324 tests pass; privacy audit reports 0 violations; the current
  private sample renders 125 previews across 32 brands, while the gallery
  covers all 33 census brands through explicit states: 31 ready, Four
  Sigmatic insufficient at 1, and LMNT unavailable at 0.
- Limitation: the dashboard HTML is about 8.3 MB with 125 embedded thumbnails,
  and the gallery consumes rather than generates the private manifest.

## 2026-07-15: Full-archive renders require uniform hardened provenance

- Authority: full-archive render, privacy isolation, and anti-stall rules.
- Decision: retain the original render attempts as audit evidence but exclude
  them from final accounting after independent review found network,
  containment, resume-integrity, and decode-boundary gaps. Restart the archive
  under a new private pipeline version only after offline safety tests pass.
- Controls: direct pinned-IP TLS on port 443 with hostname certificate checks,
  no ambient proxy/cookies/referrer, exact browser file allowlisting, strict
  URL and redirect policy, bounded raster and embedded-image decoding, local
  OCR privacy checks, master-bound retry and success state, artifact-aware
  resume validation, private modes, free-space reserve, transient sanitized
  HTML deletion, and explicit partial/complete accounting.
- Final evidence: pipeline `2026-07-15.8` resolved all 3,755 qualified
  broadcasts against production master SHA-256
  `2c71899db1f1092e189c124cb4f9ea8f9cc2909031d07c361458e99a1fd565b1`.
  It produced 2,998 privacy-cleared renders, 755 terminal safety exclusions,
  and 2 exhausted offline Vision failures, with 0 pending records and 0
  integrity requeues. The final manifest SHA-256 is
  `8d6b2ef31c7510ad7b1ae43a3062b5df55179ec14da1f6970b6828a6537871fe`;
  the ledger SHA-256 is
  `3db1f2d9c1816164ce146b9f4d3fec5616f1c34f855a7d371860d2e1fe5a6ade`.
  Report-only resume attempted 0 records, all 2,998 accepted artifact pairs
  revalidated by path, mode, hash, type, and dimensions, 37 of 37 safety tests
  passed, and the v7 sanitized staging directory is empty.
- Residual: a short recipient-specific identifier hidden in an otherwise
  ordinary clean remote path can be indistinguishable from a legitimate CDN
  asset slug before fetch. This is disclosed; final outputs remain private and
  every rendered image passes the local OCR/recipient-term gate.

## 2026-07-15: Durable skills remain provisional and fail closed

- Authority: Phase 2 durable-skills requirement and Zach's canonical voice
  rules.
- Decision: install personal `voice-and-copy` and `lead-magnet` skills under
  `~/.codex/skills`, retain `v1 PROVISIONAL pending Claude-side
  reconciliation`, and encode the authority order as live Zach instruction,
  active execution order, skill references, then logged judgment.
- Controls: canonical voice linting, channel playbooks, final editor gate,
  placeholder/link/frozen-number/keyword/Kit/package validation, combined
  Notion-plus-post filing support, and explicit manual visual/browser gates.
- Final evidence: `lead-magnet` passed 62 of 62 tests and
  `voice-and-copy` passed 10 of 10 tests. Both `quick_validate.py` checks,
  canonical fixtures, and an independent six-edge adversarial matrix passed.
- Residual: verified URLs remain a manifest assertion until a logged-out
  browser check runs; screenshots and binary assets still require visual QA;
  factual support, keyword collisions, and semantic editorial quality require
  live project context.

## 2026-07-15: Phase 2 distribution remains locked

- Authority: standing distribution lockdown.
- Decision: keep the follow-up post labeled `DRAFT - NOT CLEARED - DO NOT
  POST`, upload production handoff files only to the existing Drive folder,
  and perform no LinkedIn activity, DMs, connections, announcements, or
  person-to-person link distribution.
- Status: unchanged. Zach remains the manual publisher.

## 2026-07-15: Repository validation uses an isolated home

- Authority: preserve the installed production scheduler while proving the
  public package independently.
- Decision: run the full repository suite with a temporary `HOME` and
  `PYTHONPATH=src` instead of removing or renaming the live LaunchAgent.
- Reason: the dry-run scheduler test correctly expects no target file in a
  fresh environment, while this Mac already has the production plist
  installed. That ambient state is outside the test fixture.
- Evidence: all 324 tests pass in the isolated environment. The same suite's
  only failure under the real home is the expected existing-plist assertion.
- Impact: no production schedule, launch asset, frozen hash, or public branch
  changed for testing.

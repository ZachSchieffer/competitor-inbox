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
- Status: open.

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

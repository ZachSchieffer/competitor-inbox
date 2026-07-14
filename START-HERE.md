# The Competitor Inbox

The Competitor Inbox turns marketing emails already present in an inbox you
control into a private strategy dashboard. It separates evergreen content,
everyday promotions, seasonal promotions, and seasonal content so a brand
owner can plan ahead from observed competitor behavior.

It does not scrape competitor email addresses. Inbox history shows what a
competitor sent, not whether an email performed or converted.

## Requirements

- macOS.
- Python 3.11 or newer.
- A dedicated Gmail or Google Workspace inbox, or a local mbox export.
- Optional Anthropic API access for qualitative classification.

Production data stays in `~/competitor-inbox-data/` by default. Keep that
directory outside every Git worktree. Raw mail, normalized records, config,
AI cache, logs, state, and generated production outputs never belong in Git.

## Install

Clone the repository, open it in Terminal, and create a virtual environment
outside the checkout:

```bash
python3 -m venv ../competitor-inbox-venv
../competitor-inbox-venv/bin/python -m pip install .
../competitor-inbox-venv/bin/python -m competitor_inbox doctor
```

`doctor` checks Python, macOS Keychain availability, private directory modes,
source configuration, and credential readiness. A new install can be system
ready while `production_ready` is false. Run `setup` before a real backfill.

## Run the credential-free demo

The demo uses Northstar Apparel, 10 fictional competitors, and 365 days of
synthetic history. It never asks for inbox credentials.

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox demo
../competitor-inbox-venv/bin/python -m competitor_inbox build
../competitor-inbox-venv/bin/python -m competitor_inbox verify
../competitor-inbox-venv/bin/python -m competitor_inbox open
```

The exact demo census is:

| Quadrant | Messages |
|---|---:|
| Evergreen content | 580 |
| Everyday promotion | 491 |
| Seasonal promotion | 139 |
| Seasonal content | 50 |
| Total | 1,260 |

Every demo surface is marked `ILLUSTRATIVE PROTOTYPE`. The generated package
lives under `~/competitor-inbox-data/demo/` and includes the dataset, census,
dashboard, 2 hero candidates, and freeze manifest.

## Prepare Gmail access

IMAP app passwords provide broad mailbox access. Use a dedicated research
inbox. Google Workspace policy may disable app passwords for that account.

1. Ask a Workspace admin to confirm that the dedicated inbox user can turn on
   2-Step Verification and create app passwords.
2. Sign into Google as the exact dedicated inbox in `<INBOX_ADDRESS>`.
3. Turn on 2-Step Verification for that account.
4. Open the same account's Google Account security settings.
5. Create an app password named `Competitor Inbox`.
6. Copy the 16-character password without spaces. Google displays it once.

If the App passwords option does not appear, use the mbox route below. A normal
Google password is not a substitute. Gmail OAuth and IMAP XOAUTH2 are deferred
in `BACKLOG.md`.

## Configure the private source

Run setup:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox setup
```

For Gmail, accept `imap`, enter the dedicated inbox address, and paste the app
password only at the hidden macOS Keychain prompt. Setup uses Keychain service
`competitor-inbox-imap`. The secret is not placed in command arguments, config,
logs, environment output, or the repository.

The hidden Keychain command shape keeps `-w` last:

```bash
security add-generic-password -a "<INBOX_ADDRESS>" -s "competitor-inbox-imap" -l "Competitor Inbox IMAP" -U -w
```

Never add the password after `-w`. The cursor does not move while you paste.
That behavior is expected.

Setup writes `~/competitor-inbox-data/config.toml` with mode `0600`. Edit that
private file to add a label, sender-domain filters, or brand aliases. Use
`config.example` as the field reference, but do not copy a populated private
config into the repository.

### Mbox fallback

If app passwords are unavailable, run setup on a new private data root, choose
`mbox`, and enter the absolute path to an mbox export stored outside Git. The
mbox adapter reads the export locally and requires no Google credential.

## Backfill and build the real dashboard

Confirm readiness, then backfill 12 calendar months:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox doctor
../competitor-inbox-venv/bin/python -m competitor_inbox backfill --months 12
```

The backfill prints the complete coverage table. It exits with code `4` and
stops if the Early Data Gate fails. A pass requires at least 300 qualified
broadcasts across all brands and at least 1 brand with 15 qualified broadcasts
over 45 observed days.

After the gate passes:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox build
../competitor-inbox-venv/bin/python -m competitor_inbox verify
../competitor-inbox-venv/bin/python -m competitor_inbox open
```

The production dashboard is written to:

```text
~/competitor-inbox-data/outputs/dashboard.html
```

Normal dashboard builds do not require a browser. To create the optional
1080x1350 launch images, run `build --render-heroes` on a Mac with Chrome,
Edge, or Chromium installed. Scheduled updates skip screenshot rendering.

Email HTML is never executed. Remote images are never fetched. The dashboard
contains no runtime JavaScript or external resources. Its freshness badge shows
the date of the last successfully rendered evidence generation.

## Optional AI classification

The deterministic pipeline works without an API key. AI-only sections are
marked unavailable when no key is configured.

When optional AI processing is enabled, the app sends sanitized subject,
preheader, and up to 4,000 characters of visible text to Anthropic. It does not
send addresses, HTML, IDs, URLs, attachments, or tracking parameters. The
result cache stays outside Git.

## Daily local updates

Run one incremental update manually with:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox update
```

Install the LaunchAgent after a successful manual update:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox schedule install
../competitor-inbox-venv/bin/python -m competitor_inbox schedule status
```

The LaunchAgent runs at 7:00 AM local time and at load. Each run uses a 14-day
overlap, and a process lock allows only 1 complete update at a time. Dashboard
files use atomic replacement, and a caught update failure restores the prior
dashboard, census, coverage, hero files, and freeze manifest. The job also
sends a non-sensitive macOS notification. An abrupt shutdown between managed
file replacements can leave an incomplete generation, so `verify` fails closed
and the prior dashboard remains available as `dashboard.previous.html`. Run
`build`, then `verify`, after the Mac restarts. Logs stay private under
`~/competitor-inbox-data/logs/`.

If you use a custom private config, keep the flag before the schedule action so
the LaunchAgent records the same path:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox schedule --config /absolute/private/config.toml install
```

The Mac must be on or wake for the job to run. This is a local scheduler, so it
does not provide cloud uptime while the Mac is off.

Remove the LaunchAgent with:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox schedule remove
```

## Command reference

```text
python3 -m competitor_inbox doctor
python3 -m competitor_inbox demo
python3 -m competitor_inbox setup
python3 -m competitor_inbox backfill --months N
python3 -m competitor_inbox update
python3 -m competitor_inbox build
python3 -m competitor_inbox open
python3 -m competitor_inbox verify
python3 -m competitor_inbox privacy-check
python3 -m competitor_inbox schedule install
python3 -m competitor_inbox schedule status
python3 -m competitor_inbox schedule remove
```

Every command accepts `--data-root`, `--config`, and `--json` before any
schedule action. The default private root is `~/competitor-inbox-data/`.

## Repository privacy audit

Run both checks from the repository root before any push:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox privacy-check
python3 scripts/privacy_audit.py --repo .
```

The local audit scans the working tree, untracked and staged files, local Git
refs and objects (including unreachable objects), raw-mail formats, databases,
secrets, home paths, production addresses, assets, caches, LFS pointers, and
submodules. Before a release, fetch every remote ref and separately inventory
GitHub Releases and Actions artifacts with the GitHub CLI.

After `v1.0.2` exists, run the immutable-ref install test:

```bash
python3 scripts/fresh_install_test.py --ref v1.0.2
```

The test clones into an empty directory, creates a new virtual environment,
installs the package, and runs `doctor`, `demo`, `build`, and `verify`. It fails
if the demo path creates production data, raw mail, config, or credentials.

## Known limits

- App passwords have broad mailbox scope.
- Workspace policy may make app passwords unavailable.
- Lifecycle classification contains judgment-error risk.
- Scheduled updates depend on the Mac being on or waking.
- Inbox history measures competitor behavior, not competitor performance.
- Optional AI processing sends sanitized text to Anthropic.

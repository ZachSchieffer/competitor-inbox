# Start Here

The Competitor Inbox analyzes marketing emails already present in an inbox you
control and builds a private local dashboard.

## Requirements

- macOS.
- Python 3.11 or newer.
- A dedicated Gmail or Google Workspace inbox, or an mbox export.
- Optional Anthropic API access for qualitative classification.

IMAP app passwords provide broad mailbox access and may be disabled by Google
Workspace policy. Use a dedicated research inbox. The deterministic pipeline
works without an AI key.

## Installation

```bash
python3 -m venv ../competitor-inbox-venv
../competitor-inbox-venv/bin/python -m pip install .
../competitor-inbox-venv/bin/python -m competitor_inbox doctor
```

Keep the virtual environment outside the repository when running the full
privacy audit against a production checkout.

## Try the synthetic demo

The demo uses fictional brands and does not request credentials.

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox demo
../competitor-inbox-venv/bin/python -m competitor_inbox build
../competitor-inbox-venv/bin/python -m competitor_inbox verify
```

Every demo surface is marked `ILLUSTRATIVE PROTOTYPE`.

## Configure a private data root

Copy `config.example` to a private location outside every Git worktree. Replace
`<DATA_ROOT>` and `<INBOX_ADDRESS>` in the private copy only.

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox setup
../competitor-inbox-venv/bin/python -m competitor_inbox backfill --months 12
../competitor-inbox-venv/bin/python -m competitor_inbox build
../competitor-inbox-venv/bin/python -m competitor_inbox open
```

The setup flow stores an IMAP app password in macOS Keychain. It never writes
the password to configuration or logs. An mbox export can be used when IMAP is
unavailable.

## Daily local updates

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox schedule install
../competitor-inbox-venv/bin/python -m competitor_inbox schedule status
```

The scheduler runs at 7:00 AM local time with a 14-day overlap and retains the
prior dashboard after a failed update. The Mac must be on or wake for the job
to run.

Remove it with:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox schedule remove
```

## Safety checks

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox privacy-check
../competitor-inbox-venv/bin/python -m competitor_inbox verify
python3 scripts/privacy_audit.py --repo .
```

Never commit the private config, production data, raw messages, normalized
records, logs, credentials, or generated production dashboard.

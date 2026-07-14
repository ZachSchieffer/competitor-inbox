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

## Prepare Gmail access

Google Workspace permission and inbox-account setup are separate steps:

1. A Workspace admin confirms that organization policy allows the dedicated
   inbox user to turn on 2-Step Verification and create app passwords.
2. Sign into Google as the exact dedicated inbox in `<INBOX_ADDRESS>`. Turn on
   2-Step Verification for that account. Enabling it on an admin or personal
   account does not enable it for the dedicated inbox.
3. While still signed into that inbox account, open its Google Account security
   settings, select **App passwords**, and create one named `Competitor Inbox`.
   Google shows the 16-character app password once. Copy those 16 characters
   without spaces. Do not use the account's normal Google password.

If **App passwords** does not appear after 2-Step Verification is active, treat
it as unavailable for that account. A Workspace admin may need to change
organization policy. The current release does not support Gmail OAuth or IMAP
XOAUTH2. Use the mbox route below when policy requires OAuth.

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
for action in build verify
do
  ../competitor-inbox-venv/bin/python -m competitor_inbox "$action"
done
```

Every demo surface is marked `ILLUSTRATIVE PROTOTYPE`.

## Configure a private data root

Copy `config.example` to a private location outside every Git worktree. Replace
`<DATA_ROOT>` and `<INBOX_ADDRESS>` in the private copy only.

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox setup
```

For Gmail, press Return to accept `imap`, enter the exact dedicated inbox
address, and wait for the hidden Keychain prompts. Setup invokes this command
shape with `-w` last, so the secret never enters the command arguments:

```bash
security add-generic-password -a "<INBOX_ADDRESS>" -s "competitor-inbox-imap" -l "Competitor Inbox IMAP" -U -w
```

Paste the 16-character Google app password at `password data for new item:` and
press Return. Paste the same value again at `retype password:` if macOS asks.
The cursor does not move and no characters appear while you paste. That hidden
behavior is expected. Never add the password after `-w`, and never put it in
`config.toml`, shell history, logs, or chat.

Setup stores the password in macOS Keychain under service
`competitor-inbox-imap`. It retrieves the value without printing it. Confirm
the setup before backfilling:

```bash
../competitor-inbox-venv/bin/python -m competitor_inbox doctor
../competitor-inbox-venv/bin/python -m competitor_inbox backfill --months 12
for action in build open
do
  ../competitor-inbox-venv/bin/python -m competitor_inbox "$action"
done
```

### Mbox fallback

If app passwords are unavailable, rerun setup, choose `mbox`, and enter the
absolute path to an mbox export stored outside every Git worktree. The current
release can read that export locally without a Google credential. Gmail OAuth
and IMAP XOAUTH2 are deferred in `BACKLOG.md`; a normal Google password is not
a substitute for either method.

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

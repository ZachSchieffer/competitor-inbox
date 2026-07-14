# Agent Instructions

## Privacy boundary

- Keep production data outside every Git worktree.
- Never add a real inbox address, production domain, credential, token,
  recipient, personalized URL, message body, raw email, or production dashboard
  to this repository.
- Do not render email HTML or fetch remote email resources.
- Use synthetic fixtures written from scratch.
- Run `python3 scripts/privacy_audit.py --repo .` before a commit or push.

## Editing and testing

- Preserve unrelated user changes.
- Use the normalized contract in `SCHEMA.md`.
- Keep deterministic classification functional without an AI key.
- Add focused tests for every parser, classification, aggregation, security, or
  scheduling behavior changed.
- Run the cross-foot and package validators before declaring an artifact ready.

## Copy boundary

- Public claims require a frozen evidence manifest.
- Do not claim that inbox activity proves performance or conversions.
- The launch keyword is `INBOX`.
- Final distribution artifacts cannot contain unresolved tokens, unconfirmed
  links, dummy URLs, stale counts, or em dashes.
- Do not add Kit as a release requirement.

## External actions

The repository may be public and launch assets may be staged in approved tools.
Do not publish on LinkedIn, comment, connect, send direct messages, or announce
the resource.


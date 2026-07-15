# Deferred from v1

The first release prioritizes a private, local, auditable workflow. These items
are intentionally deferred:

- Kit newsletter broadcast.
- Durable `voice-and-copy` Codex skill.
- Durable `lead-magnet` Codex skill.
- Hosted software-as-a-service dashboard.
- Cloud ingestion and cloud scheduling.
- Gmail API OAuth and IMAP XOAUTH2.
- Windows scheduler support.
- Browser-based newsletter signup or confirmation automation.
- Milled scraping or ingestion.
- Rendering or publishing raw competitor email HTML.
- Remote image loading and email creative capture.
- Claims about email conversion, revenue, or performance.
- OpenAI runtime adapter.
- Main landing-page integration.
- Video or GIF launch asset.
- Automated LinkedIn posting, commenting, connections, or direct messages.
- GitHub release assets and production ingestion through GitHub Actions.

## v1.1: private creative rendering

- Ship the full-archive renderer as a resumable command with a progress ledger.
- Add the optional local Messaging Library thumbnail gallery only after every
  configured brand has 3 to 5 privacy-safe source messages.
- Keep the launch table view as the default until the renderer passes the full
  privacy, redaction, offline-runtime, and fresh-install test suite.

The launch sample exposed an honest source-coverage limit: LMNT had no safe
non-personalized render candidate, and Four Sigmatic had only 1 safe candidate.
The v1.0.3 launch therefore keeps the sanitized table view.

Each backlog item requires a separate privacy and product review before it can
enter scope.

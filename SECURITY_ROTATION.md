# Credential rotation required

Real-looking credentials were previously stored in the local repository configuration. Removing or ignoring `.env` does not remove values from existing Git history, clones, backups, logs, or screenshots. Treat every previously committed or shared credential as compromised.

Rotate these credential types before using the bot with a demo or live account:

- Django signing secret
- PostgreSQL/database password
- Gmail or other IMAP app password
- Telegram bot token
- webhook tokens and HMAC secrets
- broker credential encryption key, if it was exposed or reused
- MT5/broker password and any broker/API credentials
- tunnel or remote-access tokens
- any other application, notification, or integration token found during review

After rotation:

1. Put new values only in the untracked `.env` or an OS secret store.
2. Generate a dedicated `BROKER_CREDS_KEY`; never reuse `DJANGO_SECRET_KEY`.
3. Re-save broker passwords so they are encrypted with the new broker key.
4. Revoke old tokens at each provider and verify they no longer work.
5. Check deployment logs and backups for accidental disclosure.

Git history sanitization is intentionally outside this hardening pass. Perform it separately with a reviewed backup and coordinate replacement clones for every collaborator. History rewriting alone does not revoke a credential; rotation is mandatory.

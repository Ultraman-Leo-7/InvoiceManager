# Privacy

InvoiceManager is designed as a **local-first** desktop tool for personal electronic invoices.

## What stays local by default

Without explicitly using a network feature, the following data stays in the folder where InvoiceManager runs:

- Electronic invoice PDF files
- Parsed invoice information
- Purchase records
- Manual confirmation state
- Notes
- Program settings
- The local SQLite database `.invoice_manager.db`
- Local safety snapshots in `.invoice_manager_backups/`

InvoiceManager does not include analytics, advertising SDKs, telemetry, or an InvoiceManager account/cloud service.

## Network access

Network access happens only when the user uses a feature that needs it.

### QQ Mail / JD invoice retrieval

May connect to:

- QQ Mail IMAP (`imap.qq.com`) to read matching invoice emails
- JD-hosted PDF download URLs extracted from those emails

### Nutstore backup / restore

If the user configures Nutstore backup, InvoiceManager connects to Nutstore through standard WebDAV and uploads a **SQLite data snapshot**.

The Nutstore backup contains program data such as:

- Purchase records
- Manual confirmation state
- Notes
- Parsed invoice cache
- Non-secret settings

The Nutstore backup does **not** upload the invoice PDF files themselves.

Before uploading, InvoiceManager creates a transactionally consistent SQLite snapshot and removes these device-bound secrets from the cloud copy:

- QQ Mail authorization code
- Nutstore WebDAV application password

Therefore, after restoring a cloud backup on another computer, those secrets must be entered again.

The Nutstore account email can remain in the portable backup because it is configuration data rather than an authentication secret.

### Manual update check

InvoiceManager does **not** check for program updates at startup.

Only when the user clicks **Check for updates / 检查更新** does the program contact the public GitHub Releases API for this repository. If the user accepts an update, the release executable and `SHA256SUMS.txt` are downloaded from GitHub and verified locally before replacement.

## Local safety snapshots

Before destructive changes to existing purchase records (editing, deleting, or clearing all records), InvoiceManager creates a local SQLite backup first.

If that safety backup cannot be created, the destructive action is cancelled.

These local backup files may contain the same personal data as the main database and should therefore be treated as private files.

## Saved authorization/application passwords

### QQ Mail authorization code

The QQ Mail integration uses a QQ Mail authorization code, not the normal QQ login password.

### Nutstore application password

The Nutstore integration uses a third-party/WebDAV application password, not the normal Nutstore login password.

On Windows, both values are encrypted with Windows DPAPI before being stored in the local SQLite database. The UI does not display the saved plaintext value again.

A DPAPI-protected value is normally tied to the Windows user context that encrypted it. Moving the database to another Windows account or computer may therefore require entering the corresponding password again.

## GitHub and bug reports

The public GitHub repository does not need or expect users to upload real invoices or their local database.

When reporting a problem, do **not** publish:

- QQ Mail authorization codes
- Nutstore application passwords
- Real invoice PDF files unless all sensitive information has intentionally been removed
- Taxpayer identification numbers
- Personal names, addresses, phone numbers, email addresses, or bank information
- JD order numbers or other account-specific identifiers unless anonymized
- `.invoice_manager.db`
- Files from `.invoice_manager_backups/`

Please redact screenshots before attaching them to a public Issue.

## Source code and releases

The source code is public so users can inspect how local data and network access are handled.

Windows releases are built by GitHub Actions. Release assets include `SHA256SUMS.txt` so downloaded executables can be verified for integrity.

## Scope

This document describes the current open-source InvoiceManager behavior. Any future new network service, telemetry, cloud synchronization, or sensitive-data handling should update this document together with the corresponding code change.

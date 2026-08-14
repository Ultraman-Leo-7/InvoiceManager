# Privacy

InvoiceManager is designed as a **local-first** desktop tool for personal electronic invoices.

## What stays local

By default, the following data is stored only in the folder where the program is running:

- Electronic invoice PDF files
- Parsed invoice information
- Purchase records
- Manual confirmation state
- Notes
- Program settings
- The local SQLite database `.invoice_manager.db`

InvoiceManager does not include analytics, advertising SDKs, telemetry, or an InvoiceManager cloud account system.

## Network access

InvoiceManager only needs network access for features that explicitly retrieve invoices from an external service.

The current QQ Mail / JD invoice workflow may connect to:

- QQ Mail IMAP (`imap.qq.com`) to read matching invoice emails
- JD-hosted invoice download URLs extracted from those emails

Normal local PDF scanning, purchase matching, searching, sorting, manual confirmation, notes, and Excel export do not require an InvoiceManager server.

## QQ Mail authorization code

The QQ Mail integration uses a QQ Mail authorization code, not the user's normal QQ login password.

On Windows:

- The authorization code is encrypted with Windows DPAPI before being stored in the local SQLite database.
- The saved authorization code is not displayed again in plaintext in the settings window.
- Leaving the authorization-code input empty keeps the previously saved encrypted value.
- The user can explicitly clear the saved authorization code from the settings interface.

A DPAPI-protected value is normally tied to the Windows user context that encrypted it. Moving the database to another Windows account or computer may therefore require entering the authorization code again.

## GitHub and bug reports

The public GitHub repository does not need or expect users to upload real invoices or their local database.

When reporting a problem, do **not** publish:

- QQ Mail authorization codes
- Real invoice PDF files unless you have intentionally removed all sensitive information
- Taxpayer identification numbers
- Personal names, addresses, phone numbers, email addresses, or bank information
- JD order numbers or other account-specific identifiers unless they have been anonymized
- `.invoice_manager.db`

Please redact screenshots before attaching them to a public Issue.

## Source code and releases

The source code is public so users can inspect how local data and network access are handled.

Windows releases are built by GitHub Actions. Release assets include a `SHA256SUMS.txt` file so downloaded executables can be verified for integrity.

## Scope

This document describes the behavior of the current open-source InvoiceManager project. If future versions introduce additional network services, cloud synchronization, telemetry, or other data processing, this document should be updated together with the corresponding code change.

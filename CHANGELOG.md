# Changelog

All notable changes to InvoiceManager will be documented here.

## v5.2.12

### Fixed

- Fixed the Windows in-app updater occasionally launching the replacement onefile executable with inherited PyInstaller `_PYI_*` state, which could cause `Failed to load Python DLL ... _MEI...\python312.dll` immediately after updating
- The updater now waits for the current application process to exit, gives the onefile bootloader time to clean up, and launches the replacement with `PYINSTALLER_RESET_ENVIRONMENT=1` plus cleared inherited `_PYI_*` variables

### Improved

- Centralized the application version in `version.py` so future release version bumps no longer require editing the main GUI source

## v5.2.11

### Improved

- Reworked Settings into a sidebar-based layout with four categories: General, Mail & Invoices, Backup & Restore, and Updates & About
- Moved all Nutstore WebDAV configuration and recovery controls into Settings instead of exposing backup configuration as a separate top-level command
- Moved update checking into Settings → Updates & About
- Reduced the main “更多” menu to one-click low-frequency actions only: Settings, manual folder refresh, and Excel export
- Added a clickable GitHub project address and a friendly Star prompt in the Updates & About page
- Preserved manual-only update behavior: InvoiceManager still does not check for updates automatically at startup

## v5.2.10

### Added

- Manual purchase/invoice association overrides for correcting automatic price-match misalignment
- Manual association can be cleared at any time to return a component to automatic price matching
- Manual overrides are persisted in SQLite, included in normal backup/restore, and have an audit trail

### Improved

- Simplified the main command area: only the frequent JD-invoice fetch remains directly visible; refresh, Excel export, backup/restore, update check and settings are grouped under a compact “更多” menu
- Invoice search scopes now follow the fields actually visible in the invoice table instead of exposing hidden fields
- Manual associations are visibly marked in both invoice and purchase tables
- Manual-association changes create a local safety snapshot before modifying state and trigger configured cloud backup afterward

## v5.2.9

> Historical note: early automated releases `v5.2.1` through `v5.2.8` used the GitHub Actions run number as the patch component. Starting with `v5.2.9`, versions are assigned deliberately and will remain monotonic.

### Added

- InvoiceManager branding and renamed public repository
- Windows GUI-based invoice management
- QQ Mail integration for downloading JD electronic invoices
- Optional mailbox start time down to the minute
- Saved QQ Mail authorization code with Windows DPAPI protection
- Purchase record management
- Append-only SQLite purchase audit trail for insert/update/delete history and recovery diagnostics
- Purchase-record total amount display (item prices + shipping fees)
- Separate product-price and shipping-fee invoice matching
- Price-based one-to-one purchase/invoice matching
- Automatic invoice-folder change detection every 2 seconds
- Manual folder refresh
- Full-field and field-specific invoice search
- Clickable table-header sorting
- Ctrl/Shift multi-select for invoice and purchase tables
- Manual invoice confirmation and notes
- Batch deletion of PDF invoices from both disk and UI
- SQLite persistence for parsed data and manual state
- Local SQLite safety snapshots before editing/deleting/clearing existing purchase records
- Destructive purchase operations are cancelled if the safety snapshot cannot be created
- Nutstore WebDAV backup configuration using account email + third-party application password
- Optional automatic cloud backup after meaningful data changes
- Manual "backup now" action
- Restore from latest Nutstore backup or a historical timestamped backup
- SQLite integrity validation before cloud restore
- Automatic local safety snapshot before cloud restore replaces the current database
- Portable cloud snapshots strip QQ Mail and Nutstore application-password secrets
- Manual in-app update check; no automatic startup update check
- Verified update downloads using the release `SHA256SUMS.txt`
- Windows self-replacement and relaunch after a verified update download
- Excel export for invoices and purchase records
- Windows x64 release builds with GitHub Actions
- Stable release asset name: `InvoiceManager-Windows-x64.exe`
- SHA256 checksum file published with Windows releases
- InvoiceManager application icon used by Windows builds
- Unit tests for purchase matching and QQ/JD helper logic
- Authorization-code protection round-trip test
- Backup/snapshot/restore tests
- Purchase audit trail tests
- Version parsing tests for the updater
- Release build gate: syntax check, import check and pytest must pass before packaging
- CI on both `main` pushes and Pull Requests
- Explicit release workflow with a user-supplied version tag
- Dependabot for Python and GitHub Actions dependencies
- Public Bug Issue template
- Public Feature Request template
- Pull Request template with documentation/privacy/test checklists
- `CONTRIBUTING.md`
- `SECURITY.md`
- `PRIVACY.md` describing local storage, network access and secret handling
- `SUPPORT.md`
- `docs/TROUBLESHOOTING.md`
- `docs/BACKUP.md`
- `docs/RELEASE.md` documenting the repeatable release process
- Privacy-safe `.gitignore`
- MIT License

### Improved

- Excel is now an export format rather than the primary state store
- Manual confirmation and notes survive PDF refreshes
- Purchase matching results are visible from both invoice and purchase views
- Purchase data has multiple recovery layers: SQLite transaction storage, append-only audit history, local safety snapshots, and optional Nutstore history backups
- Cloud backups are created from SQLite Backup API snapshots rather than directly copying a live database file
- Cloud restore validates the downloaded SQLite database before replacing local data
- Release naming and documentation are more suitable for public users
- README contains both a beginner quick-start section and a detailed feature/implementation reference
- Release files can be verified using `SHA256SUMS.txt`
- Normal code pushes no longer create a new GitHub Release; CI and release publication are separated to avoid cluttering the Releases page
- Release numbering no longer uses `GITHUB_RUN_NUMBER`; versions now advance simply, e.g. `v5.2.8 → v5.2.9 → v5.2.10`

### Known limitations

- PDF parsing currently expects readable text layers; InvoiceManager is not a general OCR engine
- QQ Mail integration currently scans only the INBOX folder
- JD Mail parsing depends on the current sender/subject/HTML structure
- Purchase matching currently uses price only; equal-price items may require manual review
- Nutstore backup currently covers application data, not the invoice PDF files themselves
- Cloud-restored device-bound application passwords must be entered again
- Windows releases are not code-signed yet

## v5.2.1 ~ v5.2.8 (early automated releases)

- These tags were generated automatically while the GitHub Actions packaging workflow was being validated.
- The patch number reflected the workflow run count, not a deliberately managed sequence of product patch releases.
- `v5.2.8` was the final release created under that automatic numbering scheme.

## v5.1

- Introduced the GUI-based invoice management direction
- Added PDF invoice parsing and invoice table management
- Began moving long-term state away from repeatedly rebuilt Excel files

## Earlier versions

- Added the core Chinese electronic invoice PDF extraction functionality
- Supported folder-to-Excel synchronization in the original script-oriented workflow

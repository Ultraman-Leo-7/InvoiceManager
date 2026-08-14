# Changelog

All notable changes to InvoiceManager will be documented here.

## v5.2.9 (in development)

> Historical note: early automated releases `v5.2.1` through `v5.2.8` used the GitHub Actions run number as the patch component. Starting with `v5.2.9`, versions are assigned deliberately and will remain monotonic.

### Added

- InvoiceManager branding and renamed public repository
- Windows GUI-based invoice management
- QQ Mail integration for downloading JD electronic invoices
- Optional mailbox start time down to the minute
- Saved QQ Mail authorization code with Windows DPAPI protection
- Purchase record management
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
- Excel export for invoices and purchase records
- Windows x64 release builds with GitHub Actions
- Stable release asset name: `InvoiceManager-Windows-x64.exe`
- SHA256 checksum file published with Windows releases
- InvoiceManager application icon used by Windows builds
- Unit tests for purchase matching and QQ/JD helper logic
- Authorization-code protection round-trip test
- Release build gate: syntax check, import check and pytest must pass before packaging
- CI on both `main` pushes and Pull Requests
- Explicit release workflow with a user-supplied version tag
- Dependabot for Python and GitHub Actions dependencies
- Public Bug Issue template
- Public Feature Request template
- Pull Request template with documentation/privacy/test checklists
- `CONTRIBUTING.md`
- `SECURITY.md`
- `PRIVACY.md` describing local storage, network access and authorization-code handling
- `SUPPORT.md`
- `docs/TROUBLESHOOTING.md`
- `docs/RELEASE.md` documenting the repeatable release process
- Privacy-safe `.gitignore`
- MIT License

### Improved

- Excel is now an export format rather than the primary state store
- Manual confirmation and notes survive PDF refreshes
- Purchase matching results are visible from both invoice and purchase views
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

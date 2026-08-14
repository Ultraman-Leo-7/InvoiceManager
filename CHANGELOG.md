# Changelog

All notable changes to InvoiceManager will be documented here.

## v5.2 (in development)

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
- Automatic Windows x64 builds with GitHub Actions
- Stable release asset name: `InvoiceManager-Windows-x64.exe`
- SHA256 checksum file published with Windows releases
- InvoiceManager application icon used by Windows builds
- Unit tests for purchase matching and QQ/JD helper logic
- Release build gate: syntax check, import check and pytest must pass before packaging
- Pull Request CI workflow on Windows
- Dependabot for Python and GitHub Actions dependencies
- Public Bug Issue template
- Public Feature Request template
- `CONTRIBUTING.md`
- `SECURITY.md`
- Privacy-safe `.gitignore`

### Improved

- Excel is now an export format rather than the primary state store
- Manual confirmation and notes survive PDF refreshes
- Purchase matching results are visible from both invoice and purchase views
- Release naming and documentation are more suitable for public users
- README now contains both a beginner quick-start section and a detailed feature/implementation reference
- Release files can be verified using `SHA256SUMS.txt`

### Known limitations

- PDF parsing currently expects readable text layers; InvoiceManager is not a general OCR engine
- QQ Mail integration currently scans only the INBOX folder
- JD Mail parsing depends on the current sender/subject/HTML structure
- Purchase matching currently uses price only; equal-price items may require manual review
- Windows releases are not code-signed yet

## v5.1

- Introduced the GUI-based invoice management direction
- Added PDF invoice parsing and invoice table management
- Began moving long-term state away from repeatedly rebuilt Excel files

## Earlier versions

- Added the core Chinese electronic invoice PDF extraction functionality
- Supported folder-to-Excel synchronization in the original script-oriented workflow

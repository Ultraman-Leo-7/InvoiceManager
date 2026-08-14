# Release Process

InvoiceManager separates normal CI from release publication.

A normal push to `main` should **not** create a new GitHub Release. This keeps the Releases page readable and avoids publishing a new executable for every small documentation or code change.

## Before publishing

1. Make sure the latest `CI` workflow on `main` is green.
2. Confirm user-visible behavior is documented in `README.md`.
3. Update `CHANGELOG.md` for meaningful changes.
4. If privacy or security behavior changed, update `PRIVACY.md` / `SECURITY.md` as well.

## Publish from GitHub Actions

Open:

```text
GitHub -> Actions -> Build Windows Release -> Run workflow
```

The workflow asks for:

- `version` — for example `v5.2.0-beta.1` or `v5.2.0`
- `prerelease` — enable for test/beta builds; disable for a normal stable release

The version must follow the form:

```text
vMAJOR.MINOR.PATCH
```

An optional suffix is allowed, for example:

```text
v5.2.0-beta.1
```

## What the workflow does

The release job runs on a clean Windows GitHub runner and performs the following steps:

```text
Checkout source
    ↓
Install dependencies
    ↓
Python syntax check
    ↓
Import check
    ↓
pytest unit tests
    ↓
Generate Windows application icon
    ↓
PyInstaller one-file Windows x64 build
    ↓
Generate SHA256 checksum
    ↓
Upload Actions artifact
    ↓
Create GitHub Release / Pre-release
```

The published files are:

```text
InvoiceManager-Windows-x64.exe
SHA256SUMS.txt
```

## Why release publication is explicit

Earlier development builds created a pre-release automatically on every relevant push. That was useful while validating the first packaging workflow, but it quickly produced many nearly identical releases.

For a public project, the current model is cleaner:

- **CI is automatic** on code changes.
- **Publishing is explicit** when a version is actually worth giving to users.

## Versioning guideline

Until the project reaches a stable public baseline, pre-release suffixes are recommended:

```text
v5.2.0-beta.1
v5.2.0-beta.2
```

When a tested build is considered ready for ordinary users, publish without the pre-release flag, for example:

```text
v5.2.0
```

Avoid using the GitHub Actions run number as the long-term product version.

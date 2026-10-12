# Release Process

InvoiceManager separates normal CI from release publication.

Normal code changes only run CI. Updating `APP_VERSION` in `version.py` and merging that change into `main` automatically runs `Build Windows Release` to test, build and publish that version. PRs do not publish releases.

## Before publishing

1. Make sure the latest `CI` workflow on `main` is green.
2. Confirm user-visible behavior is documented in `README.md`.
3. Increment `APP_VERSION` in `version.py` and move the changes from `Unreleased` into a matching `## vMAJOR.MINOR.PATCH` section in `CHANGELOG.md`.
4. If privacy or security behavior changed, update `PRIVACY.md` / `SECURITY.md` as well.

## Automatic publication and manual retry

Merge the documented version bump into `main`. The release job independently runs the Windows tests before building and publishing the executable. A failed test or build stops publication.

To retry an unpublished version manually, select `main` and open:

Open:

```text
GitHub -> Actions -> Build Windows Release -> Run workflow
```

Leave the version input blank to read `version.py`, or enter the exact matching tag, for example:

```text
v5.2.14
```

The project uses a deliberately simple version format:

```text
vMAJOR.MINOR.PATCH
```

No beta / alpha suffix is required.

The workflow rejects a mismatched version or missing CHANGELOG entry. Existing releases are never overwritten; use a new application version for new builds. Release notes are taken from the matching CHANGELOG section.

## Historical note about v5.2.1 ~ v5.2.8

The early GitHub Actions workflow automatically generated tags such as `v5.2.1` through `v5.2.8` by using the workflow run number as the last part of the version.

Those versions are already public, so the project will not go backwards. The next release should simply continue with:

```text
v5.2.9
```

After that, small fixes can continue as `v5.2.10`, `v5.2.11`, and so on. A larger feature update can move to `v5.3.0`.

## What the workflow does

The release job runs on a clean Windows GitHub runner and performs:

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
Create GitHub Release
```

Published files:

```text
InvoiceManager-Windows-x64.exe
SHA256SUMS.txt
```

## Simple versioning rule

Keep it simple:

- Small fixes: increment the last number, e.g. `v5.2.9` -> `v5.2.10`.
- Larger feature update: increment the middle number, e.g. `v5.2.10` -> `v5.3.0`.
- Major redesign or incompatible change: increment the first number.

Do not use the GitHub Actions run number as the product version again.

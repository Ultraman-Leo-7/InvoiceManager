# Release Process

InvoiceManager separates normal CI from release publication.

Normal code changes only run CI. A new GitHub Release is created only when we explicitly decide to publish a new version.

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

Enter the next version number, for example:

```text
v5.2.9
```

The project uses a deliberately simple version format:

```text
vMAJOR.MINOR.PATCH
```

No beta / alpha suffix is required.

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

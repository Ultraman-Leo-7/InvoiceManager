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

- `version` — for example `v5.2.9-beta.1` or `v5.2.9`
- `prerelease` — enable for test/beta builds; disable for a normal stable release

The version must follow the form:

```text
vMAJOR.MINOR.PATCH
```

An optional suffix is allowed, for example:

```text
v5.2.9-beta.1
```

## Historical note about v5.2.1 ~ v5.2.8

The early GitHub Actions workflow automatically generated tags like:

```text
v5.2.1
v5.2.2
...
v5.2.8
```

At that time, the last number came directly from `GITHUB_RUN_NUMBER`. In other words, `v5.2.8` mainly meant "the eighth release-workflow run", not that the product had intentionally gone through eight carefully managed patch releases.

Those tags are already public, so the project will **not go backwards** to `v5.2.0`. From now on version numbers remain monotonic.

Therefore the next canonical test release should be:

```text
v5.2.9-beta.1
```

and, after testing, the corresponding stable release can be:

```text
v5.2.9
```

A later feature release can move to `v5.3.0` when appropriate.

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

For test builds, use a suffix while keeping the numeric version higher than the latest published tag:

```text
v5.2.9-beta.1
v5.2.9-beta.2
```

When the tested build is ready for ordinary users, publish:

```text
v5.2.9
```

For a larger feature update, increment the minor version, for example:

```text
v5.3.0-beta.1
v5.3.0
```

Do not use the GitHub Actions run number as the long-term product version again.

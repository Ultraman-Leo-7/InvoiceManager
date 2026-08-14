# Troubleshooting

This page collects common InvoiceManager problems and the expected behavior behind them.

## Windows says “Unknown publisher” or shows SmartScreen

Current Windows builds are not commercially code-signed yet, so Windows may show an unknown-publisher or SmartScreen warning even when the file came from the official GitHub Release.

Recommended checks:

1. Download only from this repository's official **Releases** page.
2. Compare the executable's SHA256 value with `SHA256SUMS.txt` from the same Release.
3. Do not use executables re-uploaded by unrelated websites or users.

## Where should I put the exe?

InvoiceManager treats the folder containing the executable as the active invoice folder.

Recommended structure:

```text
MyInvoices/
├─ InvoiceManager-Windows-x64.exe
├─ invoice-1.pdf
├─ invoice-2.pdf
└─ ...
```

If you move the exe into another folder, that new folder becomes the active invoice folder.

## A new PDF does not appear immediately

InvoiceManager checks the current folder for PDF changes about every **2 seconds**.

You can also click **刷新发票文件夹** to force an immediate scan.

If the PDF still does not appear, check that:

- The file extension is `.pdf`.
- The file has finished copying/downloading.
- The program is running from the folder you think it is.

## A PDF appears but some invoice fields are missing

InvoiceManager currently parses PDF text layers; it is not a general OCR engine.

If a PDF is essentially a scanned image without readable text, extraction may be incomplete or fail. Check the **提取状态** column and review the invoice manually.

## The QQ Mail authorization-code box is blank after saving

This is expected.

InvoiceManager intentionally does **not** display a previously saved authorization code in plaintext.

If the settings page says an authorization code is already saved, leaving the box empty means “keep using the existing saved value.”

On Windows, the saved value is protected with DPAPI before being written to the local SQLite database.

## I moved the database to another PC / Windows user and QQ Mail no longer works

A DPAPI-protected authorization code is normally tied to the Windows user context that encrypted it.

After moving `.invoice_manager.db` to another Windows account or computer, enter the QQ Mail authorization code again.

## QQ Mail cannot find an older JD invoice email

The current integration scans the QQ Mail **INBOX** only.

If an invoice email was moved into another folder, archived outside INBOX, deleted, or sent to another mailbox, the current version may not find it.

For a first full import, use the “scan all history” option. For later runs, “from last successful fetch” is usually more efficient.

## What time formats are accepted for QQ Mail fetching?

Accepted examples:

```text
2026-08-01
```

means `2026-08-01 00:00`.

```text
2026-08-01 13:25
```

means that exact local date and minute.

## JD invoice downloading finds email links but the PDF fails to download

JD controls the final download endpoint. If JD changes its email or download-link behavior, the current parser may need an update.

When reporting this problem, do not post the real invoice URL publicly if it contains account-specific information. Provide the error text and a redacted description instead.

## A purchase has shipping, but InvoiceManager does not look for item price + shipping as one invoice

This is intentional.

For a purchase such as:

```text
Item: 74.00
Shipping: 6.00
```

InvoiceManager looks for two separate invoice amounts:

```text
74.00
6.00
```

It does **not** look for one `80.00` invoice.

## Two purchases have exactly the same price

Current matching uses price only.

Each invoice can be used at most once, but when several purchases and invoices have the same price, InvoiceManager cannot know the real business relationship from price alone. It pairs them deterministically and expects manual review when necessary.

## Deleting an invoice removed the PDF from disk

This is intentional.

**删除选中发票** is a real file-deletion operation, not merely “hide this row.” The program asks for confirmation first, but it currently has no built-in undo/recycle-bin restore function.

## Excel export fails because the file is open

If `发票汇总.xlsx` is open in Excel or WPS, Windows may lock the file.

Close the workbook, then export again.

## My manual confirmation or note disappeared

Under normal operation, manual confirmation and notes are stored in SQLite and should survive normal folder refreshes and PDF re-parsing.

If state disappears unexpectedly, please open a Bug Issue and include:

- InvoiceManager version
- What happened immediately before the state disappeared
- Whether the PDF was replaced, renamed, edited, deleted and re-added, or moved between folders

Do not upload the real `.invoice_manager.db` publicly if it contains private information.

## Where is InvoiceManager's local database?

The database file is:

```text
.invoice_manager.db
```

in the same invoice folder as the executable.

On Windows, InvoiceManager attempts to mark it as a hidden file so it does not clutter the folder.

Deleting this database removes saved program state such as purchase records, notes, manual confirmations and stored settings. Back it up before intentionally removing it.

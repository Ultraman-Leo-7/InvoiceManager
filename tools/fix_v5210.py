from pathlib import Path

p = Path("app.py")
text = p.read_text(encoding="utf-8")
start = text.index('            if actual is not None and int(round(actual * 100)) != int(round(expected * 100)):\n')
end = text.index('            if not self._create_local_safety_backup_or_block("before-manual-invoice-match"):\n', start)
replacement = '''            if actual is not None and int(round(actual * 100)) != int(round(expected * 100)):
                if not messagebox.askyesno(
                    APP_TITLE,
                    f"金额不同，仍要手动关联吗？\\n\\n购买记录“{kind}”：¥{expected:.2f}\\n"
                    f"所选发票：¥{actual:.2f}\\n{filename}",
                    parent=win,
                ):
                    return
'''
text = text[:start] + replacement + text[end:]
p.write_text(text, encoding="utf-8")
print("fixed v5.2.10 manual-match confirmation string")

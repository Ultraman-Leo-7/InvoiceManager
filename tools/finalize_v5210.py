from pathlib import Path

p = Path("README.md")
text = p.read_text(encoding="utf-8")
old = '''## 3. 设置 QQ 邮箱并获取京东发票

点击：

**设置**
'''
new = '''## 3. 设置 QQ 邮箱并获取京东发票

入口位于：

**更多 → 设置**
'''
if old not in text:
    raise RuntimeError("README settings entry pattern not found")
text = text.replace(old, new, 1)
p.write_text(text, encoding="utf-8")

p = Path("CHANGELOG.md")
text = p.read_text(encoding="utf-8")
text = text.replace("## v5.2.10 (in development)", "## v5.2.10", 1)
p.write_text(text, encoding="utf-8")

print("v5.2.10 docs finalized")

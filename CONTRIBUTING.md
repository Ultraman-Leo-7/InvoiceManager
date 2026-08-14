# Contributing to InvoiceManager

感谢你愿意帮助改进 InvoiceManager。

InvoiceManager 目前主要面向 Windows 用户，用于本地电子发票管理、QQ 邮箱京东发票获取、购买记录与发票核对。

## 提交 Bug 前

请先确认：

1. 使用的是最新 GitHub Pre-release；
2. 问题可以稳定复现；
3. 已经阅读 README 中的“当前限制”；
4. Issue 中不会包含真实 QQ 邮箱授权码、身份证号、税号、地址、电话或其他敏感发票信息。

推荐使用仓库自带的 **Bug 反馈** Issue 表单。

## 功能建议

欢迎提交功能建议。请尽量说明：

- 当前使用场景；
- 现在为什么不方便；
- 期望的交互方式；
- 是否涉及新的邮箱、平台或发票格式。

## 本地开发

建议环境：

```text
Windows 10 / 11 x64
Python 3.12
```

安装开发依赖：

```bash
python -m pip install -r requirements-dev.txt
```

直接运行 GUI：

```bash
python app.py
```

运行语法检查：

```bash
python -m py_compile app.py invoice_extract.py jd_qq.py purchase_tracker.py tools/generate_icon.py
```

本地打包：

```text
双击 build.bat
```

产物：

```text
dist/InvoiceManager-Windows-x64.exe
```

## 项目模块

```text
app.py                  GUI、SQLite、文件夹刷新、搜索、排序、删除、导出
invoice_extract.py      PDF 发票解析
jd_qq.py                QQ 邮箱 -> 京东电子发票
purchase_tracker.py     购买记录与价格匹配
tools/generate_icon.py  Windows 图标生成
```

## 修改原则

### 用户数据优先

发票属于敏感数据。任何修改都不应把以下内容上传到网络：

- PDF 发票；
- SQLite 数据库；
- QQ 邮箱授权码；
- 用户购买记录；
- 包含真实个人信息的调试日志。

### 人工数据不能被自动刷新覆盖

以下数据属于人工状态：

- 人工确认；
- 备注；
- 购买记录；
- 用户设置。

修改 PDF 同步逻辑时必须保证这些数据不会因为自动重新解析而意外丢失。

### README 与 CHANGELOG 同步

如果修改了用户可见行为：

1. 更新 `README.md`；
2. 在 `CHANGELOG.md` 的开发版本下补充变化；
3. 如果改变了构建或安全行为，也同步修改相关文档。

## Pull Request

PR 建议保持单一目的，并说明：

- 修改了什么；
- 为什么修改；
- 如何验证；
- 是否改变数据库结构、匹配逻辑、邮箱规则或删除行为。

涉及删除文件、数据库迁移、凭据存储等高风险修改时，请在 PR 描述中明确指出。

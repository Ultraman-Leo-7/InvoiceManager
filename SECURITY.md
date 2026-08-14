# Security Policy

InvoiceManager 会处理电子发票、购买记录和 QQ 邮箱授权信息，因此安全与隐私问题优先级较高。

## 支持范围

当前仍处于 Pre-release 阶段。安全修复优先应用到最新测试版本。

## 报告安全问题

如果问题涉及以下内容，请 **不要在公开 Issue 中粘贴敏感数据**：

- QQ 邮箱 16 位授权码；
- 真实发票 PDF；
- 姓名、税号、身份证号、地址、电话；
- 邮箱、订单号或其他可识别个人身份的信息；
- `.invoice_manager.db` 数据库文件。

如果仓库的 GitHub **Security** 页面提供 Private vulnerability reporting，请优先使用私密安全报告渠道。

如果只是普通功能 Bug，请使用仓库的 Bug Issue 模板，并将所有敏感信息打码。

## 当前凭据设计

QQ 邮箱授权码：

- 不写入源代码；
- 不上传 GitHub；
- Windows 下使用 DPAPI 加密后存储在本地 SQLite；
- 设置界面不会重新显示已经保存的授权码明文；
- 用户可以在设置中清除已保存授权码。

## 本地数据

默认情况下，InvoiceManager 不需要云端数据库。

用户的：

- PDF 发票；
- 购买记录；
- 人工确认；
- 备注；
- 邮箱设置；

都保存在本地发票目录及其 `.invoice_manager.db` 中。

仓库 `.gitignore` 默认忽略 PDF、Excel、SQLite 数据库和常见密钥文件，以降低开发时误提交敏感数据的风险。

## Release 校验

新的 Windows Release 会同时发布：

```text
InvoiceManager-Windows-x64.exe
SHA256SUMS.txt
```

用户可以使用 SHA256 校验下载文件是否与 Release 中公布的哈希一致。

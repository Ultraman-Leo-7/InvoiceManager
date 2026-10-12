"""Readable summaries of mailbox invoice results, without UI or filesystem access."""


def format_mail_fetch_report(result: dict, stats: dict) -> str:
    """Explain file outcomes separately from matching mail and candidate counts."""
    failed = result.get("failed", 0)
    sources = result.get("sources", {})
    lines = [
        f"总计：新下载 {result.get('downloaded', 0)} 份 PDF；"
        f"内容相同跳过 {result.get('skipped', 0)} 份；失败 {failed} 项。",
        f"共识别发票邮件 {result.get('emails', 0)} 封；"
        f"PDF 附件 {result.get('attachments', 0)} 份；下载链接 {result.get('links', 0)} 个。",
        "",
    ]
    source_failures = 0
    for key, name in (("jd", "京东"), ("pinduoduo", "拼多多"), ("alipay", "支付宝发票管家")):
        counts = sources.get(key, {})
        source_failures += counts.get("failed", 0)
        lines.append(
            f"{name}：邮件 {counts.get('emails', 0)} 封；"
            f"新下载 {counts.get('downloaded', 0)} 份，"
            f"重复跳过 {counts.get('skipped', 0)} 份，失败 {counts.get('failed', 0)} 项。"
        )
    unknown_failures = failed - source_failures
    if unknown_failures > 0:
        lines.append(f"未能识别来源的候选邮件：失败 {unknown_failures} 项。")
    pending_duplicates = sum(len(group.get("remove", [])) for group in stats.get("duplicates", []))
    lines.extend([
        "",
        "邮件数不等于 PDF 数：一封邮件可含多份发票，也可能未识别到 PDF。",
        f"当前文件夹共 {stats.get('total', 0)} 个 PDF；待处理重复 {pending_duplicates} 个。",
    ])
    if failed:
        lines.extend([
            "",
            "失败项包含邮件读取失败、整封邮件未识别到支持的 PDF，以及文件下载或保存失败，并不全是文件下载失败。",
            "成功文件已保存；本次未更新“上次成功获取时间”，下次重试会按内容跳过已有 PDF。",
            "",
            "失败详情：",
        ])
        errors = result.get("errors", [])
        lines.extend(f"{index}. {error}" for index, error in enumerate(errors, 1))
        if not errors:
            lines.append("未提供具体错误信息。")
    else:
        lines.extend(["", "获取完成，已更新“上次成功获取时间”。"])
    return "\n".join(lines)

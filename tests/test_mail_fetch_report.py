from copy import deepcopy

from mail_fetch_report import format_mail_fetch_report


def make_result(sources=None, *, unknown_failures=0, errors=None):
    sources = sources or {}
    counters = ("emails", "links", "attachments", "downloaded", "skipped", "failed")
    result = {key: sum(source.get(key, 0) for source in sources.values()) for key in counters}
    result["failed"] += unknown_failures
    result["sources"] = sources
    result["errors"] = errors or []
    return result


def test_mixed_sources_explain_mail_file_and_failure_counts_without_mutation():
    result = make_result({
        "jd": {"emails": 4, "links": 5, "downloaded": 2, "skipped": 3, "failed": 1},
        "pinduoduo": {"emails": 2, "attachments": 2, "downloaded": 1, "skipped": 1},
        "alipay": {"emails": 1, "attachments": 3, "downloaded": 2, "skipped": 1},
    }, errors=["京东邮件未找到发票 PDF 下载链接"])
    stats = {"total": 12, "duplicates": [{"remove": ["copy-a.pdf", "copy-b.pdf"]}]}
    originals = deepcopy((result, stats))

    text = format_mail_fetch_report(result, stats)

    assert "总计：新下载 5 份 PDF；内容相同跳过 5 份；失败 1 项。" in text
    assert "共识别发票邮件 7 封；PDF 附件 5 份；下载链接 5 个。" in text
    assert "京东：邮件 4 封；新下载 2 份，重复跳过 3 份，失败 1 项。" in text
    assert "拼多多：邮件 2 封；新下载 1 份，重复跳过 1 份，失败 0 项。" in text
    assert "支付宝发票管家：邮件 1 封；新下载 2 份，重复跳过 1 份，失败 0 项。" in text
    assert text.index("京东：") < text.index("拼多多：") < text.index("支付宝发票管家：")
    assert "邮件数不等于 PDF 数" in text
    assert "整封邮件未识别到支持的 PDF" in text
    assert "并不全是文件下载失败" in text
    assert "成功文件已保存；本次未更新“上次成功获取时间”" in text
    assert "下次重试会按内容跳过已有 PDF" in text
    assert "当前文件夹共 12 个 PDF；待处理重复 2 个。" in text
    assert "1. 京东邮件未找到发票 PDF 下载链接" in text
    assert (result, stats) == originals


def test_platform_with_no_matching_mail_remains_visible_with_zero_counts():
    result = make_result({"jd": {"emails": 1, "links": 1, "downloaded": 1}})

    text = format_mail_fetch_report(result, {"total": 1, "duplicates": []})

    assert "拼多多：邮件 0 封；新下载 0 份，重复跳过 0 份，失败 0 项。" in text
    assert "支付宝发票管家：邮件 0 封；新下载 0 份，重复跳过 0 份，失败 0 项。" in text
    assert "未能识别来源" not in text


def test_unreadable_candidate_failures_are_not_assigned_to_a_platform():
    result = make_result({
        "jd": {"emails": 1, "failed": 1},
        "pinduoduo": {"emails": 1, "attachments": 1, "downloaded": 1},
    }, unknown_failures=2, errors=[
        "京东邮件未找到发票 PDF 下载链接",
        "读取一封候选邮件失败，可稍后重试",
        "候选邮件内容为空，可稍后重试",
    ])

    text = format_mail_fetch_report(result, {"total": 1, "duplicates": []})

    assert "总计：新下载 1 份 PDF；内容相同跳过 0 份；失败 3 项。" in text
    assert "京东：邮件 1 封；新下载 0 份，重复跳过 0 份，失败 1 项。" in text
    assert "拼多多：邮件 1 封；新下载 1 份，重复跳过 0 份，失败 0 项。" in text
    assert "未能识别来源的候选邮件：失败 2 项。" in text
    assert "3. 候选邮件内容为空，可稍后重试" in text


def test_all_success_uses_short_completion_message():
    result = make_result({
        "pinduoduo": {"emails": 1, "attachments": 1, "downloaded": 1},
        "alipay": {"emails": 1, "attachments": 2, "skipped": 2},
    })

    text = format_mail_fetch_report(result, {"total": 3, "duplicates": []})

    assert "总计：新下载 1 份 PDF；内容相同跳过 2 份；失败 0 项。" in text
    assert text.endswith("获取完成，已更新“上次成功获取时间”。")
    assert "失败详情" not in text
    assert "本次未更新" not in text


def test_zero_mail_keeps_all_sources_and_does_not_claim_files_were_downloaded():
    text = format_mail_fetch_report(make_result(), {"total": 6, "duplicates": []})

    assert "总计：新下载 0 份 PDF；内容相同跳过 0 份；失败 0 项。" in text
    assert "共识别发票邮件 0 封；PDF 附件 0 份；下载链接 0 个。" in text
    assert text.count("邮件 0 封；新下载 0 份，重复跳过 0 份，失败 0 项。") == 3
    assert "当前文件夹共 6 个 PDF" in text
    assert "成功文件已保存" not in text


def test_all_error_details_are_included_beyond_five_entries():
    errors = [f"测试候选邮件 {index} 读取失败" for index in range(1, 9)]
    result = make_result(unknown_failures=8, errors=errors)

    text = format_mail_fetch_report(result, {"total": 0, "duplicates": []})

    for index, error in enumerate(errors, 1):
        assert f"{index}. {error}" in text
    assert "未能识别来源的候选邮件：失败 8 项。" in text
    assert "……" not in text

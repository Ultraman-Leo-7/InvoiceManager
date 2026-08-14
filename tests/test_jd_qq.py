from jd_qq import extract_order_number, parse_start_time, safe_filename


def test_extract_order_number_from_jd_subject():
    subject = "您的京东订单【3557465013915946】电子发票已开具"
    assert extract_order_number(subject) == "3557465013915946"


def test_date_only_defaults_to_midnight():
    dt, imap_date = parse_start_time("2026-08-01")
    assert (dt.hour, dt.minute) == (0, 0)
    assert imap_date == "01-Aug-2026"


def test_datetime_keeps_hour_and_minute():
    dt, imap_date = parse_start_time("2026-08-01 13:25")
    assert (dt.hour, dt.minute) == (13, 25)
    assert imap_date == "01-Aug-2026"


def test_safe_filename_removes_windows_invalid_characters():
    result = safe_filename('订单:123/发票*?.pdf')
    assert ":" not in result
    assert "/" not in result
    assert "*" not in result
    assert "?" not in result

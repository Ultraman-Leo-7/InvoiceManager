from self_update import parse_version


def test_parse_plain_release_versions():
    assert parse_version("v5.2.9") == (5, 2, 9)
    assert parse_version("5.3.0") == (5, 3, 0)


def test_reject_beta_or_invalid_versions():
    assert parse_version("v5.2.9-beta.1") is None
    assert parse_version("v5.2") is None
    assert parse_version("hello") is None

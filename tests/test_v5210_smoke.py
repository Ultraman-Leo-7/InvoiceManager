import app


def test_app_version_is_v5210():
    assert app.APP_VERSION == "5.2.10"


def test_visible_search_scope_label_is_present():
    assert "全部显示字段" in app.InvoiceApp._visible_search_scopes.__code__.co_consts

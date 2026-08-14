import inspect

import app


def test_app_version_is_v5211():
    assert app.APP_VERSION == "5.2.11"


def test_visible_search_scope_label_is_present():
    assert "全部显示字段" in app.InvoiceApp._visible_search_scopes.__code__.co_consts


def test_settings_categories_are_present():
    source = inspect.getsource(app.InvoiceApp.open_settings)
    for label in ("通用", "邮箱与发票", "备份与恢复", "更新与关于"):
        assert label in source


def test_project_url_points_to_public_repo():
    assert app.PROJECT_URL == "https://github.com/Ultraman-Leo-7/InvoiceManager"

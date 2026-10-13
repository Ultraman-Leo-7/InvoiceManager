"""Exercise the real result window on Windows, including DPI and window state."""

import os
import sys
import time
import tkinter as tk
from tkinter import ttk

import pytest

import app


@pytest.fixture
def root():
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("Real Tk window requires DISPLAY on Linux; Windows always runs this test")
    window = tk.Tk()
    window.geometry("400x240+0+0")
    window.update()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def pump_until(root, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Tk did not reach the expected window state"


def mouse_click(root, button):
    x, y = button.winfo_width() // 2, button.winfo_height() // 2
    for event in ("<Enter>", "<ButtonPress-1>", "<ButtonRelease-1>"):
        button.event_generate(event, x=x, y=y)
        root.update()


def open_report(root):
    prompts = []
    duplicates = [{"keep": "synthetic.pdf", "remove": ["synthetic-copy.pdf"]}]
    root.queue_duplicate_prompt = lambda groups, force=False: prompts.append(groups)
    message = "\n".join(f"合成获取结果第 {index} 行" for index in range(100))
    app.InvoiceApp.show_mail_fetch_result(root, message, False, duplicates)
    root.update()
    report, = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
    details, = [child for child in descendants(report) if isinstance(child, tk.Text)]
    confirm, = [child for child in descendants(report) if isinstance(child, ttk.Button)]
    return report, details, confirm, prompts, duplicates, message


@pytest.mark.parametrize("scaling", [1.0, 1.5, 2.0])
def test_result_can_minimize_restore_and_confirm_at_small_size(root, scaling):
    root.tk.call("tk", "scaling", scaling)
    interactions = []
    main_button = ttk.Button(root, text="合成主窗操作", command=lambda: interactions.append(True))
    main_button.pack()
    report, details, confirm, prompts, duplicates, message = open_report(root)
    report.geometry("680x420+450+0")
    root.update()

    assert not report.transient()
    assert root.grab_current() is None
    assert confirm.winfo_ismapped()
    assert confirm.winfo_height() >= confirm.winfo_reqheight()
    assert details.winfo_height() > 0
    assert confirm.winfo_rooty() >= details.winfo_rooty() + details.winfo_height()
    assert confirm.winfo_rooty() + confirm.winfo_height() <= report.winfo_rooty() + report.winfo_height()
    assert confirm.winfo_rootx() + confirm.winfo_width() <= report.winfo_rootx() + report.winfo_width()

    report.iconify()
    pump_until(root, lambda: report.state() == "iconic")
    assert prompts == []
    assert root.grab_current() is None
    mouse_click(root, main_button)
    assert interactions == [True]

    report.deiconify()
    pump_until(root, lambda: report.state() == "normal" and report.winfo_viewable())
    assert details.get("1.0", "end-1c") == message
    assert str(details.cget("state")) == "disabled"
    assert prompts == []
    mouse_click(root, confirm)
    assert not report.winfo_exists()
    assert prompts == [duplicates]


@pytest.mark.parametrize("dismiss", ["return", "escape", "close"])
def test_real_result_keyboard_and_window_close_queue_duplicates_once(root, dismiss):
    report, _, _, prompts, duplicates, _ = open_report(root)
    if dismiss == "close":
        report.tk.call(report.protocol("WM_DELETE_WINDOW"))
    else:
        report.focus_force()
        root.update()
        report.event_generate("<Return>" if dismiss == "return" else "<Escape>")
    root.update()
    assert not report.winfo_exists()
    assert prompts == [duplicates]
    assert root.grab_current() is None


def test_closing_main_window_cleans_up_report_without_duplicate_actions(root):
    report, _, _, prompts, _, _ = open_report(root)
    destroyed = []
    report.bind("<Destroy>", lambda event: destroyed.append(event.widget) if event.widget == report else None)
    root.destroy()
    assert destroyed == [report]
    assert prompts == []

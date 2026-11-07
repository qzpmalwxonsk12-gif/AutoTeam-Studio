from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent

from autoteam_studio.core.chat import ChatService
from autoteam_studio.core.types import ControllerSettings
from .test_window import ready


def attach_chat(widget, controller, tmp_path):
    service = ChatService(ControllerSettings(backend="fake"), tmp_path)
    controller.open_chat = service.open
    controller.read_chat = service.read
    controller.chat_turn = lambda identity, text, casual=False: service.send(identity, text, casual=casual)
    controller.cancel_chat = service.cancel
    widget._open_chat(new=True)
    return service


def test_send_hello_in_same_interface_never_starts_build(window, wait, tmp_path):
    widget, controller = window
    attach_chat(widget, controller, tmp_path)
    wait(lambda: not widget.adapter.busy)
    assert widget.tabs.currentIndex() == 1
    widget.prompt.setPlainText("hello what are you both doing?")
    widget.chat_send.click()
    wait(lambda: not widget.adapter.busy, timeout=10)
    assert [m["speaker"] for m in widget.chat_state["messages"]] == ["you", "a", "b"]
    assert len(widget.conversation.bubbles) >= 3
    assert not controller.calls
    assert not widget.run_id


def test_just_chat_toggle_blocks_all_build_entry_points(window, wait, tmp_path):
    widget, controller = window
    attach_chat(widget, controller, tmp_path)
    wait(lambda: not widget.adapter.busy)
    ready(widget, controller)
    widget.casual_toggle.setChecked(True)
    assert widget.chat_panel.maximumWidth() == 600
    assert not widget.start_button.isEnabled()
    widget.start_run()
    widget.prompt.setPlainText("Build a calculator with tests")
    widget.chat_send.click()
    wait(lambda: not widget.adapter.busy, timeout=10)
    assert not widget.chat_state["build_prompt"]
    assert not controller.calls
    assert widget.brief_card.isHidden()


def test_build_request_offers_review_without_starting(window, wait, tmp_path):
    widget, controller = window
    attach_chat(widget, controller, tmp_path)
    wait(lambda: not widget.adapter.busy)
    widget.prompt.setPlainText("Build a calculator with tests")
    widget.chat_send.click()
    wait(lambda: not widget.adapter.busy, timeout=10)
    assert widget.chat_state["build_prompt"]
    assert not widget.brief_card.isHidden()
    assert not controller.calls


def test_theme_and_chat_mode_persist_without_account_changes(window):
    widget, controller = window
    before = widget.values["account_a_home"], widget.values["account_b_home"]
    widget.theme_choice.setCurrentText("Quiet dark")
    widget.casual_toggle.setChecked(True)
    saved = widget.preferences.load()
    assert saved["theme"] == "Quiet dark" and saved["casual_chat"]
    assert (saved["account_a_home"], saved["account_b_home"]) == before


def test_enter_sends_chat_and_shift_enter_is_multiline(window, wait, tmp_path):
    widget, controller = window
    attach_chat(widget, controller, tmp_path)
    wait(lambda: not widget.adapter.busy)
    widget.prompt.setPlainText("hello")
    widget.prompt.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier))
    assert "\n" in widget.prompt.toPlainText()
    widget.prompt.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    wait(lambda: not widget.adapter.busy, timeout=10)
    assert len(widget.chat_state["messages"]) == 3


def test_wrapped_bubbles_fit_and_follow_latest_message(window, app):
    widget, _ = window
    widget.casual_toggle.setChecked(True)
    widget.conversation.clear()
    for i in range(12):
        widget.conversation.put(str(i), "a", "A longer message that must remain readable. " * 8, "delivered")
    for _ in range(4):
        app.processEvents()
    for message, _, row in widget.conversation.bubbles.values():
        assert message.height() >= message.fontMetrics().height() * 3
        assert row.height() > message.height()
    scroll = widget.conversation.verticalScrollBar()
    assert scroll.value() == scroll.maximum()
    widget.conversation.follow = False
    scroll.setValue(0)
    widget.conversation.put("new", "b", "New message while you read older replies")
    app.processEvents()
    assert scroll.value() == 0
    # Item geometry, not resize-event side effects, must reserve bubble space.
    for width in (720, 1120, 800):
        widget.resize(width, 850)
        app.processEvents()
        view = widget.conversation
        rectangles = [view.visualItemRect(view.item(i)) for i in range(view.count())]
        assert all(first.bottom() < second.top() for first, second in zip(rectangles, rectangles[1:]))
        for key, (_, _, row) in view.bubbles.items():
            assert view.visualItemRect(view.items[key]).height() >= row.fit(view.viewport().width())


def test_stale_chat_poll_cannot_restore_pending_delivery(window, wait, tmp_path):
    from copy import deepcopy
    widget, controller = window
    attach_chat(widget, controller, tmp_path)
    wait(lambda: not widget.adapter.busy)
    stale = deepcopy(widget.chat_state)
    widget.prompt.setPlainText("hello")
    widget.chat_send.click()
    wait(lambda: not widget.adapter.busy, timeout=10)
    widget._show_chat(stale)
    assert len(widget.chat_state["messages"]) == 3
    assert all(m["state"] == "delivered" for m in widget.chat_state["messages"])

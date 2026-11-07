from autoteam_studio.ui.window import StartDialog
from autoteam_studio.contracts import RunSpec
from .test_window import ready


def test_start_cancellation_never_launches(window):
    widget, controller = window
    ready(widget, controller)
    seen = []
    widget.confirm_start = lambda spec, source=None: seen.append((spec, source)) or False
    widget.start_run()
    assert seen[0][0].project_root == controller.root
    assert "calculator" in seen[0][0].prompt
    assert not controller.calls
    assert "cancelled" in widget.notice.text()


def test_select_non_git_uses_selection_not_strict_preparation(window, wait):
    widget, controller = window
    calls = []
    controller.select_project = lambda path: calls.append(path) or path
    widget.prepare_project(controller.root, False)
    wait(lambda: not widget.adapter.busy)
    assert calls == [controller.root]
    assert widget.project == controller.root
    assert str(controller.root) in widget.live_project.text()


def test_multiline_message_failure_preserves_draft(window, wait):
    from .double import fixture
    widget, controller = window
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root))
    def fail(*args):
        raise RuntimeError("Could not persist message")
    controller.queue_correction = fail
    draft = "Keep the original behavior.\nAlso test the edge cases."
    widget.correction_input.setText(draft)
    widget.correction_button.click()
    wait(lambda: not widget.adapter.busy)
    assert widget.correction_input.text() == draft
    assert "Could not persist" in widget.notice.text()


def test_confirmation_shows_exact_project_prompt_tests(window):
    from PySide6.QtWidgets import QPlainTextEdit, QLabel
    widget, controller = window
    spec = RunSpec(controller.root, "Build the exact requested calculator", test_commands=("node --test",))
    dialog = StartDialog(spec, "real", "previous-run", widget)
    assert dialog.findChild(QPlainTextEdit).toPlainText() == spec.prompt
    labels = " ".join(x.text() for x in dialog.findChildren(QLabel))
    assert str(controller.root) in labels and "node --test" in labels and "previous-run" in labels
    dialog.deleteLater()

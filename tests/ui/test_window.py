from dataclasses import replace
from threading import Event, get_ident
import pytest

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox

from autoteam_studio.contracts import AgentEvent
from autoteam_studio.ui.adapter import BackgroundAdapter, Reply
from autoteam_studio.ui.window import AuthorizationDialog
from .double import fixture, STAMP


@pytest.mark.parametrize("state", ["queued", "running", "paused", "completed", "failed", "stopped"])
def test_lifecycle_views_preserve_backend_status(window, state):
    widget, controller = window
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root, state))
    assert f"Status: {state}" in widget.result_text.toPlainText()
    assert widget.stop_button.isEnabled() == (state not in {"completed", "failed", "stopped"})


def test_startup_reconcile_discovers_unfinished_history(window, wait):
    widget, controller = window
    widget.reconcile_due = True
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert "run-1" in widget.active
    assert "running" in widget.history.item(0).text()


def test_recovery_failure_keeps_partial_activity(window):
    widget, controller = window
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root))
    widget._reply(Reply("poll", widget._identity(), error=RuntimeError("storage unavailable")))
    assert "Recovery blocked" in widget.run_status.text()
    assert widget.timeline.count() == 2
    assert widget.offset == 200


def ready(widget, controller):
    widget.project = controller.root
    widget.dependencies = controller.check_dependencies()
    widget.prompt.setPlainText("Implement a calculator with unit tests")
    widget._enable()


def test_views_prompt_and_dependency_explanations(window):
    widget, controller = window
    assert [widget.tabs.tabText(i) for i in range(4)] == ["Project", "Team chat", "History", "Settings"]
    ready(widget, controller)
    for prompt in ("", "todo", "enter your prompt"):
        widget.prompt.setPlainText(prompt)
        assert not widget.start_button.isEnabled()
        widget.start_run()
    assert not controller.calls
    widget.prompt.setPlainText("Build a calculator and test it")
    widget.dependencies["git"]["available"] = False
    widget._enable()
    assert "git" in widget.start_reason.text()
    assert not widget.start_button.isEnabled()
    widget.dependencies["git"]["available"] = True
    widget.dependencies["vscode"]["available"] = False
    widget._enable()
    assert widget.start_button.isEnabled()  # optional in fake and real
    assert not widget.open_code.isEnabled()
    assert "unavailable" in widget.open_code.toolTip()


def test_prepare_and_start_run_off_thread(window, wait):
    widget, controller = window
    gui_thread = get_ident()
    threads = []
    original = controller.prepare_project
    def prepare(path, create):
        threads.append(get_ident())
        return original(path, create)
    controller.prepare_project = prepare
    widget.prepare_project(controller.root, create=True)
    wait(lambda: not widget.adapter.busy)
    assert threads[0] != gui_thread
    assert widget.project == controller.root
    assert widget.values["recent"] == [str(controller.root)]
    ready(widget, controller)
    widget.start_run()
    wait(lambda: not widget.adapter.busy)
    assert widget.run_id == "run-1"
    assert not controller.calls[-1][1].revision


def test_poll_activity_usage_controls_and_artifacts(window, wait):
    widget, controller = window
    ready(widget, controller)
    widget.select_run("run-1")
    response = fixture(controller.root)
    response["details"]["controls"]["pause"]["requested"] = True
    response["details"]["controls"]["corrections"] = [dict(
        id="c1", text="Add tests", sequence=1, created_at=STAMP,
        a=dict(state="delivered", call_id="a-1", updated_at=STAMP),
        b=dict(state="delivery_uncertain", call_id="b-1", updated_at=STAMP))]
    widget.apply_poll(response)
    assert "A implements" in widget.feeds["a"].item(0).text()
    assert "B writes" in widget.feeds["b"].item(0).text()
    assert widget.timeline.count() == 2
    assert "Unavailable" in widget.usage_labels["a"].text()
    assert "incomplete" in widget.usage_labels["b"].text()
    assert "delivery_uncertain" in widget.corrections_list.item(0).text()
    assert "Pause: requested" in widget.control_status.text()
    assert "deadline" in widget.call_status.text()
    widget.correction_input.setText("Improve tests")
    widget.correction_button.click()
    wait(lambda: not widget.adapter.busy)
    assert ("correction", "run-1", "Improve tests") in controller.calls
    assert "acknowledgment" in widget.notice.text()
    assert widget.correction_input.text() == ""
    widget.artifact_list.setCurrentRow(0)
    widget.artifact_button.click()
    wait(lambda: not widget.adapter.busy)
    assert ("artifact", "run-1", "report") in controller.calls
    widget.result_button.click()
    wait(lambda: not widget.adapter.busy)
    assert ("vscode", controller.root / "result") in controller.calls
    assert "Unverified" in widget.result_text.toPlainText()


def test_stale_identity_generation_and_cursor_rejected(window):
    widget, controller = window
    widget.select_run("run-1")
    old_identity = widget._identity()
    widget.apply_poll(fixture(controller.root))
    response = fixture(controller.root)
    response["generation"] = 0
    response["next_offset"] = 900
    widget.apply_poll(response)
    assert widget.offset == 200
    response["generation"] = 3
    widget.apply_poll(response)
    assert widget.offset == 200  # details generation did not match
    widget.select_run("run-2")
    widget._reply(Reply("poll", old_identity, fixture(controller.root)))
    assert widget.offset == 0
    assert widget.timeline.count() == 0
    assert widget.snapshot is None


def test_bursts_are_bounded_and_plain_text(window, app):
    widget, controller = window
    widget.select_run("run-1")
    for batch in range(12):
        response = fixture(controller.root)
        response["events"] = [AgentEvent("run-1", "a" if n % 2 else "b", STAMP,
                                         "unknown_kind", f"<b>record {batch * 200 + n}</b>\nsecond line")
                              for n in range(200)]
        response["next_offset"] = (batch + 1) * 200
        widget.apply_poll(response)
        app.processEvents()
    assert widget.timeline.count() == 1000
    assert widget.feeds["a"].count() == 1000
    assert widget.feeds["b"].count() == 1000
    assert "<b>" in widget.timeline.item(999).text()


def test_reserved_control_lane_and_backpressure(app, wait):
    adapter = BackgroundAdapter()
    gate = Event()
    results = []
    adapter.completed.connect(results.append)
    try:
        assert adapter.submit("poll", (0,), lambda: gate.wait(3))
        assert not adapter.submit("poll", (0,), lambda: None)
        assert adapter.submit("stop", (0,), lambda: "published", control=True)
        wait(lambda: len(results) == 1)
        assert results[0].value == "published"
        assert "work" in adapter.jobs
        assert not adapter.submit("poll", (0,), lambda: None)
    finally:
        gate.set()
        wait(lambda: not adapter.busy)


def test_gui_heartbeat_continues_during_slow_poll(window, wait):
    widget, controller = window
    gate = Event()
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        widget._submit("slow", lambda: gate.wait(3))
        wait(lambda: len(ticks) >= 5)
        assert widget.adapter.busy
    finally:
        gate.set()
        wait(lambda: not widget.adapter.busy)
        timer.stop()


def test_authorization_exact_inspection_and_rejection(window, wait, monkeypatch):
    widget, controller = window
    ready(widget, controller)
    widget.values["test_commands"] = '"C:/Python312/python.exe" -m unittest'
    widget._enable()
    assert not widget.start_button.isEnabled()
    monkeypatch.setattr(widget, "confirm_authorization", lambda inspection: False)
    widget._inspect_authorization()
    wait(lambda: not widget.adapter.busy)
    assert not any(call[0] == "grant" for call in controller.calls)
    monkeypatch.setattr(widget, "confirm_authorization", lambda inspection: True)
    widget._inspect_authorization()
    wait(lambda: not widget.adapter.busy)
    assert ("grant", controller.root, widget._commands(), "inspection-1") in controller.calls
    assert widget.start_button.isEnabled()
    dialog = AuthorizationDialog(controller.inspect_test_authorization(controller.root, widget._commands()))
    from PySide6.QtWidgets import QPlainTextEdit
    text = dialog.findChild(QPlainTextEdit).toPlainText()
    assert "sanitized_inherited_v1" in text and "result_worktree" in text and "600" in text
    dialog.deleteLater()


def test_revision_explicit_source_and_failed_launch_history(window, wait):
    widget, controller = window
    ready(widget, controller)
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root, "completed"))
    widget.start_run(True)
    wait(lambda: not widget.adapter.busy)
    call = next(call for call in controller.calls if call[0] == "revision")
    assert call[2] == "run-1"
    assert call[1].revision is False
    assert widget.run_id == "run-2"
    controller.launch_failure = True
    widget.start_run()
    wait(lambda: not widget.adapter.busy)
    assert widget.run_id == "run-1"
    widget.history_due = True
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert "failed" in widget.history.item(0).text()
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert widget.snapshot.status == "failed"
    assert not widget.stop_button.isEnabled()


def test_checkpoint_ack_and_terminal_control_disable(window, wait):
    widget, controller = window
    widget.select_run("run-1")
    response = fixture(controller.root, "paused")
    response["details"]["controls"]["pause"].update(requested=True, acknowledged=True)
    widget.apply_poll(response)
    assert "Pause: acknowledged" in widget.control_status.text()
    assert widget.resume_button.isEnabled()
    widget.resume_button.click()
    wait(lambda: not widget.adapter.busy)
    assert ("resume", "run-1") in controller.calls
    response["details"]["controls"]["stop"]["requested"] = True
    widget.apply_poll(response)
    assert not widget.resume_button.isEnabled()
    assert "Stop: requested" in widget.control_status.text()
    response["snapshot"] = replace(response["snapshot"], status="stopped")
    response["details"]["controls"]["stop"]["acknowledged"] = True
    widget.apply_poll(response)
    assert not widget.correction_button.isEnabled()


def test_close_waits_for_checkpoint(window, wait, monkeypatch):
    widget, controller = window
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.close()
    assert widget.closing and widget.isVisible()
    widget._close_step()
    wait(lambda: not widget.adapter.busy)
    assert ("stop", "run-1") in controller.calls
    widget._close_step()
    wait(lambda: widget.allow_close)
    assert not widget.isVisible()


def test_settings_replace_controller_and_invalidate_authorization(window, wait):
    widget, controller = window
    ready(widget, controller)
    widget.authorization = {"authorized": True}
    created = []
    widget.factory = lambda values: created.append(values) or controller
    widget.backend.setCurrentText("fake")
    widget.settings_fields["test_timeout_seconds"].setValue(42)
    widget._save_settings()
    wait(lambda: not widget.adapter.busy)
    assert created[0]["test_timeout_seconds"] == 42
    assert widget.authorization is None
    assert widget.preferences.load()["test_timeout_seconds"] == 42
    assert widget.dependencies is None


def test_storage_change_preserves_active_controller(window, wait, monkeypatch):
    widget, controller = window
    ready(widget, controller)
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root))
    created = []
    widget.factory = lambda values: created.append(values)
    widget.settings_fields["storage_root"].setText(str(controller.root / "other"))
    widget._save_settings()
    assert not created
    assert widget.controller is controller
    assert "unfinished runs" in widget.notice.text()
    assert widget.preferences.load()["storage_root"] == widget.values["storage_root"]
    widget.correction_input.setText("Keep the worker reachable")
    widget.correction_button.click()
    wait(lambda: not widget.adapter.busy)
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert ("correction", "run-1", "Keep the worker reachable") in controller.calls
    assert any(call[0] == "poll" for call in controller.calls)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.close()
    widget._close_step()
    wait(lambda: not widget.adapter.busy)
    assert ("stop", "run-1") in controller.calls
    widget._close_step()
    wait(lambda: widget.allow_close)


def test_storage_change_checks_unselected_indexed_runs(window, wait):
    widget, controller = window
    created = []
    widget.factory = lambda values: created.append(values)
    widget.settings_fields["storage_root"].setText(str(controller.root / "other"))
    widget._save_settings()
    wait(lambda: not widget.adapter.busy)
    assert not created
    assert widget.controller is controller
    assert "indexed runs are unfinished" in widget.notice.text()


def test_replacement_recovers_before_operations(window, wait):
    from .double import ControllerDouble
    widget, controller = window
    ready(widget, controller)
    widget.select_run("run-1")
    widget.apply_poll(fixture(controller.root, "completed"))
    controller.response = fixture(controller.root, "completed")
    replacement = ControllerDouble(controller.root / "other")
    widget.factory = lambda values: replacement
    widget.settings_fields["storage_root"].setText(str(replacement.root))
    widget._save_settings()
    wait(lambda: not widget.adapter.busy)
    assert widget.controller is replacement
    assert widget.run_id is None and widget.snapshot is None
    assert not widget.reconciled
    assert not widget.start_button.isEnabled()
    assert not widget.stop_button.isEnabled()
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert widget.reconciled
    assert widget.active == {"run-1"}


def test_failed_startup_recovery_prevents_close_until_recovered(window, wait, monkeypatch):
    widget, controller = window
    original = controller.reconcile
    def fail():
        raise RuntimeError("storage unavailable")
    controller.reconcile = fail
    widget.reconcile_due = True
    widget.refresh()
    wait(lambda: not widget.adapter.busy)
    assert not widget.active
    assert not widget.reconciled
    assert widget.reconcile_due
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.close()
    assert widget.isVisible() and not widget.allow_close
    widget._close_step()
    wait(lambda: not widget.adapter.busy)
    assert widget.isVisible() and not widget.allow_close
    assert not widget.reconciled
    widget.timer.stop()
    controller.reconcile = original
    widget.close()
    widget._close_step()
    wait(lambda: not widget.adapter.busy)
    assert widget.active == {"run-1"}
    assert not widget.allow_close
    widget._close_step()
    wait(lambda: not widget.adapter.busy)
    assert ("stop", "run-1") in controller.calls
    widget._close_step()
    wait(lambda: widget.allow_close)

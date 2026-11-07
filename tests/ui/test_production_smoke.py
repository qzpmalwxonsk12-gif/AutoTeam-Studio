"""GUI -> production facade -> owned fake worker; no real-account execution.

Absence of Task A is an explicit skip, never replaced with a UI double.
Once A is integrated, failures are integration failures and are not skipped.
"""
import os
import shutil
import subprocess
import sys

import pytest
from PySide6.QtCore import QSettings

from autoteam_studio.ui.preferences import Preferences, production_controller
from autoteam_studio.ui.window import MainWindow, TERMINAL


def test_gui_production_fake_worker(app, ui_workspace, wait, monkeypatch):
    try:
        from autoteam_studio.core.api import ControllerSettings, TeamController
    except ModuleNotFoundError as exc:
        if exc.name in {"autoteam_studio.core", "autoteam_studio.core.api"}:
            pytest.skip("Integration blocker: Task A core.api and owned fake worker are absent")
        raise
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        pytest.skip("Production V1 requires Windows and Python 3.12")
    git = shutil.which("git")
    if not git:
        pytest.skip("Production fake worktrees require Git")

    # Keep the fixed discovery namespace, preferences, account homes, storage,
    # Git global config and demo project inside this disposable workspace.
    monkeypatch.setenv("LOCALAPPDATA", str(ui_workspace / "local"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ui_workspace / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    preferences = Preferences(QSettings(str(ui_workspace / "ui.ini"), QSettings.Format.IniFormat))
    values = preferences.load()
    values.update(backend="fake", auto_open=False, git_executable=git,
                  account_a_home=str(ui_workspace / "account-a"),
                  account_b_home=str(ui_workspace / "account-b"),
                  storage_root=str(ui_workspace / "storage"), max_call_seconds=30,
                  test_commands=f'"{sys.executable}" -m unittest discover -s tests -p test_autoteam_demo_*.py')
    preferences.save(values)
    widget = MainWindow(preferences=preferences, poll_interval=30)
    widget.confirm_authorization = lambda inspection: True  # test-only fixture grant
    widget.confirm_start = lambda spec, source=None: True  # test-only simulated consent
    widget.show()
    try:
        wait(lambda: widget.dependencies is not None, timeout=20)
        assert widget.controller is not None
        widget.timer.stop()
        wait(lambda: not widget.adapter.busy)
        project = ui_workspace / "demo project"
        widget.prepare_project(project, create=True)
        wait(lambda: not widget.adapter.busy, timeout=65)
        assert widget.project == project, widget.notice.text()
        before = subprocess.run([git, "-C", str(project), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        widget._inspect_authorization()
        wait(lambda: not widget.adapter.busy, timeout=20)
        assert widget.authorization and widget.authorization["authorized"], widget.notice.text()
        widget.prompt.setPlainText("Implement a small calculator with meaningful source and unit tests from both owners.")
        widget.start_run()
        wait(lambda: not widget.adapter.busy, timeout=35)
        assert widget.run_id, widget.notice.text()
        widget.timer.start()
        wait(lambda: widget.snapshot is not None and widget.snapshot.status in TERMINAL, timeout=180)
        assert widget.snapshot.status == "completed", widget.result_text.toPlainText()
        assert widget.feeds["a"].count() > 0
        assert widget.feeds["b"].count() > 0
        assert widget.artifact_list.count() > 0
        assert widget.details["result_sha"]
        after = subprocess.run([git, "-C", str(project), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run([git, "-C", str(project), "status", "--porcelain"],
                               capture_output=True, text=True, check=True, timeout=10).stdout
        assert before == after and not dirty
    finally:
        widget.timer.stop()
        wait(lambda: not widget.adapter.busy, timeout=65)
        if widget.run_id and (not widget.snapshot or widget.snapshot.status not in TERMINAL):
            controller, run_id = widget.controller, widget.run_id
            widget._submit("cleanup", lambda: controller.stop_at_checkpoint(run_id), control=True)
            wait(lambda: not widget.adapter.busy, timeout=10)
            widget.timer.start()
            wait(lambda: widget.snapshot and widget.snapshot.status in TERMINAL, timeout=90)
        widget.allow_close = True
        widget.close()
        widget.deleteLater()
        app.processEvents()

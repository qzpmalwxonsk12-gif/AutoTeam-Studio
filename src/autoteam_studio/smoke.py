"""Explicit offline packaged GUI -> facade -> worker -> test executable check."""
import json
import os
from pathlib import Path
import time


def main(folder):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    # Set these before constructing Qt/controller; account homes are dummy paths.
    os.environ["LOCALAPPDATA"] = str(folder / "local")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from autoteam_studio.core.api import fake_demo_commands
    from autoteam_studio.core.project import git
    from autoteam_studio.ui.preferences import Preferences
    from autoteam_studio.ui.window import MainWindow, TERMINAL
    app = QApplication.instance() or QApplication([])
    # Qt's offscreen Windows plugin does not discover system fonts automatically.
    font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
        app.setFont(QFont("Segoe UI", 10))
    prefs = Preferences(QSettings(str(folder / "desktop.ini"), QSettings.Format.IniFormat))
    values = prefs.load()
    values.update(backend="fake", auto_open=False, storage_root=str(folder / "storage"),
                  account_a_home=str(folder / "dummy-a"), account_b_home=str(folder / "dummy-b"),
                  test_commands="\n".join(fake_demo_commands()), max_call_seconds=30)
    prefs.save(values)
    window = MainWindow(preferences=prefs, poll_interval=50)
    window.confirm_start = lambda spec, source=None: True  # Explicit offline-only fixture.
    window.confirm_authorization = lambda inspection: True
    window.show()
    def wait(predicate, seconds=40):
        deadline = time.monotonic() + seconds
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise RuntimeError("Smoke timed out: " + window.notice.text())
            time.sleep(.01)
        app.processEvents()
    try:
        wait(lambda: window.dependencies is not None)
        window.timer.stop()
        wait(lambda: not window.adapter.busy)
        window._open_chat(new=True)
        wait(lambda: not window.adapter.busy)
        window.prompt.setPlainText("Hello, are you both here?")
        window.chat_send.click()
        wait(lambda: not window.adapter.busy)
        if not window.chat_state or len(window.chat_state["messages"]) != 3 or window.run_id:
            raise RuntimeError("Chat must produce two replies without starting a project run")
        if any(m["state"] != "delivered" for m in window.chat_state["messages"]):
            raise RuntimeError("Packaged conversational worker did not reply")
        window.casual_toggle.setChecked(True)
        window.prompt.setPlainText("Build a calculator")
        window.chat_send.click()
        wait(lambda: not window.adapter.busy)
        if window.chat_state["build_prompt"] or window.run_id:
            raise RuntimeError("Just chat leaked into project execution")
        window.grab().save(str(folder / "chat-phone-light.png"))
        window.theme_choice.setCurrentText("Quiet dark")
        app.processEvents()
        window.grab().save(str(folder / "chat-phone-dark.png"))
        window.casual_toggle.setChecked(False)
        window.theme_choice.setCurrentText("Soft light")
        window.prepare_project(folder / "project", create=True)
        wait(lambda: not window.adapter.busy)
        if not window.project:
            raise RuntimeError(window.notice.text())
        window.timer.start()
        wait(lambda: window.project_check and window.project_check["ready"])
        window.timer.stop()
        wait(lambda: not window.adapter.busy)
        before = git(window.controller._git(), window.project, "rev-parse", "HEAD")
        window._inspect_authorization()
        wait(lambda: not window.adapter.busy)
        window.prompt.setPlainText("Build a small tested calculator with contributions from both simulated agents.")
        window.tabs.setCurrentIndex(1)
        window.grab().save(str(folder / "live-before.png"))
        window.start_run()
        wait(lambda: not window.adapter.busy)
        if not window.run_id:
            raise RuntimeError(window.notice.text())
        window.timer.start()
        wait(lambda: window.snapshot is not None and window.snapshot.status in TERMINAL, 180)
        window.timer.stop()
        wait(lambda: not window.adapter.busy)
        if window.snapshot.status != "completed":
            raise RuntimeError(window.result_text.toPlainText())
        if before != git(window.controller._git(), window.project, "rev-parse", "HEAD") or git(window.controller._git(), window.project, "status", "--porcelain"):
            raise RuntimeError("Original project changed")
        window.grab().save(str(folder / "live-completed.png"))
        window.tabs.setCurrentIndex(2)
        app.processEvents()
        window.grab().save(str(folder / "results.png"))
        result = {"status": "passed", "run_id": window.run_id, "result_sha": window.details["result_sha"],
                  "agent_a_events": window.feeds["a"].count(), "agent_b_events": window.feeds["b"].count(),
                  "artifacts": window.artifact_list.count(), "original_preserved": True,
                  "chat_replies": 4, "casual_mode_no_build": True,
                  "real_codex_calls": 0}
        (folder / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        (folder / "result.json").write_text(json.dumps({"status": "failed", "error": str(exc)}, indent=2), encoding="utf-8")
        return 1
    finally:
        window.timer.stop()
        if window.controller:
            for process in list(window.controller._workers.values()):
                process.close()
        wait(lambda: not window.adapter.busy)
        window.allow_close = True
        window.close()


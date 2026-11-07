"""Exercise the real module entry point in a bounded offscreen subprocess."""
import os
from pathlib import Path
import subprocess
import sys


def test_entry_point_launches_without_core_or_credentials():
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    script = '''
import runpy
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from autoteam_studio.ui import window
original = window.MainWindow
def create():
    widget = original()
    def finish():
        assert widget.isVisible()
        assert widget.tabs.count() == 4
        if widget.adapter.busy:
            QTimer.singleShot(50, finish)
            return
        widget.allow_close = True
        widget.close()
    QTimer.singleShot(200, finish)
    return widget
window.MainWindow = create
runpy.run_module("autoteam_studio", run_name="__main__")
'''
    # The bootstrap must not scan real account homes in this launch-only test.
    # Use a factory that explicitly reports absent integration, without opening
    # credentials even when this test is run after A is merged.
    script = script.replace("widget = original()", "widget = original(controller_factory=lambda values: (_ for _ in ()).throw(ImportError('test backend unavailable')))")
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

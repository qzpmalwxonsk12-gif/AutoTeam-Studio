import os
import time
import shutil
import stat
import uuid
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from autoteam_studio.ui.preferences import Preferences
from autoteam_studio.ui.window import MainWindow
from .double import ControllerDouble

collect_ignore_glob = ["tempcheck*", ".tmp-check*", ".test-work"]


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def wait(app):
    def until(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("GUI condition timed out")
            time.sleep(0.005)
        app.processEvents()
    return until


@pytest.fixture
def ui_workspace():
    # Python 3.12's Windows mode-0700 temporary directories can exclude the
    # restricted execution token. Use normal inherited workspace permissions.
    base = Path(os.environ.get("AUTOTEAM_TEST_TEMP", str(Path(tempfile.gettempdir()) / "ats-ui")))
    path = base / uuid.uuid4().hex[:12]
    path.mkdir(parents=True)
    yield path
    assert path.resolve().is_relative_to(base.resolve())
    import gc
    gc.collect()
    def remove_readonly(func, target, exc_info):
        # Git object files on Windows can be read-only. Clear that attribute
        # before retrying removal; do not weaken project runtime permissions.
        if not isinstance(exc_info[1], PermissionError):
            raise exc_info[1]
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)

    for attempt in range(8):
        try:
            shutil.rmtree(path, onerror=remove_readonly)
            break
        except (PermissionError, OSError):
            if attempt == 7:
                pytest.fail(f"Windows could not clean test workspace: {path}")
            time.sleep(0.25 * (attempt + 1))


@pytest.fixture
def window(app, ui_workspace, wait):
    tmp_path = ui_workspace
    controller = ControllerDouble(tmp_path)
    preferences = Preferences(QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat))
    values = preferences.load()
    values.update(backend="fake", auto_open=False)
    preferences.save(values)
    widget = MainWindow(controller, preferences=preferences, startup=False)
    widget.confirm_start = lambda spec, source=None: True  # Explicit simulated consent.
    widget.reconciled = True  # Most tests begin after successful startup recovery.
    widget.show()
    yield widget, controller
    wait(lambda: not widget.adapter.busy)
    widget.allow_close = True
    widget.close()
    widget.deleteLater()
    app.processEvents()

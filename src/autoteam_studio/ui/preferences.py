"""Only user preferences are stored here; never authentication data."""
from __future__ import annotations

import json
from pathlib import Path
from PySide6.QtCore import QSettings, QStandardPaths


DEFAULTS = {
    "backend": "real", "git_executable": "", "codex_executable": "",
    "vscode_executable": "", "worker_executable": "", "storage_root": "",
    "account_a_home": str(Path.home() / ".codex-a"),
    "account_b_home": str(Path.home() / ".codex-b"),
    "test_timeout_seconds": 600, "max_call_seconds": 1200,
    "review_rounds": 1, "auto_open": True, "test_commands": "", "recent": [],
    "theme": "Soft light", "casual_chat": False,
}


class Preferences:
    def __init__(self, store=None):
        self.store = store if store is not None else QSettings(
            str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
                / "AutoTeamStudio" / "desktop.ini"), QSettings.Format.IniFormat)

    def load(self):
        values = dict(DEFAULTS)
        try:
            saved = json.loads(self.store.value("preferences", "{}"))
            for key, default in DEFAULTS.items():
                if key in saved and type(saved[key]) is type(default):
                    values[key] = saved[key]
        except (TypeError, ValueError):
            pass
        return values

    def save(self, values):
        self.store.setValue("preferences", json.dumps({k: values[k] for k in DEFAULTS}))
        self.store.sync()


def production_controller(values):
    # Sole production dependency boundary; allows the shell to explain a missing
    # backend rather than failing to import before a window can be displayed.
    from autoteam_studio.core.api import ControllerSettings, TeamController
    paths = {key: Path(values[key]).expanduser().resolve() if values[key] else None
             for key in ("git_executable", "codex_executable", "vscode_executable",
                         "worker_executable", "storage_root", "account_a_home", "account_b_home")}
    return TeamController(ControllerSettings(
        backend=values["backend"], test_timeout_seconds=values["test_timeout_seconds"], **paths))

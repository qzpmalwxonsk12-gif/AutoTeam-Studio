"""Desktop workspace. All facade I/O is performed by BackgroundAdapter."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QPushButton, QLabel, QPlainTextEdit, QLineEdit, QListWidget, QListWidgetItem,
    QComboBox, QSpinBox, QCheckBox, QFileDialog, QMessageBox, QSplitter,
    QDialog, QDialogButtonBox, QScrollArea, QInputDialog,
)

from autoteam_studio.contracts import RunSpec
from .adapter import BackgroundAdapter
from .chat_workspace import ChatWorkspace
from .conversation import theme
from .preferences import Preferences, production_controller

TERMINAL = {"completed", "failed", "stopped"}


def meaningful_prompt(text):
    text = text.strip()
    return len(text) >= 10 and len(text.split()) >= 2 and text.casefold() not in {
        "enter your prompt", "enter prompt here", "your prompt here", "type your prompt here",
    }


def label(text=""):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def feed():
    widget = QListWidget()
    widget.setWordWrap(True)
    widget.setUniformItemSizes(False)
    return widget


def bounded_append(widget, text):
    # One item is one durable record even when the message has many lines.
    widget.addItem(text)
    while widget.count() > 1000:
        widget.takeItem(0)


class AuthorizationDialog(QDialog):
    def __init__(self, inspection, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Authorize project tests")
        self.resize(700, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(label("These commands execute project code which agents may subsequently modify."))
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(json.dumps({key: inspection[key] for key in (
            "commands", "working_directory", "environment_policy", "timeout_seconds",
            "execution", "project_identity", "notice")}, indent=2))
        layout.addWidget(detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Authorize exact commands")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MessageEditor(QPlainTextEdit):
    """Multiline composer; compatibility helpers keep controller/UI callers simple."""
    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)


class StartDialog(QDialog):
    def __init__(self, spec, backend, source=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm revision" if source else "Confirm team start")
        self.resize(720, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Project: {spec.project_root}\n" +
                               (f"Revision of: {source}\n" if source else "") +
                               ("Uses both saved Codex accounts." if backend == "real" else "Offline simulation — no Codex usage.")))
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(spec.prompt)
        layout.addWidget(preview, 1)
        layout.addWidget(label("Tests: " + ("\n".join(spec.test_commands) or "None configured — this run cannot be marked verified.")))
        layout.addWidget(label("The team works in separate Git worktrees. Your selected checkout is preserved."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start confirmed task")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(ChatWorkspace, QMainWindow):
    def __init__(self, controller=None, *, controller_factory=production_controller,
                 preferences=None, poll_interval=300, startup=True):
        super().__init__()
        self.setWindowTitle("AutoTeam Studio")
        self.resize(1120, 850)
        self.setMinimumSize(700, 650)
        self.preferences = preferences or Preferences()
        self.values = self.preferences.load()
        self.setStyleSheet(theme(self.values["theme"] == "Quiet dark"))
        self.factory = controller_factory
        self.controller = controller
        self.project = None
        self.run_id = None
        self.snapshot = None
        self.details = None
        self.dependencies = None
        self.account_checks = None
        self.project_check = None
        self.project_check_due = False
        self.confirming = False
        self.offset = 0
        self.generation = -1
        self.epoch = 0
        self.run_epoch = 0
        self.active = set()
        self.authorization = None
        self.inspection_context = None
        self.starting = False
        self.authorizing = False
        self.correction_submitted = None
        self.closing = False
        self.close_stops = set()
        self.allow_close = False
        self.history_due = False
        self.reconcile_due = startup
        self.reconciled = False
        self.auto_open_due = False
        self.adapter = BackgroundAdapter(self)
        self.adapter.completed.connect(self._reply)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._projects_view()
        self._live_view()
        self._results_view()
        self._settings_view()
        self.tabs.setCurrentIndex(1)
        self.notice = label("Welcome. Connect your two accounts, then say hello.")
        self.statusBar().addWidget(self.notice, 1)
        if self.controller is None:
            self._submit("bootstrap", lambda: self.factory(self.values))
        self.timer = QTimer(self)
        self.timer.setInterval(poll_interval)
        self.timer.timeout.connect(self.refresh)
        if startup:
            self.timer.start()
            QTimer.singleShot(0, self.refresh)
        self._enable()

    def _page(self, name):
        page = QWidget()
        layout = QVBoxLayout(page)
        page.setObjectName("page")
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        self.tabs.addTab(area, name)
        return layout

    def _button(self, text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _projects_view(self):
        layout = self._page("Project")
        layout.addWidget(label("Project setup ? Choose where your team will work when you are ready to build."))
        self.project_label = label("No project selected")
        layout.addWidget(self.project_label)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.open_project = self._button("Open project…", lambda: self._pick_project(False), row)
        self.create_project = self._button("Create project…", lambda: self._pick_project(True), row)
        self.open_code = self._button("Open project in VS Code", self._open_project_code, row)
        self.project_readiness = label("Choose any existing local project folder, or create a new one.")
        layout.addWidget(self.project_readiness)
        self.baseline_button = self._button("Review and save Git baseline…", self._inspect_baseline, layout)
        account_row = QHBoxLayout()
        layout.addLayout(account_row)
        self.account_labels = {}
        for who in ("a", "b"):
            self.account_labels[who] = label(f"Account {who.upper()}: not checked")
            account_row.addWidget(self.account_labels[who])
        self.connect_button = self._button("Check / connect saved accounts", self._connect_accounts, layout)
        layout.addWidget(label("Checking saved logins does not start development. Account paths can be changed in Settings."))
        layout.addWidget(label("Recent projects — double-click to open"))
        self.recents = QListWidget()
        self.recents.addItems(self.values["recent"])
        self.recents.itemDoubleClicked.connect(lambda item: self.prepare_project(Path(item.text()), False))
        self.recents.setMaximumHeight(140)
        layout.addWidget(self.recents)
        self.dependency_text = QPlainTextEdit()
        self.dependency_text.setReadOnly(True)
        self.dependency_text.setPlainText("Checking dependencies…")
        self.dependency_text.setMaximumHeight(180)
        self.dependency_text.hide()
        diagnostics = QCheckBox("Show installation details")
        diagnostics.toggled.connect(self.dependency_text.setVisible)
        layout.addWidget(diagnostics)
        layout.addWidget(self.dependency_text)
        self._button("Refresh dependencies", self._check_dependencies, layout)
        self._button("Back to conversation", lambda: self.tabs.setCurrentIndex(1), layout)
        layout.addStretch()

    def _results_view(self):
        layout = self._page("History")
        self._button("Refresh history", self._history, layout)
        self.history = QListWidget()
        self.history.itemClicked.connect(lambda item: self.select_run(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.history)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text)
        self.result_button = self._button("Open result worktree in VS Code", self._open_result, layout)
        self.restart_button = self._button("Resume stopped run from checkpoint…", self._resume_stopped, layout)
        self.artifact_list = QListWidget()
        layout.addWidget(self.artifact_list)
        self.artifact_list.currentRowChanged.connect(self._enable)
        self.artifact_button = self._button("Open selected artifact", self._open_artifact, layout)

    def _settings_view(self):
        layout = self._page("Settings")
        area = QScrollArea()
        area.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        self.settings_fields = {}
        self.backend = QComboBox()
        self.backend.addItems(["real", "fake"])
        self.backend.setCurrentText(self.values["backend"])
        form.addRow("Backend", self.backend)
        self.backend.setToolTip("real uses saved Codex logins; fake runs an offline simulation.")
        for key, caption in (
            ("account_a_home", "Account A home"), ("account_b_home", "Account B home"),
            ("git_executable", "Git executable (optional)"), ("codex_executable", "Codex executable (optional)"),
            ("vscode_executable", "VS Code executable (optional)"), ("worker_executable", "Worker executable (optional)"),
            ("storage_root", "Storage root (optional)"),
        ):
            field = QLineEdit(self.values[key])
            self.settings_fields[key] = field
            row = QHBoxLayout()
            row.addWidget(field)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, key=key: self._browse_setting(key))
            row.addWidget(browse)
            form.addRow(caption, row)
        for key, caption, minimum, maximum in (
            ("test_timeout_seconds", "Test deadline (seconds)", 1, 7200),
            ("max_call_seconds", "Model call deadline (seconds)", 30, 7200),
            ("review_rounds", "Integrated review passes", 1, 4),
        ):
            field = QSpinBox()
            field.setRange(minimum, maximum)
            field.setValue(self.values[key])
            self.settings_fields[key] = field
            form.addRow(caption, field)
        self.test_commands = QPlainTextEdit(self.values["test_commands"])
        self.test_commands.setPlaceholderText("One command per line; no shell operators")
        form.addRow("Project test commands", self.test_commands)
        presets = QHBoxLayout()
        self.test_preset = QComboBox()
        self.test_preset.addItems(["Choose a test preset", "Python / pytest", "JavaScript / Node", "JavaScript / npm", "Rust", "Go", ".NET", "Offline demo"])
        presets.addWidget(self.test_preset)
        self._button("Use preset", self._use_test_preset, presets)
        form.addRow("Test presets", presets)
        form.addRow(label("Use the project's actual test runner. Presets fill the command box; Save and authorize before running. Build outputs should be listed in the project's .gitignore."))
        self.auto_open = QCheckBox("Open projects automatically in VS Code")
        self.auto_open.setChecked(self.values["auto_open"])
        form.addRow(self.auto_open)
        area.setWidget(panel)
        layout.addWidget(area)
        self.save_settings = self._button("Save settings for new launches", self._save_settings, layout)
        self.authorize_button = self._button("Inspect and authorize saved test commands…", self._inspect_authorization, layout)
        self.authorization_text = label("No test authorization inspected. Empty tests are unverified and cannot pass acceptance.")
        layout.addWidget(self.authorization_text)

    def _identity(self):
        return self.epoch, self.run_epoch, self.run_id

    def _submit(self, operation, call, *, control=False):
        accepted = self.adapter.submit(operation, self._identity(), call, control=control)
        if not accepted:
            self.notice.setText("Operation in progress; please try again when it finishes.")
        self._enable()
        return accepted

    def _pick_project(self, create):
        if create:
            parent = QFileDialog.getExistingDirectory(self, "Choose the parent folder for your new project")
            if not parent:
                return
            name, accepted = QInputDialog.getText(self, "Create project", "New folder name:")
            name = name.strip()
            if not accepted:
                return
            if not name or name in {".", ".."} or any(c in name for c in '<>:"/\\|?*') or name.endswith((" ", ".")):
                self.notice.setText("Enter a valid folder name without path separators.")
                return
            path = str(Path(parent) / name)
        else:
            path = QFileDialog.getExistingDirectory(self, "Choose existing project folder")
        if path:
            self.prepare_project(Path(path), create)

    def prepare_project(self, path, create=False):
        if self.controller is None or self.starting:
            return
        controller = self.controller
        self.preparing_create = create
        call = (lambda: controller.select_project(path)) if not create and hasattr(controller, "select_project") else (lambda: controller.prepare_project(path, create=create))
        self._submit("prepare", call)

    def _connect_accounts(self):
        if self.controller and hasattr(self.controller, "connect_accounts"):
            self._submit("accounts", self.controller.connect_accounts)

    def _inspect_baseline(self):
        if self.controller and self.project:
            controller, project = self.controller, self.project
            self._submit("baseline_preview", lambda: controller.inspect_baseline(project))

    def confirm_baseline(self, preview):
        dialog = QDialog(self)
        dialog.setWindowTitle("Review local Git baseline")
        dialog.resize(720, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"Project: {preview['root']}\nSave a local commit of the files below. Existing history is retained. Nothing is uploaded."))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        deleted = set(preview["deleted"])
        text.setPlainText("\n".join(("DELETE from Git: " if p in deleted else "Include: ") + p for p in preview["files"]) or "Empty project baseline")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save local baseline")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _check_dependencies(self):
        if self.controller:
            if self.project and hasattr(self.controller, "project_status"):
                self.project_check_due = True
            self._submit("dependencies", self.controller.check_dependencies)

    def _browse_setting(self, key):
        if key.endswith("_home") or key == "storage_root":
            path = QFileDialog.getExistingDirectory(self, "Choose " + key.replace("_", " "))
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose executable", "", "Programs (*.exe *.cmd);;All files (*)")
        if path:
            self.settings_fields[key].setText(path)

    def _use_test_preset(self):
        import sys
        name = self.test_preset.currentText()
        presets = {"Python / pytest": 'python -m pytest', "JavaScript / Node": "node --test",
                   "JavaScript / npm": "npm test", "Rust": "cargo test", "Go": "go test ./...", ".NET": "dotnet test"}
        if name == "Offline demo":
            from autoteam_studio.core.api import fake_demo_commands
            self.test_commands.setPlainText("\n".join(fake_demo_commands()))
            self.backend.setCurrentText("fake")
        elif name in presets:
            if name == "Python / pytest" and not getattr(sys, "frozen", False):
                presets[name] = f'"{sys.executable}" -m pytest'
            self.test_commands.setPlainText(presets[name])

    def _commands(self):
        return tuple(line.strip() for line in self.values["test_commands"].splitlines() if line.strip())

    def _start_blocker(self):
        if self.casual_toggle.isChecked():
            return "Turn off Just chat to prepare a project build."
        if "chat" in self.adapter.jobs:
            return "Wait for the conversation replies before starting a build."
        if not self.controller:
            return "Backend unavailable. Check the installation and restart the application."
        if not self.reconciled:
            return "Storage recovery must succeed before starting a run."
        if self.starting or "work" in self.adapter.jobs:
            return "Waiting for the current background operation."
        if self.project is None:
            return "Choose a project folder first."
        if self.project_check_due or (self.project_check and not self.project_check["ready"]):
            return "Review the project's Git baseline in Projects before starting."
        if not self.dependencies:
            return "Dependency check required."
        required = ["git", "runtime", "worker"]
        if self.values["backend"] == "real":
            required += ["codex", "account_a", "account_b"]
        missing = [name for name in required if not self.dependencies[name]["available"]]
        if missing:
            return "Unavailable: " + ", ".join(missing) + ". See Projects diagnostics."
        if self.values["backend"] == "real" and not (self.account_checks and all(v["available"] for v in self.account_checks.values())):
            return "Check / connect both saved accounts in Projects first."
        if self.snapshot and self.snapshot.status not in TERMINAL and self.details and Path(self.details["project_root"]) == self.project:
            return "This project has an active run. Send a message to the team or pause it."
        if not meaningful_prompt(self.prompt.toPlainText()):
            return "Enter a meaningful task (at least 10 characters and two words)."
        if self.values["backend"] == "real" and not self._commands():
            return "Configure and authorize project tests in Settings before starting a real task."
        if self._commands() and not (self.authorization and self.authorization["authorized"]):
            return "Inspect and authorize the saved project tests in Settings first."
        return ""

    def _enable(self, *_):
        if not hasattr(self, "notice"):
            return
        reason = self._start_blocker()
        can_start = not reason and not self.closing
        self.start_button.setEnabled(can_start)
        self.start_reason.setText(reason or (
            "Real run: uses both subscriptions." if self.values["backend"] == "real" else
            "Fake run: no Codex calls or credentials. Acceptance still requires configured tests."))
        self.start_button.setToolTip(reason)
        revision_ok = bool(self.snapshot and self.snapshot.status == "completed" and self.details
                           and Path(self.details["project_root"]) == self.project)
        self.revision_button.setEnabled(can_start and revision_ok)
        self.revision_button.setToolTip(reason or ("" if revision_ok else "Select a terminal run from this project's history."))
        work_free = "work" not in self.adapter.jobs and not self.starting and not self.closing
        self.connect_button.setEnabled(bool(work_free and self.controller and hasattr(self.controller, "connect_accounts")))
        self.baseline_button.setEnabled(bool(work_free and self.project_check and self.project_check["can_snapshot"]))
        for button in (self.open_project, self.create_project, self.save_settings):
            button.setEnabled(work_free and (button is self.save_settings or self.controller is not None))
        vscode = bool(self.dependencies and self.dependencies["vscode"]["available"])
        for button, path in ((self.open_code, self.project),
                             (self.result_button, self.snapshot.result_dir if self.snapshot else None)):
            button.setEnabled(bool(work_free and vscode and path))
            button.setToolTip("" if vscode and path else "VS Code or the requested folder is unavailable. See Projects diagnostics.")
        self.artifact_button.setEnabled(work_free and self.artifact_list.currentItem() is not None)
        self.restart_button.setEnabled(bool(work_free and self.reconciled and self.snapshot and self.snapshot.status == "stopped" and self.snapshot.stage != "awaiting_task" and hasattr(self.controller, "resume_stopped")))
        self.authorize_button.setEnabled(bool(work_free and self.controller and self.project and self._commands()))
        self.authorize_button.setToolTip("Save commands and select a project before authorization.")
        live = bool(self.snapshot and self.snapshot.status not in TERMINAL)
        controls = self.details["controls"] if self.details else None
        stopped = bool(controls and controls["stop"]["requested"])
        available = live and self.reconciled and "control" not in self.adapter.jobs and not stopped and not self.closing
        for button in (self.pause_button, self.resume_button, self.stop_button, self.correction_button):
            button.setEnabled(available)
            button.setToolTip("Checkpoint action; active calls continue until completion or their deadline." if available
                              else "Select an active run; controls are unavailable after stop or terminal publication, or while a request is publishing.")
        self.correction_button.setEnabled(available and bool(self.correction_input.text().strip()))
        self.resume_button.setEnabled(available and bool(controls and controls["pause"]["requested"]))
        self._chat_enable()

    def start_run(self, revision=False):
        if self.confirming:
            return
        reason = self._start_blocker()
        if reason:
            self.notice.setText(reason)
            return
        if revision and not self.revision_button.isEnabled():
            return
        spec = RunSpec(self.project, self.prompt.toPlainText().strip(),
                       test_commands=self._commands(), max_call_seconds=self.values["max_call_seconds"],
                       review_rounds=self.values["review_rounds"])
        controller, source = self.controller, self.run_id
        self.confirming = True
        try:
            confirmed = self.confirm_start(spec, source if revision else None)
        finally:
            self.confirming = False
        if not confirmed:
            self.notice.setText("Start cancelled. No work was launched.")
            return
        call = (lambda: controller.start_revision(spec, source)) if revision else (lambda: controller.start(spec))
        self.starting = True
        if not self._submit("start", call):
            self.starting = False
        self._enable()

    def confirm_start(self, spec, source=None):
        return StartDialog(spec, self.values["backend"], source, self).exec() == QDialog.DialogCode.Accepted

    def select_run(self, run_id):
        if self.starting or self.closing:
            return
        self.run_epoch += 1
        self.run_id = run_id
        self.snapshot = self.details = None
        self.offset, self.generation = 0, -1
        for widget in (*self.feeds.values(), self.timeline, self.artifact_list, self.corrections_list):
            widget.clear()
        self.result_text.clear()
        self.run_status.setText(f"Loading {run_id}…")
        self.call_status.setText("Loading active calls…")
        for widget in self.usage_labels.values():
            widget.setText("Reported tokens: Unavailable")
        self._enable()

    def _control(self, method, *args):
        if not self.reconciled or not self.controller or not self.snapshot or self.snapshot.status in TERMINAL:
            return
        controller, run_id = self.controller, self.run_id
        self._submit("control:" + method, lambda: getattr(controller, method)(run_id, *args), control=True)

    def _correction(self):
        text = self.correction_input.text().strip()
        if text:
            self.correction_submitted = text
            self._control("queue_correction", text)

    def _history(self):
        if self.project and self.controller:
            self.history_due = True
            self.refresh()

    def _open_project_code(self):
        if self.project and self.controller:
            controller, path = self.controller, self.project
            self._submit("open", lambda: controller.open_in_vscode(path))

    def _open_result(self):
        if self.snapshot and self.snapshot.result_dir:
            controller, path = self.controller, self.snapshot.result_dir
            self._submit("open", lambda: controller.open_in_vscode(path))

    def _resume_stopped(self):
        if not self.restart_button.isEnabled():
            return
        self.confirming = True
        try:
            answer = QMessageBox.question(self, "Resume saved task",
                f"Resume run {self.run_id} in {self.details['project_root']}?\n"
                f"Saved backend: {self.details['backend']}. Real runs use the saved account settings.\n"
                "Completed operations will be reused after the worktrees are verified. The original task continues; send new instructions through the message box after resuming.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        finally:
            self.confirming = False
        if answer == QMessageBox.StandardButton.Yes:
            controller, run_id = self.controller, self.run_id
            self.starting = True
            if not self._submit("start", lambda: controller.resume_stopped(run_id)):
                self.starting = False

    def _open_artifact(self):
        item = self.artifact_list.currentItem()
        if item and self.controller:
            controller, run_id, artifact_id = self.controller, self.run_id, item.data(Qt.ItemDataRole.UserRole)
            self._submit("open", lambda: controller.open_artifact(run_id, artifact_id))

    def _save_settings(self):
        if self.adapter.busy or self.starting or self.closing:
            self.notice.setText("Wait for the current operation before saving settings.")
            return
        values = dict(self.values)
        values["backend"] = self.backend.currentText()
        for key, field in self.settings_fields.items():
            values[key] = field.value() if isinstance(field, QSpinBox) else field.text().strip()
        values["test_commands"] = self.test_commands.toPlainText()
        values["auto_open"] = self.auto_open.isChecked()
        storage_changed = values["storage_root"] != self.values["storage_root"]
        if storage_changed and self.controller and (self.active or not self.reconciled):
            self.notice.setText("Storage cannot change until recovery succeeds and all unfinished runs terminate.")
            return
        # Controller construction can validate paths. Keep that work off the GUI.
        controller = self.controller
        def replacement():
            # Check the entire old index, including runs not selected in the UI.
            if storage_changed and controller:
                if any(s.status not in TERMINAL for s in controller.reconcile()):
                    raise RuntimeError("Storage cannot change while indexed runs are unfinished.")
            return self.factory(values), values
        self._submit("settings", replacement)

    def _inspect_authorization(self):
        controller, project, commands = self.controller, self.project, self._commands()
        self.inspection_context = (project, commands, controller)
        self._submit("inspect", lambda: controller.inspect_test_authorization(project, commands))

    def confirm_authorization(self, inspection):
        return AuthorizationDialog(inspection, self).exec() == QDialog.DialogCode.Accepted

    def refresh(self):
        if not self.controller or self.authorizing or self.confirming:
            return
        if self.closing:
            self._close_step()
            return
        if "work" in self.adapter.jobs:
            return
        if self.reconcile_due:
            self.reconcile_due = False
            self.reconciled = False
            self._submit("reconcile", self.controller.reconcile)
        elif self.dependencies is None:
            self._check_dependencies()
        elif self.project_check_due:
            self.project_check_due = False
            controller, project = self.controller, self.project
            self._submit("project_status", lambda: controller.project_status(project))
        elif self.auto_open_due:
            self.auto_open_due = False
            if self.dependencies["vscode"]["available"]:
                self._open_project_code()
            else:
                self.notice.setText("Project ready. VS Code is unavailable; see Projects diagnostics.")
        elif self.history_due and self.project:
            self.history_due = False
            controller, project = self.controller, self.project
            self._submit("history", lambda: controller.list_history(project))
        elif hasattr(self.controller, "open_chat") and not self.chat_loaded:
            self._open_chat()
        elif self.chat_link_due and "chat" not in self.adapter.jobs:
            controller = self.controller
            identity, run_id = self.chat_link_due
            self.chat_link_due = None
            self._submit("chat_link", lambda: controller.link_chat_run(identity, run_id))
        elif "chat" in self.adapter.jobs and self.chat_state:
            controller, identity = self.controller, self.chat_state["id"]
            self._submit("chat_poll", lambda: controller.read_chat(identity))
        elif self.run_id:
            controller, run_id, offset = self.controller, self.run_id, self.offset
            self._submit("poll", lambda: controller.poll(run_id, offset))

    def _reply(self, reply):
        if self._chat_reply(reply):
            return
        # Run switching invalidates run-specific results; settings/project switching
        # invalidates all results. Rejected batches never advance the event cursor.
        run_specific = reply.operation == "poll" or reply.operation.startswith("control:")
        if reply.identity[0] != self.epoch or (run_specific and reply.identity != self._identity()):
            self._enable()
            return
        op = reply.operation
        if op == "start":
            self.starting = False
        if reply.error:
            error = reply.error
            self.notice.setText(f"{getattr(error, 'code', 'operation_failed')}: {error}")
            if op == "project_status":
                self.project_check = {"ready": False, "can_snapshot": False, "detail": str(error)}
                self.project_readiness.setText(str(error))
            if op == "bootstrap":
                self.reconcile_due = False
                self.dependency_text.setPlainText(f"Backend unavailable: {error}\nRebuild or reinstall the complete AutoTeam Studio folder, including its worker.")
            if op == "start" and getattr(error, "run_id", None):
                self.select_run(error.run_id)
                self.history_due = True
            if op in {"poll", "reconcile", "close_reconcile"}:
                self.run_status.setText(f"Recovery blocked / refresh unavailable: {error}")
            if op in {"reconcile", "close_reconcile"}:
                self.reconciled = False
                self.reconcile_due = True
            if self.closing:
                if op == "close_stop" and getattr(error, "code", None) == "run_terminal":
                    self.notice.setText("Run finished before stop publication; reconciling before close.")
                else:
                    self.closing = False
                    self.notice.setText(f"Close deferred: {error}. Retry after resolving the blocker.")
            self._enable()
            return
        value = reply.value
        if op == "bootstrap":
            self.controller = value
        elif op == "dependencies":
            self.dependencies = value
            self.dependency_text.setPlainText("\n\n".join(
                f"{name}: {'Available' if info['available'] else 'Unavailable'} — {info['detail']}\n{info['path'] or ''}"
                for name, info in value.items()))
            if self.account_checks is None:
                for who in ("a", "b"):
                    info = value[f"account_{who}"]
                    self.account_labels[who].setText(f"Account {who.upper()}: {'Configured — check login' if info['available'] else 'Not configured'}\n{info['detail']}")
        elif op == "prepare":
            self.epoch += 1
            self.project = Path(value)
            self.project_check = {"ready": True, "can_snapshot": False, "detail": "New project and empty Git baseline ready."} if getattr(self, "preparing_create", False) else None
            self.project_check_due = self.project_check is None and hasattr(self.controller, "project_status")
            self.project_readiness.setText(self.project_check["detail"] if self.project_check else "Checking Git readiness…")
            self.authorization = None
            self.authorization_text.setText("Project changed. Inspect saved test commands for this repository.")
            self.project_label.setText(str(self.project))
            self.live_project.setText(f"Selected project: {self.project}")
            self.values["recent"] = [str(self.project)] + [p for p in self.values["recent"] if p != str(self.project)][:19]
            self.preferences.save(self.values)
            self.recents.clear()
            self.recents.addItems(self.values["recent"])
            self.select_run(None)
            self.run_status.setText("No run selected")
            self.history_due = not self.project_check_due
            self.auto_open_due = self.values["auto_open"]
            self.notice.setText("Project selected. Return to Team chat to talk or prepare a build.")
        elif op == "project_status":
            self.project_check = value
            self.project_readiness.setText(value["detail"])
            self.history_due = value["ready"]
        elif op == "accounts":
            self.account_checks = value
            if self.dependencies:
                self.dependencies.update(value)
            for who in ("a", "b"):
                info = value[f"account_{who}"]
                self.account_labels[who].setText(f"Account {who.upper()}: {'Connected' if info['available'] else 'Needs attention'}\n{info['detail']}")
            self.notice.setText("Saved account checks finished. No development work was started.")
        elif op == "baseline_preview":
            self.authorizing = True
            try:
                confirmed = self.confirm_baseline(value)
            finally:
                self.authorizing = False
            if confirmed:
                controller, project = self.controller, Path(value["root"])
                self._submit("baseline_saved", lambda: controller.save_baseline(project, value["inspection_id"]))
        elif op == "baseline_saved":
            self.project_check_due = True
            self.authorization = None
            self.notice.setText("Local baseline saved. Project files and existing history are preserved.")
        elif op == "start":
            self.active.add(value)
            self.select_run(value)
            self.history_due = True
            self.tabs.setCurrentIndex(1)
        elif op == "poll":
            self.apply_poll(value)
        elif op in {"history", "reconcile", "close_reconcile"}:
            if op in {"reconcile", "close_reconcile"}:
                self.reconciled = True
                self.reconcile_due = False
            for snapshot in value:
                if snapshot.status in TERMINAL:
                    self.active.discard(snapshot.run_id)
                else:
                    self.active.add(snapshot.run_id)
            if op in {"history", "reconcile"}:
                self.history.clear()
                for snapshot in value:
                    item = QListWidgetItem(f"{snapshot.run_id} — {snapshot.status} — {snapshot.error or snapshot.stage}")
                    item.setData(Qt.ItemDataRole.UserRole, snapshot.run_id)
                    self.history.addItem(item)
            if op == "close_reconcile":
                # Reconciliation must include all unfinished indexed runs.
                self.active = {s.run_id for s in value if s.status not in TERMINAL}
                if not self.active and not self.adapter.busy:
                    self.allow_close = True
                    self.close()
        elif op.startswith("control:"):
            self.notice.setText("Request published. Waiting for durable checkpoint acknowledgment.")
            if op == "control:queue_correction" and self.correction_input.text().strip() == self.correction_submitted:
                self.correction_input.clear()
                self.corrections_list.addItem(f"{self.correction_submitted} | A: pending | B: pending")
                self.corrections_list.show()
        elif op == "settings":
            storage_changed = self.values["storage_root"] != value[1]["storage_root"]
            self.controller, self.values = value
            self.chat_loaded = False
            self.chat_state = None
            self.preferences.save(self.values)
            self.epoch += 1
            self.authorization = None
            self.dependencies = None
            self.account_checks = None
            for who in ("a", "b"):
                self.account_labels[who].setText(f"Account {who.upper()}: settings changed — check again")
            self.reconciled = False
            self.reconcile_due = True
            if storage_changed:
                self.select_run(None)
                self.history.clear()
                self.active.clear()
                self.history_due = bool(self.project)
            self.authorization_text.setText("Settings changed. Inspect test authorization again.")
            self.notice.setText("Settings saved for new launches. Existing runs keep their launch settings.")
        elif op == "inspect":
            self.authorizing = True
            try:
                confirmed = self.confirm_authorization(value)
            finally:
                self.authorizing = False
            if confirmed:
                project, commands, controller = self.inspection_context
                self._submit("grant", lambda: controller.grant_test_authorization(project, commands, value["inspection_id"]))
        elif op == "grant":
            self.authorization = value
            self.authorization_text.setText("Exact saved test policy authorized." if value["authorized"] else value["notice"])
        self._enable()

    def apply_poll(self, response):
        snapshot, details = response["snapshot"], response["details"]
        generation = response["generation"]
        if snapshot.run_id != self.run_id or generation < self.generation:
            return
        if details["generation"] != generation or response["next_offset"] < self.offset:
            self.notice.setText("Incoherent backend response rejected; cursor preserved.")
            return
        self.generation = generation
        self.snapshot, self.details = snapshot, details
        if self.chat_state and hasattr(self.controller, "link_chat_run"):
            marker = (self.chat_state["id"], snapshot.run_id, snapshot.status)
            if marker not in self.chat_linked and (snapshot.status in TERMINAL or snapshot.status == "running"):
                self.chat_linked.add(marker)
                self.chat_link_due = marker[:2]
        self.offset = response["next_offset"]
        was_active = snapshot.run_id in self.active
        if snapshot.status in TERMINAL:
            self.active.discard(snapshot.run_id)
            if was_active:
                self.history_due = True
        else:
            self.active.add(snapshot.run_id)
        self.run_status.setText(f"{snapshot.run_id} — {snapshot.status} — {snapshot.stage}\n{snapshot.error or details['reason_code'] or ''}")
        calls = details["active_calls"]
        self.call_status.setText("\n".join(
            f"Agent {c['account'].upper()} — {c['stage']} — deadline {c['deadline']} — last reported activity {c['last_activity_at'] or 'Unavailable'}"
            for c in calls) or "No active calls")
        usage = details["usage"]
        coverage = f"{usage['known_calls']}/{usage['total_calls']} calls reported"
        if usage["known_calls"] < usage["total_calls"]:
            coverage += " — incomplete"
        for account in ("a", "b"):
            tokens = getattr(snapshot, f"reported_tokens_{account}")
            cached = usage[f"cached_input_{account}"]
            self.usage_labels[account].setText(
                f"Reported tokens: {tokens if tokens is not None else 'Unavailable'}; {coverage}\n"
                f"Cached input: {cached if cached is not None else 'Unavailable'} (not added again)")
        follow_widgets = [widget for widget in (*self.feeds.values(), self.timeline)
                          if widget.verticalScrollBar().value() >= widget.verticalScrollBar().maximum() - 2]
        for event in response["events"]:
            if event.run_id != self.run_id:
                continue
            self._conversation_event(event)
            payload = event.payload if isinstance(event.payload, dict) else {}
            fragments = []
            if payload.get("paths"):
                fragments.append("Files: " + ", ".join(map(str, payload["paths"][:12])))
            if payload.get("exit_code") is not None:
                fragments.append("Exit: " + str(payload["exit_code"]))
            if payload.get("state"):
                fragments.append("State: " + str(payload["state"]))
            if payload.get("code"):
                fragments.append("Code: " + str(payload["code"]))
            suffix = " | " + " | ".join(fragments) if fragments else ""
            if payload.get("output"):
                suffix += "\n" + str(payload["output"])
            text = f"{event.timestamp} | {event.agent.upper()} | {event.kind} | {event.message}{suffix}"
            if event.agent in self.feeds:
                bounded_append(self.feeds[event.agent], text)
            bounded_append(self.timeline, text)
        # One layout/scroll per batch, rather than per streamed record.
        if response["events"]:
            for widget in follow_widgets:
                widget.scrollToBottom()
        controls = details["controls"]
        states = []
        for name in ("pause", "stop"):
            control = controls[name]
            state = "acknowledged" if control["acknowledged"] else "requested" if control["requested"] else "not requested"
            states.append(f"{name.capitalize()}: {state}")
        self.control_status.setText("; ".join(states) + ". Active calls finish before checkpoint actions.")
        self.corrections_list.clear()
        for correction in controls["corrections"]:
            self.corrections_list.addItem(f"{correction['text']} | A: {correction['a']['state']} | B: {correction['b']['state']}")
            if self.chat_run == self.run_id:
                delivery = f"A: {correction['a']['state']} · B: {correction['b']['state']}"
                self.conversation.put("guidance/" + correction["id"], "you", correction["text"], delivery, correction["created_at"])
        self.corrections_list.setVisible(bool(controls["corrections"]))
        self.corrections_list.setToolTip("Delivered means the prompt was submitted, not that the agent complied. Uncertain delivery is not silently retried.")
        selected = self.artifact_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.artifact_list.clear()
        for artifact in response["artifacts"]:
            item = QListWidgetItem(f"{artifact['label']} — {artifact['kind']} — {artifact['path']}")
            item.setData(Qt.ItemDataRole.UserRole, artifact["id"])
            self.artifact_list.addItem(item)
            if artifact["id"] == selected_id:
                self.artifact_list.setCurrentItem(item)
        lines = [f"Status: {snapshot.status}", f"Backend: {details['backend']}",
                 f"Project: {details['project_root']}", f"Source run: {details['source_run_id']}",
                 f"Result branch: {details['result_branch']}", f"Result SHA: {details['result_sha']}",
                 f"Reason: {details['reason_code'] or snapshot.error or ''}"]
        lines.extend(f"{name} worktree: {path or 'Unavailable'}" for name, path in details["worktrees"].items())
        lines.extend(f"{gate['name']}: {gate['state']} — SHA {gate['sha']} — {gate['explanation']}"
                     for gate in details["gates"])
        self.result_text.setPlainText("\n".join(lines))
        self._enable()

    def closeEvent(self, event):
        if self.allow_close or (self.controller is None and not self.adapter.busy) or (self.reconciled and not self.active and not self.adapter.busy and not self.starting and not self.reconcile_due):
            self.timer.stop()
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        answer = QMessageBox.question(self, "Stop before closing",
            "Cancel chat replies, request checkpoint stop for unfinished project runs, and close after they finish? Active project calls can last until their deadlines.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.closing = True
            if "chat" in self.adapter.jobs:
                self._cancel_chat()
            self.close_stops.clear()
            self.timer.start()
            self.notice.setText("Waiting for checkpoint stop before closing. No detached continuation is offered.")
            self._enable()

    def _close_step(self):
        if self.adapter.busy or self.starting:
            return
        pending = self.active - self.close_stops
        if pending:
            run_id = next(iter(pending))
            controller = self.controller
            self.close_stops.add(run_id)
            self._submit("close_stop", lambda: controller.stop_at_checkpoint(run_id), control=True)
        else:
            self._submit("close_reconcile", self.controller.reconcile)

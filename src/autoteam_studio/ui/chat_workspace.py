"""Conversation-first workspace, with project execution as an explicit transition."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QCheckBox,
    QComboBox, QSplitter, QListWidget, QDialog, QDialogButtonBox, QPlainTextEdit,
)
from .conversation import ConversationView, Composer, theme


def caption(text="", name="muted"):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setObjectName(name)
    return result


class ChatWorkspace:
    def _live_view(self):
        self.chat_state = None
        self.chat_loaded = False
        self.chat_loading = False
        self.chat_submitted = None
        self.chat_run = None
        self.chat_link_due = None
        self.chat_linked = set()
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)
        self.tabs.addTab(page, "Team chat")
        top = QHBoxLayout()
        top.addWidget(caption("Your team", "title"))
        top.addStretch()
        self.theme_choice = QComboBox()
        self.theme_choice.addItems(["Soft light", "Quiet dark"])
        self.theme_choice.setCurrentText(self.values["theme"])
        self.theme_choice.currentTextChanged.connect(self._set_theme)
        self.theme_choice.setAccessibleName("Color theme")
        top.addWidget(self.theme_choice)
        self.casual_toggle = QCheckBox("Just chat")
        self.casual_toggle.setChecked(self.values["casual_chat"])
        self.casual_toggle.setToolTip("A phone-like group chat. Build actions are hidden in this mode.")
        self.casual_toggle.toggled.connect(self._chat_mode)
        top.addWidget(self.casual_toggle)
        outer.addLayout(top)
        center = QHBoxLayout()
        center.addStretch()
        self.chat_panel = QWidget()
        self.chat_panel.setObjectName("page")
        self.chat_panel.setMaximumWidth(1100)
        panel = QVBoxLayout(self.chat_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(10)
        center.addWidget(self.chat_panel, 1)
        center.addStretch()
        outer.addLayout(center, 1)
        header = QFrame()
        header.setObjectName("card")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        row.addWidget(caption("You  +  Agent A  +  Agent B", "group"), 1)
        self.chat_connect = self._button("Connect accounts", self._connect_accounts, row)
        header_layout.addLayout(row)
        self.chat_accounts = caption("Check your two saved accounts to start talking.")
        header_layout.addWidget(self.chat_accounts)
        self.chat_mode_text = caption("Chat freely. A build request becomes a brief for you to review.")
        self.chat_mode_text.hide()
        panel.addWidget(header)
        history_row = QHBoxLayout()
        self.chat_history = QComboBox()
        self.chat_history.setMinimumWidth(130)
        self.chat_history.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chat_history.setAccessibleName("Saved conversations")
        self.chat_history.activated.connect(self._choose_chat)
        history_row.addWidget(self.chat_history, 1)
        self.new_chat = self._button("New chat", lambda: self._open_chat(new=True), history_row)
        panel.addLayout(history_row)
        self.live_project = caption("No project selected · Choose a folder when you want to build.")
        self.live_project.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        panel.addWidget(self.live_project)
        self.conversation = ConversationView()
        panel.addWidget(self.conversation, 1)
        self.conversation.put("welcome", "system", "A place to talk, think, and build together.\nSay hello to both agents, or describe something you want to make.")
        self.brief_card = QFrame()
        self.brief_card.setObjectName("card")
        brief_box = QHBoxLayout(self.brief_card)
        brief_box.addWidget(caption("The team has a project brief ready for your review."), 1)
        self.brief_button = self._button("Review brief…", self._review_brief, brief_box)
        panel.addWidget(self.brief_card)
        self.brief_card.hide()

        self.run_bar = QWidget()
        run_layout = QVBoxLayout(self.run_bar)
        run_layout.setContentsMargins(0, 0, 0, 0)
        self.run_status = caption("No run selected")
        self.run_status.setMaximumHeight(44)
        row = QHBoxLayout()
        row.addWidget(self.run_status, 1)
        self.pause_button = self._button("Pause", lambda: self._control("pause_next_tasks"), row)
        self.resume_button = self._button("Resume", lambda: self._control("resume"), row)
        self.stop_button = self._button("Stop", lambda: self._control("stop_at_checkpoint"), row)
        self.activity_toggle = QCheckBox("Work details")
        row.addWidget(self.activity_toggle)
        self.view_results_button = self._button("View results", lambda: self.tabs.setCurrentIndex(2), row)
        run_layout.addLayout(row)
        self.control_status = caption("Controls take effect after active calls finish.")
        self.control_status.hide()
        panel.addWidget(self.run_bar)
        self.corrections_list = QListWidget()
        self.corrections_list.setMaximumHeight(65)
        self.corrections_list.hide()
        panel.addWidget(self.corrections_list)

        self.activity_dialog = QDialog(self)
        self.activity_dialog.setWindowTitle("Team work details")
        self.activity_dialog.resize(900, 650)
        dialog_layout = QVBoxLayout(self.activity_dialog)
        self.activity_panel = QWidget()
        activity = QVBoxLayout(self.activity_panel)
        activity.setContentsMargins(0, 0, 0, 0)
        self.call_status = caption("No active calls")
        activity.addWidget(self.control_status)
        self.control_status.show()
        activity.addWidget(self.call_status)
        splitter = QSplitter()
        self.feeds, self.usage_labels = {}, {}
        for who in ("a", "b"):
            agent = QWidget()
            box = QVBoxLayout(agent)
            self.usage_labels[who] = caption("Reported tokens: Unavailable")
            box.addWidget(self.usage_labels[who])
            self.feeds[who] = QListWidget()
            self.feeds[who].setWordWrap(True)
            box.addWidget(self.feeds[who])
            splitter.addWidget(agent)
        activity.addWidget(splitter, 2)
        self.timeline = QListWidget()
        self.timeline.setWordWrap(True)
        activity.addWidget(self.timeline, 1)
        dialog_layout.addWidget(self.activity_panel)
        self.activity_toggle.toggled.connect(self.activity_dialog.setVisible)
        self.activity_dialog.finished.connect(lambda *_: self.activity_toggle.setChecked(False))

        composer = QFrame()
        composer.setObjectName("composer")
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(12, 10, 12, 10)
        self.prompt = Composer()
        self.prompt.setPlaceholderText("Message both agents…")
        self.prompt.setFixedHeight(64)
        self.prompt.textChanged.connect(self._enable)
        self.prompt.submitted.connect(self._send_chat)
        compose.addWidget(self.prompt)
        self.correction_input = Composer()
        self.correction_input.setFixedHeight(64)
        self.correction_input.setPlaceholderText("Send guidance to the working team…")
        self.correction_input.textChanged.connect(self._enable)
        self.correction_input.submitted.connect(self._correction)
        compose.addWidget(self.correction_input)
        row = QHBoxLayout()
        row.addWidget(caption("Enter to send · Shift+Enter for a new line"), 1)
        self.cancel_chat_button = self._button("Cancel replies", self._cancel_chat, row)
        self.chat_send = self._button("Send", self._send_chat, row)
        self.chat_send.setObjectName("primary")
        self.correction_button = self._button("Send guidance", self._correction, row)
        self.correction_button.setObjectName("primary")
        compose.addLayout(row)
        panel.addWidget(composer)
        self.chat_status = caption("Messages use both connected accounts. Nothing starts on its own.")
        panel.addWidget(self.chat_status)
        self.build_actions = QWidget()
        actions = QVBoxLayout(self.build_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.start_button = self._button("Build from this message…", lambda: self.start_run(False), row)
        self.revision_button = self._button("Revise completed project…", lambda: self.start_run(True), row)
        self.setup_button = self._button("Project setup", lambda: self.tabs.setCurrentIndex(0), row)
        actions.addLayout(row)
        self.start_reason = caption()
        self.start_reason.setMaximumHeight(22)
        actions.addWidget(self.start_reason)
        panel.addWidget(self.build_actions)
        self.chat_panel.setMaximumWidth(600 if self.casual_toggle.isChecked() else 1100)

    def _set_theme(self, value):
        self.values["theme"] = value
        self.setStyleSheet(theme(value == "Quiet dark"))
        self.preferences.save(self.values)

    def _chat_mode(self, casual):
        self.values["casual_chat"] = casual
        self.preferences.save(self.values)
        self.chat_panel.setMaximumWidth(600 if casual else 1100)
        self.chat_mode_text.setText("Just the three of you. No project work in this mode." if casual else
                                   "Chat freely. A build request becomes a brief for you to review.")
        self._enable()

    def _chat_enable(self):
        live = bool(self.snapshot and self.snapshot.status not in {"completed", "failed", "stopped"})
        busy = "chat" in self.adapter.jobs
        connected = self.values["backend"] == "fake" or bool(self.account_checks and all(v["available"] for v in self.account_checks.values()))
        can_chat = bool(self.controller and hasattr(self.controller, "chat_turn") and self.chat_state and
                        connected and not live and not busy and not self.starting and not self.closing and not self.chat_loading)
        self.chat_send.setEnabled(can_chat and bool(self.prompt.text().strip()))
        self.chat_send.setToolTip("" if connected else "Connect both saved accounts first.")
        self.cancel_chat_button.setVisible(busy)
        self.cancel_chat_button.setEnabled(busy and not self.closing)
        self.new_chat.setEnabled(bool(self.controller and hasattr(self.controller, "open_chat") and
                                     "work" not in self.adapter.jobs and not busy and not self.chat_loading and not live and not self.closing))
        self.chat_history.setEnabled(self.new_chat.isEnabled())
        self.chat_connect.setEnabled(self.connect_button.isEnabled() and not busy)
        self.connect_button.setEnabled(self.connect_button.isEnabled() and not busy)
        self.casual_toggle.setEnabled(not busy and not live and not self.starting)
        self.prompt.setVisible(not live)
        self.correction_input.setVisible(live)
        self.chat_send.setVisible(not live)
        self.correction_button.setVisible(live)
        casual = self.casual_toggle.isChecked()
        self.build_actions.setVisible(not casual and not live)
        self.live_project.setVisible(not casual)
        self.run_bar.setVisible(bool(self.run_id))
        for button in (self.pause_button, self.resume_button, self.stop_button):
            button.setVisible(live)
        self.view_results_button.setVisible(bool(self.snapshot and not live))
        self.brief_card.setVisible(bool(self.chat_state and self.chat_state.get("build_prompt") and not casual and not live and not busy))
        if busy:
            for widget in (self.start_button, self.revision_button, self.open_project, self.create_project, self.save_settings):
                widget.setEnabled(False)
        if live:
            self.chat_status.setText("Guidance is queued for each agent's next call. Pause and Stop wait for a checkpoint.")
        elif not connected:
            self.chat_status.setText("Connect your saved accounts above. No project folder is needed to chat.")
        elif not busy and not self.chat_submitted:
            if self.chat_state and self.chat_state["messages"]:
                replies = [m for m in self.chat_state["messages"][-3:] if m["speaker"] in {"a", "b"}]
                self.chat_status.setText(" · ".join(f"Agent {m['speaker'].upper()}: {m['state']}" for m in replies))
            else:
                self.chat_status.setText("Both agents reply to each message. Recent conversation is shared with both.")
        if self.values["backend"] == "fake":
            self.chat_accounts.setText("Agent A · simulated    /    Agent B · simulated")
        else:
            parts = []
            for who in ("a", "b"):
                info = (self.account_checks or {}).get(f"account_{who}", {})
                parts.append(f"Agent {who.upper()} · " + ("connected" if info.get("available") else "not connected"))
            self.chat_accounts.setText("    /    ".join(parts))

    def _open_chat(self, identity=None, new=False):
        if not self.controller or not hasattr(self.controller, "open_chat") or "chat" in self.adapter.jobs:
            return
        controller = self.controller
        if self._submit("chat_open", lambda: controller.open_chat(identity, new)):
            self.chat_loading = True
            self._enable()

    def _choose_chat(self, index):
        identity = self.chat_history.itemData(index)
        if identity and (not self.chat_state or identity != self.chat_state["id"]):
            self._open_chat(identity)

    def _send_chat(self):
        if not self.chat_send.isEnabled():
            return
        controller, identity = self.controller, self.chat_state["id"]
        text, casual = self.prompt.text().strip(), self.casual_toggle.isChecked()
        if self.adapter.submit("chat_send", self._identity(), lambda: controller.chat_turn(identity, text, casual), chat=True):
            self.chat_submitted = text
            self.chat_status.setText("Sending to Agent A and Agent B…")
            self._enable()

    def _cancel_chat(self):
        if self.controller and hasattr(self.controller, "cancel_chat"):
            self.controller.cancel_chat()  # Sets a local Event; no I/O on the GUI thread.
            self.chat_status.setText("Cancelling the two replies…")

    def _chat_reply(self, reply):
        if not reply.operation.startswith("chat_"):
            return False
        if reply.operation == "chat_open":
            self.chat_loading = False
        if reply.error:
            self.chat_status.setText(str(reply.error))
            self.notice.setText(str(reply.error))
            if reply.operation == "chat_send":
                self.chat_submitted = None
            if reply.operation == "chat_open":
                self.chat_loaded = True  # Avoid retry loops on damaged storage.
            self._enable()
            return True
        if reply.operation == "chat_open":
            value = reply.value
            self.chat_loaded = True
            self.chat_run = None
            self.chat_submitted = None
            self.conversation.clear()
            self.chat_history.blockSignals(True)
            self.chat_history.clear()
            current = value["conversation"]
            entries = [current] + [h for h in value["history"] if h["id"] != current["id"]]
            for record in entries:
                self.chat_history.addItem(record["title"], record["id"])
            self.chat_history.blockSignals(False)
            self._show_chat(current)
            if not current["messages"]:
                self.conversation.put("welcome", "system", "Say hello, share an idea, or ask us something. Both agents will reply here.")
        elif reply.operation in {"chat_send", "chat_poll"}:
            if self.chat_state and reply.value["id"] == self.chat_state["id"]:
                self._show_chat(reply.value)
            if reply.operation == "chat_send":
                delivered = [m for m in reply.value["messages"] if m["turn"] == reply.value["turn"] and m["speaker"] != "you"]
                self.chat_status.setText(" · ".join(f"Agent {m['speaker'].upper()}: {m['state']}" for m in delivered))
                if self.prompt.text().strip() == self.chat_submitted:
                    self.prompt.clear()
                self.chat_submitted = None
        elif reply.operation == "chat_link":
            if self.chat_state and reply.value["id"] == self.chat_state["id"]:
                self.chat_state = reply.value
        self._enable()
        return True

    def _show_chat(self, state):
        if self.chat_state and state["id"] == self.chat_state["id"] and state.get("revision", 0) < self.chat_state.get("revision", 0):
            return
        if self.chat_run is not None:
            self.conversation.clear()
            self.chat_run = None
        self.chat_state = state
        for item in state["messages"][-200:]:
            self.conversation.put(item["id"], item["speaker"], item["text"], item["state"], item["timestamp"])
        index = self.chat_history.findData(state["id"])
        if index >= 0:
            self.chat_history.setItemText(index, state["title"])

    def _review_brief(self):
        if not self.chat_state or not self.chat_state.get("build_prompt") or self.casual_toggle.isChecked():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Your project brief")
        dialog.resize(640, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(caption("Edit the brief, then choose a project and confirm the build.", "group"))
        edit = QPlainTextEdit(self.chat_state["build_prompt"])
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use this brief")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and edit.toPlainText().strip():
            self.prompt.setPlainText(edit.toPlainText())
            self.notice.setText("Brief ready. Choose the project and test settings, then select Build from this message.")
            if not self.project:
                self.tabs.setCurrentIndex(0)

    def _conversation_event(self, event):
        if event.kind not in {"message", "stage", "status", "file_change", "error"}:
            return
        if event.kind == "message" and event.message.endswith("invocation started"):
            return
        if self.chat_run != self.run_id:
            self.conversation.clear()
            self.chat_run = self.run_id
        text = event.message
        if event.kind == "message":
            try:
                value = json.loads(text)
                text = value.get("message", value.get("summary", text)) if isinstance(value, dict) else text
            except ValueError:
                pass
        if event.kind == "stage":
            text = "Team stage · " + text.replace("_", " ").capitalize()
        self.conversation.put(f"{event.timestamp}/{event.agent}/{event.kind}/{text[:60]}", event.agent, text,
                              timestamp=event.timestamp)

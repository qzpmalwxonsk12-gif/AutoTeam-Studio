"""Readable group-chat bubbles and a quiet, consistent application palette."""
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPlainTextEdit, QListWidget, QListWidgetItem, QAbstractItemView


def theme(dark=False):
    colors = ({"bg": "#202725", "surface": "#29322e", "input": "#242d29", "text": "#e1e8e2",
               "muted": "#a6b5ac", "line": "#45574b", "hover": "#35463b", "accent": "#9ccbb0",
               "selected": "#385644", "user": "#354e40", "bot": "#2d3831"} if dark else
              {"bg": "#eeefea", "surface": "#f8f9f4", "input": "#fcfcf8", "text": "#273b32",
               "muted": "#5c6c63", "line": "#c8d1c7", "hover": "#e3e9df", "accent": "#285c43",
               "selected": "#dce8dc", "user": "#dcebdc", "bot": "#f8f9f4"})
    return """
        QWidget { font-family: 'Segoe UI'; font-size: 11pt; color: %(text)s; }
        QMainWindow, QDialog, QScrollArea, QWidget#page { background: %(bg)s; }
        QLabel { background: transparent; }
        QLabel#title { font-size: 18pt; font-weight: 600; }
        QLabel#muted, QLabel#meta { color: %(muted)s; font-size: 10pt; }
        QTabWidget::pane { border: none; background: %(bg)s; }
        QTabBar::tab { background: %(bg)s; padding: 12px 24px; border: none; color: %(muted)s; }
        QTabBar::tab:selected { color: %(accent)s; background: %(selected)s; border-bottom: 2px solid %(accent)s; }
        QPushButton, QToolButton { background: %(surface)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px 14px; }
        QPushButton:hover:enabled, QToolButton:hover { background: %(hover)s; }
        QPushButton:disabled { color: %(muted)s; border-color: %(line)s; }
        QPushButton#primary:enabled { background: #356447; color: #f7faf5; border-color: #356447; font-weight: 600; }
        QPlainTextEdit, QLineEdit, QListWidget, QComboBox, QSpinBox {
            background: %(input)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px;
            selection-background-color: %(selected)s; selection-color: %(text)s;
        }
        QPlainTextEdit:focus, QLineEdit:focus { border-color: %(accent)s; }
        QListWidget::item { padding: 8px; }
        QListWidget::item:selected { background: %(selected)s; color: %(text)s; border-radius: 5px; }
        QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; selection-background-color: %(selected)s; }
        QListWidget#conversation { background: %(bg)s; border: none; padding: 0; }
        QListWidget#conversation::item { background: transparent; padding: 0; }
        QWidget#thread { background: %(bg)s; }
        QFrame#bubble { background: %(bot)s; border: 1px solid %(line)s; border-radius: 12px; }
        QFrame#bubble[speaker='you'] { background: %(user)s; }
        QFrame#bubble[speaker='system'] { background: %(hover)s; border: none; }
        QFrame#composer, QFrame#card { background: %(surface)s; border: 1px solid %(line)s; border-radius: 12px; }
        QScrollBar:vertical { width: 10px; background: transparent; }
        QScrollBar::handle:vertical { background: %(line)s; border-radius: 5px; min-height: 25px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        QStatusBar { background: %(surface)s; border-top: 1px solid %(line)s; }
        QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(line)s; padding: 6px; }
    """ % colors


class Composer(QPlainTextEdit):
    submitted = Signal()

    def text(self):
        return self.toPlainText()

    def setText(self, value):
        self.setPlainText(value)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class BubbleRow(QWidget):
    """Reserve the wrapped text height before the scrolling layout can compress it."""
    def fit(self, available_width):
        if not hasattr(self, "message"):
            return
        width = max(80, min(760, int((available_width - 20) * 5 / 6)) - 34)
        height = max(self.message.fontMetrics().height(), self.message.heightForWidth(width))
        self.message.setFixedHeight(height)
        return height + self.title.sizeHint().height() + (self.meta.sizeHint().height() + 6 if not self.meta.isHidden() else 0) + 30


class ConversationView(QListWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("conversation")
        self.setUniformItemSizes(False)
        self.setSpacing(6)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bubbles = {}
        self.items = {}
        self.follow = True
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.verticalScrollBar().sliderMoved.connect(lambda value: setattr(self, "follow", value >= self.verticalScrollBar().maximum() - 35))

    def _range_changed(self, minimum, maximum):
        if self.follow:
            self.verticalScrollBar().setValue(maximum)

    def wheelEvent(self, event):
        self.follow = False
        super().wheelEvent(event)
        self.follow = self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() - 35

    def clear(self):
        super().clear()
        self.bubbles.clear()
        self.items.clear()
        self.follow = True

    def _size_row(self, identity):
        row = self.bubbles[identity][2]
        height = row.fit(self.viewport().width())
        self.items[identity].setSizeHint(QSize(0, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for identity in getattr(self, "bubbles", {}):
            self._size_row(identity)

    def put(self, identity, speaker, text, status="", timestamp=""):
        following = self.follow
        if identity not in self.bubbles:
            row = BubbleRow()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            bubble = QFrame()
            bubble.setObjectName("bubble")
            bubble.setProperty("speaker", speaker)
            bubble.setMaximumWidth(760)
            box = QVBoxLayout(bubble)
            box.setContentsMargins(16, 11, 16, 11)
            box.setSpacing(6)
            title = QLabel({"you": "You", "a": "Agent A", "b": "Agent B", "system": "AutoTeam"}.get(speaker, speaker))
            title.setStyleSheet("font-weight: 600;")
            box.addWidget(title)
            message = QLabel()
            message.setWordWrap(True)
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(message)
            meta = QLabel()
            meta.setObjectName("meta")
            box.addWidget(meta)
            row.message, row.title, row.meta = message, title, meta
            if speaker == "you":
                layout.addStretch(1)
            layout.addWidget(bubble, 5)
            if speaker != "you":
                layout.addStretch(1)
            item = QListWidgetItem()
            self.addItem(item)
            self.setItemWidget(item, row)
            self.items[identity] = item
            self.bubbles[identity] = (message, meta, row)
            if len(self.bubbles) > 250:
                oldest = next(iter(self.bubbles))
                del self.bubbles[oldest]
                del self.items[oldest]
                self.takeItem(0)
        message, meta, row = self.bubbles[identity]
        message.setText(text or {"pending": "Waiting…", "submitting": "Sending…", "awaiting_reply": "Thinking…"}.get(status, ""))
        if timestamp:
            from PySide6.QtCore import QDateTime
            stamp = QDateTime.fromString(timestamp, Qt.DateFormat.ISODateWithMs).toLocalTime().toString("hh:mm")
        else:
            stamp = ""
        meta.setText(" · ".join(x for x in (stamp, status.replace("_", " ")) if x))
        meta.setVisible(bool(meta.text()))
        self._size_row(identity)
        if following:
            QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

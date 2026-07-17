"""Main window: sidebar navigation + stacked views + runtime status pill."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hearth")
        self.resize(1080, 740)

        root = QWidget()
        root.setObjectName("root")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 6, 8, 12)
        side.setSpacing(2)

        title = QLabel("Hearth")
        title.setObjectName("appTitle")
        side.addWidget(title)

        self._stack = QStackedWidget()
        self._nav_buttons: list[QPushButton] = []
        outer.addWidget(sidebar)
        outer.addWidget(self._stack, stretch=1)

        self.status_pill = QLabel("Model: checking…")
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_pill.setWordWrap(True)

        self._side_layout = side
        self.setCentralWidget(root)

    def add_view(self, name: str, widget: QWidget) -> None:
        index = self._stack.count()
        self._stack.addWidget(widget)
        button = QPushButton(name)
        button.setProperty("nav", True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, i=index: self._select(i))
        self._side_layout.addWidget(button)
        self._nav_buttons.append(button)
        if index == 0:
            self._select(0)

    def finish_sidebar(self) -> None:
        self._side_layout.addStretch(1)
        self._side_layout.addWidget(self.status_pill)

    def _select(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, button in enumerate(self._nav_buttons):
            button.setProperty("active", i == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_status(self, text: str) -> None:
        self.status_pill.setText(text)

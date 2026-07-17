"""Action history: every proposed action and what happened to it."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..storage.db import Database


class HistoryView(QWidget):
    def __init__(self, db: Database):
        super().__init__()
        self._db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        header = QHBoxLayout()
        title = QLabel("Action history")
        title.setProperty("h1", True)
        refresh = QPushButton("Refresh")
        refresh.setProperty("secondary", True)
        refresh.clicked.connect(self.refresh)
        header.addWidget(title, stretch=1)
        header.addWidget(refresh)
        layout.addLayout(header)

        sub = QLabel(
            "Everything Hearth proposed or ran, newest first. Nothing here is synced anywhere."
        )
        sub.setProperty("muted", True)
        layout.addWidget(sub)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["When", "Tool", "Risk", "Status", "Details"])
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, stretch=1)
        self.refresh()

    def refresh(self) -> None:
        rows = self._db.list_actions()
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            when = datetime.fromtimestamp(row["created_at"]).strftime("%m-%d %H:%M:%S")
            details = row["result_summary"] or row["preview"]
            for col, value in enumerate((when, row["tool"], row["risk"], row["status"], details)):
                self._table.setItem(i, col, QTableWidgetItem(str(value)))

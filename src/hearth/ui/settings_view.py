"""Settings: model, runtime, and Google credentials file. Saved to the
per-user config.toml; model changes apply on the next message."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import Config


class SettingsView(QWidget):
    def __init__(self, config: Config, on_saved: Callable[[Config], None]):
        super().__init__()
        self._config = config
        self._on_saved = on_saved

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        title = QLabel("Settings")
        title.setProperty("h1", True)
        layout.addWidget(title)

        model_frame = QFrame()
        model_frame.setProperty("card", True)
        model_layout = QVBoxLayout(model_frame)
        heading = QLabel("Model")
        heading.setProperty("h2", True)
        model_layout.addWidget(heading)
        hint = QLabel(
            "gemma4:e2b suits 8 GB machines; switch to gemma4:e4b on 16 GB+. "
            "Hearth never downloads a model by itself — pull one with "
            "'ollama pull <name>' first."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        model_layout.addWidget(hint)

        self._model_name = QLineEdit(config.model.name)
        self._context = QSpinBox()
        self._context.setRange(1024, 131072)
        self._context.setValue(config.model.context_length)
        self._steps = QSpinBox()
        self._steps.setRange(1, 20)
        self._steps.setValue(config.model.max_agent_steps)
        self._keep_alive = QLineEdit(config.model.keep_alive)

        for label, widget in (
            ("Model name", self._model_name),
            ("Context length (tokens)", self._context),
            ("Max tool steps per request", self._steps),
            ("Keep model loaded for (e.g. 5m)", self._keep_alive),
        ):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setProperty("muted", True)
            lab.setMinimumWidth(220)
            row.addWidget(lab)
            row.addWidget(widget, stretch=1)
            model_layout.addLayout(row)

        self._autostart = QCheckBox("Start Ollama automatically when Hearth needs it")
        self._autostart.setChecked(config.ollama.autostart)
        model_layout.addWidget(self._autostart)
        layout.addWidget(model_frame)

        appearance_frame = QFrame()
        appearance_frame.setProperty("card", True)
        appearance_layout = QVBoxLayout(appearance_frame)
        aheading = QLabel("Appearance")
        aheading.setProperty("h2", True)
        appearance_layout.addWidget(aheading)
        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme")
        theme_label.setProperty("muted", True)
        theme_label.setMinimumWidth(220)
        self._theme = QComboBox()
        self._theme.addItems(["System", "Dark", "Light"])
        current = {"system": 0, "dark": 1, "light": 2}.get(config.ui.theme, 0)
        self._theme.setCurrentIndex(current)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self._theme, stretch=1)
        appearance_layout.addLayout(theme_row)
        layout.addWidget(appearance_frame)

        google_frame = QFrame()
        google_frame.setProperty("card", True)
        google_layout = QVBoxLayout(google_frame)
        gheading = QLabel("Google credentials")
        gheading.setProperty("h2", True)
        google_layout.addWidget(gheading)
        ghint = QLabel(
            "Path to the OAuth 'Desktop app' JSON you downloaded from Google Cloud "
            "Console. Required before connecting Gmail (docs/google-oauth.md)."
        )
        ghint.setProperty("muted", True)
        ghint.setWordWrap(True)
        google_layout.addWidget(ghint)
        cred_row = QHBoxLayout()
        self._credentials = QLineEdit(config.gmail.credentials_file)
        self._credentials.setPlaceholderText("/path/to/client_secret_….json")
        browse = QPushButton("Browse…")
        browse.setProperty("secondary", True)
        browse.clicked.connect(self._pick_credentials)
        cred_row.addWidget(self._credentials, stretch=1)
        cred_row.addWidget(browse)
        google_layout.addLayout(cred_row)
        layout.addWidget(google_frame)

        save_row = QHBoxLayout()
        self._status = QLabel("")
        self._status.setProperty("muted", True)
        save = QPushButton("Save settings")
        save.setObjectName("sendBtn")
        save.clicked.connect(self._save)
        save_row.addWidget(self._status, stretch=1)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        layout.addStretch(1)

    def _pick_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Google OAuth credentials", "", "JSON files (*.json)"
        )
        if path:
            self._credentials.setText(path)

    def _save(self) -> None:
        self._config.model.name = self._model_name.text().strip() or self._config.model.name
        self._config.model.context_length = self._context.value()
        self._config.model.max_agent_steps = self._steps.value()
        self._config.model.keep_alive = self._keep_alive.text().strip() or "5m"
        self._config.ollama.autostart = self._autostart.isChecked()
        self._config.gmail.credentials_file = self._credentials.text().strip()
        self._config.ui.theme = ["system", "dark", "light"][self._theme.currentIndex()]
        self._config.save()
        self._on_saved(self._config)
        self._status.setText("Saved. Model changes apply to the next message.")

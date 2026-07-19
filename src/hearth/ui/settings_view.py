"""Settings: model picker, runtime, personalization, Google credentials, and
cloud API keys. Config values go to the per-user config.toml; API keys go to
the OS credential store only. Model changes apply on the next message.

The model picker lists what is actually usable on this machine: models
installed in Ollama (queried live, with sizes) plus cloud models for every
provider with a stored API key. Picking a cloud model makes it the primary —
labeled in the app — while local stays the default."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

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
from ..runtime.cloud import CLOUD_PROVIDERS, configured_model
from ..storage.keychain import SecretStore

_CUSTOM = ("custom", "")


def _total_ram_gb() -> int | None:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3)
    except Exception:  # noqa: BLE001 — a hint, never a blocker
        return None


class SettingsView(QWidget):
    def __init__(
        self,
        config: Config,
        on_saved: Callable[[Config], None],
        secrets: SecretStore,
        list_models: Callable[[], Awaitable[list[dict]]] | None = None,
    ):
        super().__init__()
        self._config = config
        self._on_saved = on_saved
        self._secrets = secrets
        self._list_models = list_models

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        title = QLabel("Settings")
        title.setProperty("h1", True)
        layout.addWidget(title)

        layout.addWidget(self._build_model_card())
        layout.addWidget(self._build_personal_card())
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_google_card())
        layout.addWidget(self._build_fallback_card())

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

        self._refresh_models()

    # -- card builders -------------------------------------------------------

    def _labelled_row(self, layout: QVBoxLayout, label: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setProperty("muted", True)
        lab.setMinimumWidth(220)
        row.addWidget(lab)
        row.addWidget(widget, stretch=1)
        layout.addLayout(row)

    def _build_model_card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        heading = QLabel("Model")
        heading.setProperty("h2", True)
        box.addWidget(heading)

        ram = _total_ram_gb()
        ram_note = f" This machine has {ram} GB RAM." if ram else ""
        hint = QLabel(
            "Local models come from Ollama on this machine — add more with "
            "'ollama pull <name>' and hit Refresh. Cloud models appear once their "
            "API key is saved below; picking one sends chats to that provider and "
            f"is labeled in the app.{ram_note}"
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        box.addWidget(hint)

        picker_row = QHBoxLayout()
        self._model_combo = QComboBox()
        self._model_combo.currentIndexChanged.connect(self._on_model_choice)
        refresh = QPushButton("Refresh")
        refresh.setProperty("secondary", True)
        refresh.clicked.connect(self._refresh_models)
        picker_row.addWidget(self._model_combo, stretch=1)
        picker_row.addWidget(refresh)
        box.addLayout(picker_row)

        self._custom_model = QLineEdit()
        self._custom_model.setPlaceholderText("Ollama model name, e.g. llama3.2:3b")
        self._custom_model.hide()
        box.addWidget(self._custom_model)

        self._context = QSpinBox()
        self._context.setRange(1024, 131072)
        self._context.setValue(self._config.model.context_length)
        self._steps = QSpinBox()
        self._steps.setRange(1, 20)
        self._steps.setValue(self._config.model.max_agent_steps)
        self._keep_alive = QLineEdit(self._config.model.keep_alive)
        self._labelled_row(box, "Context length (tokens)", self._context)
        self._labelled_row(box, "Max tool steps per request", self._steps)
        self._labelled_row(box, "Keep model loaded for (e.g. 5m)", self._keep_alive)

        self._autostart = QCheckBox("Start Ollama automatically when Hearth needs it")
        self._autostart.setChecked(self._config.ollama.autostart)
        box.addWidget(self._autostart)
        return frame

    def _build_personal_card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        heading = QLabel("Personal")
        heading.setProperty("h2", True)
        box.addWidget(heading)
        hint = QLabel(
            "Optional. Used only for the greeting and so the assistant knows who "
            "it's helping — stored in your local config, shared with nothing else."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        box.addWidget(hint)
        self._user_name = QLineEdit(self._config.user.name)
        self._user_name.setPlaceholderText("What should Hearth call you?")
        self._user_about = QLineEdit(self._config.user.about)
        self._user_about.setPlaceholderText("e.g. I'm a student in Dhaka; keep answers short")
        self._labelled_row(box, "Your name", self._user_name)
        self._labelled_row(box, "About you", self._user_about)
        return frame

    def _build_appearance_card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        heading = QLabel("Appearance")
        heading.setProperty("h2", True)
        box.addWidget(heading)
        self._theme = QComboBox()
        self._theme.addItems(["System", "Dark", "Light"])
        current = {"system": 0, "dark": 1, "light": 2}.get(self._config.ui.theme, 0)
        self._theme.setCurrentIndex(current)
        self._labelled_row(box, "Theme", self._theme)
        return frame

    def _build_google_card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        heading = QLabel("Google credentials")
        heading.setProperty("h2", True)
        box.addWidget(heading)
        hint = QLabel(
            "Path to the OAuth 'Desktop app' JSON you downloaded from Google Cloud "
            "Console. Required before connecting Gmail (docs/google-oauth.md)."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        box.addWidget(hint)
        cred_row = QHBoxLayout()
        self._credentials = QLineEdit(self._config.gmail.credentials_file)
        self._credentials.setPlaceholderText("/path/to/client_secret_….json")
        browse = QPushButton("Browse…")
        browse.setProperty("secondary", True)
        browse.clicked.connect(self._pick_credentials)
        cred_row.addWidget(self._credentials, stretch=1)
        cred_row.addWidget(browse)
        box.addLayout(cred_row)
        return frame

    def _build_fallback_card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        heading = QLabel("Cloud providers (optional)")
        heading.setProperty("h2", True)
        box.addWidget(heading)
        hint = QLabel(
            "Keys unlock two things: cloud models in the picker above, and — if "
            "enabled — automatic fallback when the local model is down (labeled in "
            "chat every time). Keys are stored in the system keychain, never in files."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        box.addWidget(hint)
        self._fallback_enabled = QCheckBox("Allow cloud fallback when the local model is down")
        self._fallback_enabled.setChecked(self._config.fallback.enabled)
        box.addWidget(self._fallback_enabled)

        self._key_fields: dict[str, QLineEdit] = {}
        for provider_id, spec in CLOUD_PROVIDERS.items():
            row = QHBoxLayout()
            lab = QLabel(f"{spec.label} API key")
            lab.setProperty("muted", True)
            lab.setMinimumWidth(220)
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            clear = QPushButton("Clear")
            clear.setProperty("secondary", True)
            clear.clicked.connect(lambda _=False, p=provider_id: self._clear_key(p))
            row.addWidget(lab)
            row.addWidget(field, stretch=1)
            row.addWidget(clear)
            box.addLayout(row)
            self._key_fields[provider_id] = field
        self._refresh_key_placeholders()
        return frame

    # -- model picker --------------------------------------------------------

    def _refresh_models(self) -> None:
        if self._list_models is None:
            self._fill_models([])
            return
        asyncio.ensure_future(self._fetch_models())

    async def _fetch_models(self) -> None:
        try:
            models = await self._list_models()
        except Exception:  # noqa: BLE001 — a dead daemon just means an empty list
            models = []
        self._fill_models(models)

    def _fill_models(self, models: list[dict]) -> None:
        current = (self._config.model.provider, self._config.model.name)
        combo = self._model_combo
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            size = f" — {m['size_gb']} GB" if m.get("size_gb") else ""
            combo.addItem(f"{m['name']}{size}", ("ollama", m["name"]))
        if current[0] == "ollama" and current[1] not in {m["name"] for m in models}:
            combo.addItem(f"{current[1]} — not installed", current)
        for provider_id, spec in CLOUD_PROVIDERS.items():
            model = configured_model(self._config.fallback, provider_id)
            if self._secrets.get(spec.key_name):
                combo.addItem(f"{spec.label} — {model} (cloud)", (provider_id, model))
        # Whatever is currently configured must stay selectable, even if its
        # key was cleared or its configured model was edited by hand.
        datas = {combo.itemData(i) for i in range(combo.count())}
        if current[0] != "ollama" and current not in datas:
            spec = CLOUD_PROVIDERS.get(current[0])
            label = spec.label if spec else current[0]
            combo.addItem(f"{label} — {current[1]} (cloud)", current)
        combo.addItem("Custom local model…", _CUSTOM)
        index = next((i for i in range(combo.count()) if combo.itemData(i) == current), 0)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._on_model_choice()

    def _on_model_choice(self) -> None:
        self._custom_model.setVisible(self._model_combo.currentData() == _CUSTOM)

    # -- actions -------------------------------------------------------------

    def _pick_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Google OAuth credentials", "", "JSON files (*.json)"
        )
        if path:
            self._credentials.setText(path)

    def _refresh_key_placeholders(self) -> None:
        for provider_id, field in self._key_fields.items():
            stored = self._secrets.get(CLOUD_PROVIDERS[provider_id].key_name)
            field.setPlaceholderText(
                "saved in keychain — paste to replace" if stored else "not set"
            )

    def _clear_key(self, provider_id: str) -> None:
        self._secrets.delete(CLOUD_PROVIDERS[provider_id].key_name)
        self._key_fields[provider_id].clear()
        self._refresh_key_placeholders()
        self._refresh_models()
        self._status.setText(f"{CLOUD_PROVIDERS[provider_id].label} key removed.")

    def _save(self) -> None:
        choice = self._model_combo.currentData()
        if choice == _CUSTOM:
            if name := self._custom_model.text().strip():
                self._config.model.provider = "ollama"
                self._config.model.name = name
        elif choice is not None:
            self._config.model.provider, self._config.model.name = choice
        self._config.model.context_length = self._context.value()
        self._config.model.max_agent_steps = self._steps.value()
        self._config.model.keep_alive = self._keep_alive.text().strip() or "5m"
        self._config.ollama.autostart = self._autostart.isChecked()
        self._config.gmail.credentials_file = self._credentials.text().strip()
        self._config.ui.theme = ["system", "dark", "light"][self._theme.currentIndex()]
        self._config.user.name = self._user_name.text().strip()
        self._config.user.about = self._user_about.text().strip()
        self._config.fallback.enabled = self._fallback_enabled.isChecked()
        for provider_id, field in self._key_fields.items():
            if value := field.text().strip():
                self._secrets.set(CLOUD_PROVIDERS[provider_id].key_name, value)
                field.clear()
        self._refresh_key_placeholders()
        self._refresh_models()  # a new key may have unlocked cloud entries
        self._config.save()
        self._on_saved(self._config)
        self._status.setText("Saved. Model changes apply to the next message.")

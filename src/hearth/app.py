"""Hearth application entry point and composition root.

Builds the storage, runtime, agent, connectors, and UI, and runs the Qt +
asyncio (qasync) event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from .agent.gate import ActionGate, ApprovalRequest, ApprovalResponse
from .agent.loop import AgentEvent, AgentLoop
from .agent.tools import ToolRegistry
from .config import Config
from .connectors.calendar import make_calendar_store, register_calendar_tools
from .connectors.files import ApprovedRoots, register_file_tools
from .connectors.gmail import GoogleGmailClient, register_gmail_tools
from .connectors.google_auth import CALENDAR_SCOPES, GMAIL_SCOPES, GoogleAuth
from .connectors.reminders import register_reminders_tools
from .connectors.system import register_sysinfo_tools, register_system_tools
from .connectors.utility import register_utility_tools
from .connectors.weather import register_weather_tools
from .logging_setup import setup_logging
from .permissions import Permissions
from .runtime.ollama_manager import OllamaRuntimeManager, RuntimeState
from .runtime.provider import ChatMessage, OllamaProvider
from .storage.db import Database
from .storage.keychain import KeychainSecretStore
from .ui.chat_view import ChatView
from .ui.history_view import HistoryView
from .ui.main_window import MainWindow
from .ui.permission_center import PermissionCenter
from .ui.settings_view import SettingsView
from .ui.theme import apply_theme

log = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 12  # keep the 4k context from overflowing


class HearthApp:
    def __init__(self, qt_app: QApplication):
        self._qt = qt_app
        self.config = Config.load()

        # storage
        self.db = Database()
        self.secrets = KeychainSecretStore()

        # Google (Gmail everywhere; Google Calendar on non-mac)
        scopes = list(GMAIL_SCOPES)
        use_google_calendar = self.config.calendar.backend == "google" or (
            self.config.calendar.backend == "auto" and sys.platform != "darwin"
        )
        if use_google_calendar:
            scopes += CALENDAR_SCOPES
        self.google_auth = GoogleAuth(self.secrets, scopes)
        self.gmail_client = GoogleGmailClient(self.google_auth)
        self.calendar_store = make_calendar_store(
            self.config.calendar.backend, google_auth=self.google_auth
        )

        # runtime
        self.runtime = OllamaRuntimeManager(self.config.ollama)
        self.provider = OllamaProvider(self.config.model, self.config.ollama)

        # permissions + tools
        self.permissions = Permissions(self.db, self.google_auth.is_connected)
        self.registry = ToolRegistry()
        self._register_tools()

        # UI
        self.window = MainWindow()
        self.chat = ChatView(on_send=self._on_send, on_stop=self._on_stop)
        self.permission_center = PermissionCenter(
            self.permissions,
            self.db,
            connect_gmail=lambda: asyncio.ensure_future(self._connect_gmail()),
            disconnect_gmail=self._disconnect_gmail,
            grant_calendar=lambda: asyncio.ensure_future(self.calendar_store.request_access()),
        )
        self.history_view = HistoryView(self.db)
        self.settings_view = SettingsView(self.config, on_saved=self._on_settings_saved)
        self.window.add_view("Chat", self.chat)
        self.window.add_view("Permissions", self.permission_center)
        self.window.add_view("History", self.history_view)
        self.window.add_view("Settings", self.settings_view)
        self.window.finish_sidebar()

        self._tray = QSystemTrayIcon(self._default_icon(), self._qt)
        self._tray.show()

        # gate + agent (approval cards live in the chat view)
        self.gate = ActionGate(
            self.db, self.registry, self.permissions.check, self._request_approval
        )
        self.agent = AgentLoop(
            self.provider,
            self.registry,
            self.gate,
            max_steps=self.config.model.max_agent_steps,
        )

        self.conversation_id = self.db.create_conversation()
        self._history: list[ChatMessage] = []
        self._active_task: asyncio.Task | None = None

        apply_theme(self._qt, self.config.ui.theme)
        try:
            self._qt.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except AttributeError:
            pass  # older Qt without the signal: no live follow, still themed

        self._qt.aboutToQuit.connect(self._shutdown)

    # -- wiring ---------------------------------------------------------------

    def _default_icon(self) -> QIcon:
        return self._qt.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def _register_tools(self) -> None:
        register_utility_tools(self.registry, self.config.web)
        register_file_tools(
            self.registry,
            ApprovedRoots(self.db.list_approved_folders),
            self.config.files.max_read_bytes,
        )
        register_gmail_tools(self.registry, self.gmail_client)
        register_calendar_tools(self.registry, self.calendar_store)
        register_system_tools(
            self.registry,
            clipboard_get=lambda: self._qt.clipboard().text(),
            clipboard_set=lambda text: self._qt.clipboard().setText(text),
            notifier=self._notify,
            approved_shortcuts=self.db.list_approved_shortcuts,
        )
        register_sysinfo_tools(self.registry)
        register_reminders_tools(self.registry, self.db)
        register_weather_tools(self.registry)

    def _notify(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message)

    # -- approval bridge -----------------------------------------------------

    async def _request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        future = self.chat.show_confirmation(request)
        response: ApprovalResponse = await future
        self.history_view.mark_dirty()
        return response

    # -- Google connect ----------------------------------------------------------

    async def _connect_gmail(self) -> str:
        credentials_file = self.config.gmail.credentials_file
        if not credentials_file:
            raise RuntimeError("Set the Google credentials file path in Settings first.")
        email = await self.google_auth.connect(credentials_file)
        self.db.set_connector_status("gmail", "granted", {"email": email})
        return email

    def _disconnect_gmail(self) -> None:
        self.google_auth.disconnect()
        self.db.set_connector_status("gmail", "revoked")

    def _on_system_scheme_changed(self, *_args) -> None:
        if self.config.ui.theme == "system":
            apply_theme(self._qt, "system")

    def _on_settings_saved(self, config: Config) -> None:
        apply_theme(self._qt, config.ui.theme)
        self.runtime.update_config(config.ollama)
        self.provider = OllamaProvider(config.model, config.ollama)
        self.agent = AgentLoop(
            self.provider, self.registry, self.gate, max_steps=config.model.max_agent_steps
        )

    # -- chat flow -----------------------------------------------------------------

    def _on_send(self, text: str) -> None:
        if self._active_task and not self._active_task.done():
            return
        self.chat.add_user_message(text)
        self.chat.set_busy(True)
        self._active_task = asyncio.ensure_future(self._run_turn(text))

    def _on_stop(self) -> None:
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self.chat.cancel_open_cards()

    async def _run_turn(self, text: str) -> None:
        try:
            state = await self.runtime.ensure_running()
            if state is not RuntimeState.READY:
                self._set_status_for_state(state)
                self.chat.add_assistant_message(
                    "I can't reach the local model right now. " + self._recovery_hint(state)
                )
                return
            if not await self.runtime.model_available(self.config.model.name):
                self.window.set_status(f"Model missing: {self.config.model.name}")
                self.chat.add_assistant_message(
                    f"The model '{self.config.model.name}' isn't installed in Ollama. "
                    f"Run 'ollama pull {self.config.model.name}' in a terminal, or pick "
                    "an installed model in Settings. I never download models myself."
                )
                return

            self.window.set_status(f"Model: {self.config.model.name} — thinking…")
            self.chat.begin_stream()

            def on_event(event: AgentEvent) -> None:
                if event.kind == "text":
                    self.chat.stream_chunk(event.text)
                elif event.kind == "tool_started":
                    self.chat.add_tool_note(f"Running {event.tool}…")
                elif event.kind == "tool_finished":
                    self.history_view.mark_dirty()

            async with self.runtime.generation_lock:
                answer = await self.agent.run(
                    list(self._history), text, on_event, self.conversation_id
                )

            self.chat.end_stream(answer)
            self._history.append(ChatMessage("user", text))
            self._history.append(ChatMessage("assistant", answer))
            self._history[:] = self._history[-MAX_HISTORY_MESSAGES:]
            self.db.add_message(self.conversation_id, "user", text)
            self.db.add_message(self.conversation_id, "assistant", answer)
            self.window.set_status(f"Model: {self.config.model.name} — ready")
        except asyncio.CancelledError:
            self.chat.end_stream("")
            self.chat.add_tool_note("Stopped.")
            self.window.set_status(f"Model: {self.config.model.name} — ready")
        except Exception as exc:  # noqa: BLE001 — last-resort guard for the UI
            log.exception("Turn failed")
            self.chat.end_stream("")
            self.chat.add_assistant_message(
                f"Something went wrong: {exc}. If this keeps happening, check the "
                "log file and that Ollama has enough free memory."
            )
        finally:
            self.chat.set_busy(False)

    # -- runtime status ---------------------------------------------------------

    def _recovery_hint(self, state: RuntimeState) -> str:
        if state is RuntimeState.UNAVAILABLE:
            return (
                "I tried to start Ollama but couldn't. Check that Ollama is installed "
                "(https://ollama.com/download), or start it manually with 'ollama serve'."
            )
        return "It is still starting — try again in a few seconds."

    def _set_status_for_state(self, state: RuntimeState) -> None:
        labels = {
            RuntimeState.READY: f"Model: {self.config.model.name} — ready",
            RuntimeState.STARTING: "Model: starting Ollama…",
            RuntimeState.UNAVAILABLE: "Model: unavailable",
            RuntimeState.MODEL_MISSING: f"Model missing: {self.config.model.name}",
        }
        self.window.set_status(labels.get(state, "Model: checking…"))

    async def startup_check(self) -> None:
        try:
            state = await self.runtime.ensure_running()
            if state is RuntimeState.READY and not await self.runtime.model_available(
                self.config.model.name
            ):
                state = RuntimeState.MODEL_MISSING
                self.runtime.state = state
            self._set_status_for_state(state)
        except Exception:  # noqa: BLE001 — status check must never crash startup
            log.exception("Startup check failed")
            self.window.set_status("Model: status unknown")

    def _shutdown(self) -> None:
        self.chat.cancel_open_cards()
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self.runtime.shutdown()
        self.db.close()


def main() -> None:
    setup_logging()
    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    app.setApplicationName("Hearth")

    import qasync

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    hearth = HearthApp(app)
    hearth.window.show()

    with loop:
        loop.create_task(hearth.startup_check())
        loop.run_forever()


if __name__ == "__main__":
    main()

"""Hearth application entry point and composition root.

Builds the storage, runtime, agent, connectors, and UI, and runs the Qt +
asyncio (qasync) event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .agent.gate import ActionGate, ApprovalRequest, ApprovalResponse, DbSealStore
from .agent.loop import AgentEvent, AgentLoop
from .agent.prompts import build_system_prompt
from .agent.tools import ToolRegistry
from .assurance import (
    EffectAdapterRegistry,
    IntentSeal,
    PolicyConfig,
    Principal,
    get_or_create_key,
    register_builtin_adapters,
)
from .attachments import extract_document, frame_document
from .config import Config, app_data_dir
from .connectors.calendar import make_calendar_store, register_calendar_tools
from .connectors.files import ApprovedRoots, register_file_tools
from .connectors.gmail import GoogleGmailClient, register_gmail_tools
from .connectors.google_auth import CALENDAR_SCOPES, GMAIL_SCOPES, GoogleAuth
from .connectors.mcp import MCPManager
from .connectors.reminders import register_reminders_tools
from .connectors.system import register_sysinfo_tools, register_system_tools
from .connectors.utility import register_utility_tools
from .connectors.weather import register_weather_tools
from .logging_setup import setup_logging
from .permissions import Permissions
from .runtime.cloud import (
    CLOUD_PROVIDERS,
    FallbackProvider,
    build_cloud_chain,
    build_primary_provider,
)
from .runtime.ollama_manager import OllamaRuntimeManager, RuntimeState
from .runtime.provider import ChatMessage, OllamaProvider
from .skills import SkillLibrary
from .storage.db import Database
from .storage.keychain import KeychainSecretStore
from .ui.chat_view import ChatView
from .ui.history_view import HistoryView
from .ui.main_window import MainWindow
from .ui.permission_center import PermissionCenter
from .ui.settings_view import SettingsView
from .ui.theme import app_icon, apply_theme
from .voice import VoiceError, VoiceInput

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

        # voice input (optional deps; degrades to an install hint)
        self.voice = VoiceInput(app_data_dir() / "voice-models")
        self._voice_busy = False

        # UI
        self.window = MainWindow()
        self.chat = ChatView(
            on_send=self._on_send,
            on_stop=self._on_stop,
            on_voice=self._on_voice,
            greeting_name=self.config.user.name,
        )
        self.permission_center = PermissionCenter(
            self.permissions,
            self.db,
            connect_gmail=lambda: asyncio.ensure_future(self._connect_gmail()),
            disconnect_gmail=self._disconnect_gmail,
            grant_calendar=lambda: asyncio.ensure_future(self.calendar_store.request_access()),
        )
        self.history_view = HistoryView(self.db)
        self.settings_view = SettingsView(
            self.config,
            on_saved=self._on_settings_saved,
            secrets=self.secrets,
            list_models=self._installed_models,
        )
        self.window.add_view("Chat", self.chat)
        self.window.add_view("Permissions", self.permission_center)
        self.window.add_view("History", self.history_view)
        self.window.add_view("Settings", self.settings_view)
        self.window.finish_sidebar()

        icon = app_icon()
        self._qt.setWindowIcon(icon)
        self._tray = QSystemTrayIcon(icon, self._qt)
        self._tray.show()

        # IntentSeal: the deterministic, provenance-bound capability monitor.
        # One instance, one verification path. Its signing key lives in the OS
        # credential store; spent one-use nonces persist in SQLite so a seal
        # cannot be replayed across restarts. Full policy is active, but the
        # gate still confirms every WRITE tool (legacy semantics) until tests
        # justify a narrower policy.
        self.effects = EffectAdapterRegistry()
        register_builtin_adapters(self.effects)
        self.intentseal = IntentSeal(
            key=get_or_create_key(self.secrets),
            config=PolicyConfig.full(),
            seal_store=DbSealStore(self.db),
        )

        # gate + agent (approval cards live in the chat view)
        self.gate = ActionGate(
            self.db,
            self.registry,
            self.permissions.check,
            self._request_approval,
            intentseal=self.intentseal,
            effects=self.effects,
        )
        self._system_prompt = build_system_prompt(self.config.user.name, self.config.user.about)
        self.agent = AgentLoop(
            self.provider,
            self.registry,
            self.gate,
            max_steps=self.config.model.max_agent_steps,
            system_prompt=self._system_prompt,
            principal_provider=self._current_principal,
        )

        self.skills = SkillLibrary()
        self.conversation_id = self.db.create_conversation()
        self._history: list[ChatMessage] = []
        self._active_task: asyncio.Task | None = None
        self._cloud_primary_noted = False

        apply_theme(self._qt, self.config.ui.theme)
        try:
            self._qt.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except AttributeError:
            pass  # older Qt without the signal: no live follow, still themed

        self._qt.aboutToQuit.connect(self._shutdown)

    # -- wiring ---------------------------------------------------------------

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
            screen_capture=self._capture_screen,
        )
        register_sysinfo_tools(self.registry)
        register_reminders_tools(self.registry, self.db)
        register_weather_tools(self.registry)
        self.mcp = MCPManager(self.config.mcp, self.registry)

    def _notify(self, title: str, message: str) -> None:
        self._tray.showMessage(title, message)

    def _current_principal(self) -> Principal:
        metadata = self.db.get_connector_metadata("gmail")
        email = str(metadata.get("email", "")).strip().lower()
        account = f"gmail:{email}" if email else "local"
        return Principal(
            user_id="local-user",
            account=account,
            display_name=self.config.user.name,
        )

    def _capture_screen(self) -> str:
        from .images import ImageError, encode_qimage

        screen = self._qt.primaryScreen()
        if screen is None:
            raise ImageError("No screen available")
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            raise ImageError(
                "Capture returned an empty image (missing Screen Recording permission?)"
            )
        return encode_qimage(pixmap.toImage())

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
        self._cloud_primary_noted = False  # re-announce if a cloud model is picked
        self._system_prompt = build_system_prompt(config.user.name, config.user.about)
        self.provider = OllamaProvider(config.model, config.ollama)
        self.agent = AgentLoop(
            self.provider,
            self.registry,
            self.gate,
            max_steps=config.model.max_agent_steps,
            system_prompt=self._system_prompt,
            principal_provider=self._current_principal,
        )
        self.window.set_status(f"Model: {self._model_label()} — ready")

    # -- chat flow -----------------------------------------------------------------

    def _on_send(self, text: str, attachment_paths: list[str] | None = None) -> None:
        if self._active_task and not self._active_task.done():
            return
        attachment_paths = attachment_paths or []
        attached_names = [Path(p).name for p in attachment_paths]

        model_text = text
        if text.startswith("/"):
            expanded = self.skills.expand(text)
            if expanded is None:
                # Unknown command: answer locally, never wake the model.
                self.chat.add_user_message(text)
                self.chat.add_assistant_message(self.skills.help_text())
                return
            model_text = expanded

        suffix = f"\n[attached: {', '.join(attached_names)}]" if attached_names else ""
        self.chat.add_user_message(text + suffix)
        # Attachment content is turn-scoped: later turns keep a short marker so
        # the 4k context doesn't fill with stale documents and image tokens.
        history_text = text + ("\n[attachments were included earlier]" if attached_names else "")
        self.chat.set_busy(True)
        self._active_task = asyncio.ensure_future(
            self._run_turn(
                model_text,
                display_text=text,
                attachment_paths=attachment_paths,
                history_text=history_text,
            )
        )

    def _on_stop(self) -> None:
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self.chat.cancel_open_cards()

    async def _run_turn(
        self,
        text: str,
        display_text: str | None = None,
        attachment_paths: list[str] | None = None,
        history_text: str | None = None,
    ) -> None:
        display_text = display_text if display_text is not None else text
        history_text = history_text if history_text is not None else text
        try:
            # Encoding a photo or extracting a 40-page PDF takes seconds; do it
            # off the UI thread (QImage, unlike QPixmap, is thread-safe).
            images, doc_blocks, attachment_evidence, failures = await asyncio.to_thread(
                _process_attachments, attachment_paths or []
            )
            for note in failures:
                self.chat.add_tool_note(note)
            if doc_blocks:
                text = "\n\n".join([text, *doc_blocks])

            agent, model_label = await self._pick_agent()
            if agent is None:
                return  # _pick_agent already told the user why
            if self.config.model.provider == "ollama" and agent is not self.agent:
                consented = await self.gate.confirm_cloud_egress(
                    trusted_text=display_text,
                    untrusted_content=attachment_evidence,
                    history_messages=[
                        (message.role, message.content) for message in self._history
                    ],
                    resource=model_label,
                    principal=self._current_principal(),
                    conversation_id=self.conversation_id,
                )
                if not consented:
                    self.chat.add_assistant_message(
                        "Cloud fallback was not authorized, so no local content left this Mac."
                    )
                    return

            self.window.set_status(f"Model: {model_label} — thinking…")
            self.chat.begin_stream()

            def on_event(event: AgentEvent) -> None:
                if event.kind == "text":
                    self.chat.stream_chunk(event.text)
                elif event.kind == "tool_started":
                    self.chat.add_tool_note(f"Running {event.tool}…")
                elif event.kind == "tool_finished":
                    self.history_view.mark_dirty()

            async with self.runtime.generation_lock:
                try:
                    answer = await agent.run(
                        list(self._history),
                        text,
                        on_event,
                        self.conversation_id,
                        images=images,
                        trusted_user_text=display_text,
                        attachment_evidence=attachment_evidence,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — try cloud before giving up
                    chain = self._cloud_chain() if agent is self.agent else []
                    if not chain:
                        raise
                    log.warning("Local turn failed (%s); retrying via cloud fallback", exc)
                    consented = await self.gate.confirm_cloud_egress(
                        trusted_text=display_text,
                        untrusted_content=attachment_evidence,
                        history_messages=[
                            (message.role, message.content) for message in self._history
                        ],
                        resource=f"{chain[0].label} (cloud)",
                        principal=self._current_principal(),
                        conversation_id=self.conversation_id,
                    )
                    if not consented:
                        self.chat.end_stream("")
                        self.chat.add_assistant_message(
                            "The local model failed, and cloud fallback was not authorized. "
                            "No local content was sent."
                        )
                        return
                    self.chat.end_stream("")
                    agent, model_label = self._make_cloud_agent(chain)
                    self.chat.add_tool_note(
                        f"The local model failed mid-request — retrying with {model_label}."
                    )
                    self.window.set_status(f"Model: {model_label} — thinking…")
                    self.chat.begin_stream()
                    answer = await agent.run(
                        list(self._history),
                        text,
                        on_event,
                        self.conversation_id,
                        images=images,
                        trusted_user_text=display_text,
                        attachment_evidence=attachment_evidence,
                    )

            self.chat.end_stream(answer)
            self._history.append(ChatMessage("user", history_text))
            self._history.append(ChatMessage("assistant", answer))
            self._history[:] = self._history[-MAX_HISTORY_MESSAGES:]
            self.db.add_message(self.conversation_id, "user", display_text)
            self.db.add_message(self.conversation_id, "assistant", answer)
            self.window.set_status(f"Model: {model_label} — ready")
        except asyncio.CancelledError:
            self.chat.end_stream("")
            self.chat.add_tool_note("Stopped.")
            self.window.set_status(f"Model: {self._model_label()} — ready")
        except Exception as exc:  # noqa: BLE001 — last-resort guard for the UI
            log.exception("Turn failed")
            self.chat.end_stream("")
            hint = (
                "check the log file and that Ollama has enough free memory"
                if self.config.model.provider == "ollama"
                else "check the log file and the provider's API key in Settings"
            )
            self.chat.add_assistant_message(
                f"Something went wrong: {exc}. If this keeps happening, {hint}."
            )
        finally:
            self.chat.set_busy(False)

    # -- provider selection ------------------------------------------------------

    async def _installed_models(self) -> list[dict]:
        """Installed Ollama models for the Settings picker. Starts the daemon
        if allowed — unless a cloud primary is selected, in which case an idle
        daemon shouldn't be spun up just to fill a dropdown."""
        if self.config.model.provider != "ollama":
            if not await self.runtime.is_daemon_up():
                return []
        elif await self.runtime.ensure_running() is not RuntimeState.READY:
            return []
        return await self.runtime.list_models(fresh=True)

    def _model_label(self) -> str:
        if self.config.model.provider == "ollama":
            return self.config.model.name
        spec = CLOUD_PROVIDERS.get(self.config.model.provider)
        label = spec.label if spec else self.config.model.provider
        return f"{label} · {self.config.model.name} (cloud)"

    async def _pick_agent(self) -> tuple[AgentLoop | None, str]:
        """The agent for the model chosen in Settings: a cloud provider when
        the user explicitly picked one; otherwise the local agent when the
        local model is reachable; otherwise the cloud fallback chain if
        enabled; otherwise None after explaining the situation in the chat."""
        if self.config.model.provider != "ollama":
            picked = self._primary_cloud_agent()
            if picked is None:
                self.chat.add_assistant_message(
                    f"The cloud model you selected ({self._model_label()}) has no API key "
                    "stored. Add the key in Settings, or switch back to a local model."
                )
                return None, ""
            agent, label = picked
            if not self._cloud_primary_noted:
                self._cloud_primary_noted = True
                self.chat.add_tool_note(
                    f"Using {label} — messages go to this provider until you pick a "
                    "local model in Settings."
                )
            return agent, label

        state = await self.runtime.ensure_running()
        if state is RuntimeState.READY and await self.runtime.model_available(
            self.config.model.name
        ):
            return self.agent, self.config.model.name
        if chain := self._cloud_chain():
            agent, label = self._make_cloud_agent(chain)
            self.chat.add_tool_note(f"Local model unavailable — falling back to {label}.")
            return agent, label
        if state is not RuntimeState.READY:
            self._set_status_for_state(state)
            self.chat.add_assistant_message(
                "I can't reach the local model right now. " + self._recovery_hint(state)
            )
        else:
            self.window.set_status(f"Model missing: {self.config.model.name}")
            self.chat.add_assistant_message(
                f"The model '{self.config.model.name}' isn't installed in Ollama. "
                f"Run 'ollama pull {self.config.model.name}' in a terminal, or pick "
                "an installed model in Settings. I never download models myself."
            )
        return None, ""

    def _cloud_chain(self):
        if not self.config.fallback.enabled:
            return []
        return build_cloud_chain(self.config.fallback, self.secrets)

    def _make_cloud_agent(self, chain) -> tuple[AgentLoop, str]:
        provider = FallbackProvider(
            chain,
            on_switch=lambda label: self.chat.add_tool_note(
                f"{label} did not answer — trying the next cloud fallback…"
            ),
        )
        return self._wrap_agent(provider), f"{chain[0].label} (cloud)"

    def _primary_cloud_agent(self) -> tuple[AgentLoop, str] | None:
        provider = build_primary_provider(
            self.config.model.provider, self.config.model.name, self.secrets
        )
        if provider is None:
            return None
        return self._wrap_agent(provider), self._model_label()

    def _wrap_agent(self, provider) -> AgentLoop:
        return AgentLoop(
            provider,
            self.registry,
            self.gate,
            max_steps=self.config.model.max_agent_steps,
            system_prompt=self._system_prompt,
            principal_provider=self._current_principal,
        )

    # -- voice input -------------------------------------------------------------

    def _on_voice(self) -> None:
        if self._voice_busy:
            return
        if self.voice.recording:
            try:
                audio = self.voice.stop()
            except VoiceError as exc:
                self.chat.add_tool_note(str(exc))
                self.chat.set_voice_state("idle")
                return
            self._voice_busy = True
            self.chat.set_voice_state("busy")
            asyncio.ensure_future(self._transcribe(audio))
            return
        if not self.voice.available:
            from .voice import INSTALL_HINT

            self.chat.add_tool_note(INSTALL_HINT)
            return
        if not self.voice.model_ready():
            reply = QMessageBox.question(
                self.window,
                "Download speech model?",
                "Voice input uses a local Whisper model (about 75 MB, downloaded once "
                "from Hugging Face). Recording and transcription always run on this "
                "machine — audio never leaves it.\n\nDownload the model now?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        try:
            self.voice.start()
        except VoiceError as exc:
            self.chat.add_tool_note(str(exc))
            return
        self.chat.set_voice_state("recording")

    async def _transcribe(self, audio) -> None:
        try:
            text = await asyncio.to_thread(self.voice.transcribe_blocking, audio)
            self.chat.insert_transcript(text)
        except VoiceError as exc:
            self.chat.add_tool_note(str(exc))
        except Exception as exc:  # noqa: BLE001 — a bad model download shouldn't crash the app
            log.exception("Transcription failed")
            self.chat.add_tool_note(f"Transcription failed: {exc}")
        finally:
            self._voice_busy = False
            self.chat.set_voice_state("idle")

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
            if self.config.model.provider != "ollama":
                # Cloud model chosen: don't autostart Ollama just to sit idle.
                self.window.set_status(f"Model: {self._model_label()}")
            else:
                state = await self.runtime.ensure_running()
                if state is RuntimeState.READY and not await self.runtime.model_available(
                    self.config.model.name
                ):
                    state = RuntimeState.MODEL_MISSING
                    self.runtime.state = state
                self._set_status_for_state(state)
            if self.config.mcp.servers and self.permissions.check("mcp"):
                await self.mcp.start()
        except Exception:  # noqa: BLE001 — status check must never crash startup
            log.exception("Startup check failed")
            self.window.set_status("Model: status unknown")

    def _shutdown(self) -> None:
        self.voice.cancel()
        self.chat.cancel_open_cards()
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self.mcp.kill_all()
        self.runtime.shutdown()
        self.db.close()


def _process_attachments(
    paths: list[str],
) -> tuple[list[str], list[str], list[tuple[str, object]], list[str]]:
    """Encode images / extract documents for one turn. Runs in a worker thread.

    Returns (base64 images, framed document blocks, user-facing failure notes);
    one bad file never blocks the send.
    """
    from .images import encode_image_file, is_image_path

    images: list[str] = []
    doc_blocks: list[str] = []
    evidence: list[tuple[str, object]] = []
    failures: list[str] = []
    for path in paths:
        name = Path(path).name
        try:
            if is_image_path(path):
                images.append(encode_image_file(path))
                evidence.append((name, {"image": "local image attachment"}))
            else:
                doc = extract_document(path)
                doc_blocks.append(frame_document(doc))
                evidence.append((doc.name, doc.text))
        except Exception as exc:  # noqa: BLE001 — report per file, keep going
            failures.append(f"Could not attach {name}: {exc}")
    return images, doc_blocks, evidence, failures


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

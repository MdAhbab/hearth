# Hearth — extension plan (appendix to the original build plan)

The original plan specified a macOS-only MVP ("personal-ai") with Gmail,
EventKit Calendar, approved-folder files, and narrow macOS tools. During the
build the product was named **Hearth**, and the scope was extended as follows.
This document records what was added, why, and what's next.

## Added in this build (beyond the original plan)

**Cross-platform support (macOS / Windows / Linux)**
- `platformdirs` for config/db/log locations; `keyring` already abstracts the
  three OS credential stores; `send2trash` abstracts Trash/Recycle Bin.
- Ollama manager finds the binary per-platform and detaches the daemon
  correctly on POSIX (`start_new_session`) and Windows (`CREATE_NO_WINDOW`).
- Calendar became a two-backend protocol: **EventKit** on macOS (native,
  offline, syncs Google calendars added to Apple Calendar) and **Google
  Calendar API** elsewhere, sharing the Gmail OAuth connection and consent
  flow. `[calendar] backend` config can force either.
- macOS-only tools (open app, Shortcuts, Chrome tab) register conditionally;
  the toolset degrades gracefully instead of breaking.

**New capabilities**
- `calendar_find_free_slots` — pure gap-finder over busy events within working
  hours ("find me a free hour this week"). Fully unit-tested.
- Clipboard read/write (write confirmed) via Qt — cross-platform.
- `web_fetch` — opt-in (off by default) page-to-text fetching, size-capped,
  script/style stripped. Off by default because it sends requests off-device.
- `time_now` and `calculate` (AST-whitelisted arithmetic only) — small local
  models are unreliable at dates and math; these keep answers honest and are
  always available.
- Gmail drafts vs. send are separate tools with distinct previews, so "draft
  it" never risks sending.

**Robustness corner cases handled**
- Model without native tool support → automatic JSON tool-call fallback
  (schema moved into the system prompt, response parsed defensively).
- Identical failing tool call twice in a row → loop breaks with an explanation
  instead of burning steps.
- Revoked/expired Google token → self-clears, UI shows disconnected, tools
  return a permission hint instead of stack traces.
- Locked/absent OS keyring → treated as "not connected", never a crash.
- Ollama killed mid-generation, model missing, daemon start timeout → typed
  states with specific recovery messages in the UI.
- Overwrite protection on file writes; boundary tests for `../`, symlink
  escape, and prefix-sibling roots (`/data` vs `/data-evil`).
- Approval cards resolve safely if the app quits or the run is cancelled while
  a card is open (resolved as Reject).

## Design decisions worth recording

- **One gate.** Every tool execution — read or write — flows through
  `ActionGate.execute`. There is no second path, which is what makes the
  "rejected actions change nothing" guarantee testable.
- **The model proposes, the executor disposes.** Tool results are framed as
  quoted data (`TOOL_RESULT_FRAME`) to blunt prompt injection from email/web
  content; the system prompt reinforces it; and the URL-opening tool is
  write-classified so exfiltration-via-link always needs human eyes.
- **No agent framework.** The loop is ~100 lines and fully scripted in tests;
  debugging beats abstraction at this scale.

## Delivered after the initial build

- **Reminders** — EventKit on macOS, local SQLite list elsewhere (v0.2).
- **Weather, system info, unit conversion, content search** (v0.2).
- **Production audit pass** — gate audit-row lifecycle under cancellation,
  per-session EventKit access, thread-safe SQLite, search pruning, psutil
  sampling, streaming batched rendering, capped chat memory, lazy history.
- **Light/dark theming** with a System-following mode.
- **MCP client** — external Model Context Protocol servers over stdio; every
  external tool call is confirmation-gated.
- **Skills** — built-in and user-defined /commands expanding to prompts.
- **Vision** — chat image attachments, files_view_image for approved
  folders, and a confirmation-gated screenshot tool; all images are
  downscaled to a 1024-px edge before reaching the model.
- **Document attachments** — PDFs (pypdf), DOCX (stdlib zip+XML), and
  text/code files attach in chat; text is extracted locally, capped to fit
  the context, and framed as quoted data like tool results.
- **Voice input** — push-to-talk mic button; faster-whisper (int8, CPU)
  transcribes locally into the input box for review before sending. Optional
  `hearth[voice]` extra; the ~75 MB model downloads only with explicit
  consent. (Ollama's API takes text+images only, so audio cannot go to the
  chat model directly — local transcription is the honest architecture.)
- **Cloud fallback** — opt-in chain over the OpenAI-compatible APIs of
  Gemini, OpenAI, DeepSeek, and NVIDIA. Used only when the local model is
  unreachable (including mid-request failure, retried once); keys in the OS
  keychain; every cloud turn labeled in chat; a provider that fails before
  emitting output is skipped, one that fails mid-stream is not retried to
  avoid duplicated text.

## Production-readiness pass (this build)

- **Python 3.13 support** — `run.py` and the pins now accept 3.11–3.13
  (PySide6 ≥ 6.8, PyInstaller ≥ 6.10), so machines with only the current
  Python work out of the box.
- **Model picker** — Settings lists installed Ollama models live (with sizes
  and a machine-RAM hint) plus one entry per cloud provider with a stored
  key. `[model] provider` selects "ollama" or a cloud id; a cloud primary is
  labeled in the status bar and announced in chat. Roadmap item "Model
  manager" is delivered except `ollama pull` (still deliberate: Hearth never
  downloads models).
- **Markdown rendering** — assistant bubbles render bold/lists/code/links via
  a ~140-line escape-first converter (`ui/markdown.py`); model text can never
  inject markup.
- **Personalization** — optional `[user] name/about` feeds the greeting and
  system prompt; the prompt is also platform-aware now (no more "your Mac" on
  Windows).
- **Windows open-app** — `system_open_app` on Windows matches Start Menu
  shortcuts by name (only installed apps; never a shell string).
- **UI polish** — a flame logo drawn with QPainter is the single brand mark
  (app/taskbar/tray icon, sidebar, welcome hero); the conversation is a
  centered width-capped column where the assistant answers as open text and
  the user speaks in compact warm bubbles that hug their content; welcome
  empty state with time-of-day greeting and starter chips (the permanent
  chip row is gone); Send↔Stop morphing button; scroll anchoring that
  respects reading position; thin scrollbars; composer focus ring;
  palette-correct error text in light mode; History empty state; minimum
  window size; accessible names on icon-only buttons.
- **Reliability** — attachments (PDF extraction, image encode) moved off the
  UI thread; DB guards against writes-after-close during shutdown; SQLite
  indexes for messages/actions/reminders; `/api/tags` result cached ~30 s.

## Roadmap (not in this build)

1. **Contacts lookup** — read-only contact search to help address emails
   (macOS Contacts framework / Google People API).
2. **Multiple conversations UI** — the schema already stores them; add a
   sidebar list + titles.
3. **Scheduled digests with consent** — e.g. a morning "inbox + calendar"
   summary the user explicitly turns on; still no autonomous writes.
4. **`ollama pull` with a confirmation card** — the picker exists; downloads
   stay manual for now.
5. **Local RAG over approved folders** — embeddings index (all local) so
   file search becomes semantic.
6. **Windows/Linux CI** — GitHub Actions matrix running the (already
   platform-clean) test suite on all three OSes.

# Troubleshooting

## Ollama / model

**"I can't reach the local model right now"**
- Is Ollama installed? `ollama --version`. If not: [ollama.com/download](https://ollama.com/download).
- Try `ollama serve` in a terminal and read its error. Port already in use
  usually means another daemon is fine and Hearth will find it on retry.
- Hearth looks for the binary on PATH, then the standard install locations.
  A custom location works as long as it's on PATH.

**"The model 'X' isn't installed"**
- Open Settings — the model picker lists everything Ollama has on this
  machine; pick one of those. `ollama pull gemma4:e2b` fetches the default
  if the list is empty. Hearth never pulls models itself.

**First reply is very slow**
- That's the model loading into memory (once per `keep_alive` window). On an
  8 GB machine expect tens of seconds cold, fast afterwards.

**Out-of-memory / system freezes (8 GB Macs)**
- Keep `context_length` at 4096, use the `e2b` model, close browsers with many
  tabs. If Ollama gets killed by the OS, Hearth reports the failure and
  recovers on the next message.
- Check pressure: Activity Monitor → Memory tab while generating.

**Responses cut off mid-sentence**
- Raise `request_timeout_s` in config, or lower `context_length`; on tiny
  machines long inputs crowd out the answer.

## Gmail / Google

**"Set the Google credentials file path in Settings first"** — do exactly
that; see [google-oauth.md](google-oauth.md).

**Browser opens but sign-in fails with "unverified app"** — click *Advanced →
Continue*. It's your own OAuth client in testing mode.

**"Gmail is not connected" after it worked for days** — testing-mode refresh
tokens expire after ~7 idle days. Reconnect, or publish your consent screen to
production.

**redirect_uri_mismatch** — the credential must be type **Desktop app**.

## Calendar

**macOS: access denied** — System Settings → Privacy & Security → Calendars.
During development the grant attaches to the app you launched Hearth from
(Terminal/IDE); the packaged Hearth.app prompts on first use.

**Windows/Linux: "Google account is not connected"** — calendar rides on the
Google connection there; connect Gmail first (with Calendar API enabled).

## Secrets / Keychain

**"Credential store unavailable"** — the OS keyring is locked or missing. On
Linux install/start a Secret Service provider (e.g. GNOME Keyring or KWallet).
Hearth treats a locked store as "not connected" rather than crashing.

## MCP servers

**Configured server's tools don't appear** — check the order: server declared
in `config.toml`, "MCP servers" permission enabled, then restart Hearth
(servers connect at startup). The log file records handshake failures.

**A server keeps timing out** — its process may be waiting for input or
crashed; run the command from `[[mcp.servers]]` manually in a terminal and
watch its output.

## Where things live

- Config: `~/Library/Application Support/Hearth/config.toml` (macOS),
  `%LOCALAPPDATA%\Hearth\` (Windows), `~/.local/share/Hearth/` (Linux)
- Database: `hearth.db` next to the config
- Logs (bodies and tokens redacted): platform log dir, e.g.
  `~/Library/Logs/Hearth/hearth.log` on macOS
- Deleting the config/db directory resets Hearth completely; tokens are
  removed via Permission Center → Disconnect (they're in the OS keyring, not
  in these folders).

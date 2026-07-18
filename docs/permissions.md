# Permissions, by platform

Hearth's Permission Center is the single switchboard for what the assistant may
touch. Everything is **off** until you enable it. Reads run automatically once
granted; writes always show a confirmation card first.

| Permission | Unlocks | macOS | Windows | Linux |
|---|---|---|---|---|
| Gmail | search/read; draft/send with confirmation | ✓ | ✓ | ✓ |
| Calendar | list/find slots; mutations with confirmation | EventKit (native) | Google Calendar API | Google Calendar API |
| Files | tools inside approved folders only | ✓ | ✓ | ✓ |
| System | reveal file, notify, read clipboard, open app; open URL / write clipboard confirmed | ✓ | ✓ (no open-app) | ✓ (no open-app) |
| Shortcuts | run approved macOS Shortcuts (each run confirmed) | ✓ | — | — |
| Browser | read active Chrome tab title/URL | ✓ (Automation prompt) | — | — |
| Web access | fetch pages as text | ✓ | ✓ | ✓ |
| Reminders | list; create/complete with confirmation | EventKit (native) | local list | local list |
| Weather | current conditions via Open-Meteo (no key) | ✓ | ✓ | ✓ |
| MCP servers | tools from configured external servers; every call confirmed | ✓ | ✓ | ✓ |
| Core (always on) | current time, calculator | ✓ | ✓ | ✓ |

## OS-level prompts you may see

**macOS**
- *Calendars* — appears the first time you grant Calendar in Hearth. If you
  refused it once: System Settings → Privacy & Security → Calendars → enable
  for Hearth (or the terminal/IDE you launched it from during development).
- *Automation → Google Chrome* — appears on first use of the Chrome tab tool.
- *Reminders* — appears the first time a reminders tool runs.
- *Notifications* — allow if you want the notify tool to be visible.
- Hearth does **not** request Full Disk Access or Accessibility.

**Windows / Linux**
- No OS prompts; calendar/Gmail go through the Google sign-in flow instead.
- Linux clipboard/notifications use Qt and work on X11 and Wayland.

## Approved folders

File tools resolve every path (symlinks included) and refuse anything that
lands outside your approved roots — `../` tricks and symlink escapes are
tested against. Deletes use the system Trash/Recycle Bin via `send2trash`.

## Approved Shortcuts (macOS)

Add the exact Shortcut name in the Permission Center. Only listed names can
run, every run is confirmed first, and the Shortcut's input is shown on the
card.

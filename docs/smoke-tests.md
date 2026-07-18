# Manual smoke-test checklist

Run before calling a build done. Automated tests cover logic with fakes; this
list exercises the real integrations — one read and one confirmed write per
connector, plus the safety-critical rejection paths.

## Runtime
- [ ] Quit Ollama (`pkill ollama` / Quit menu-bar app), launch Hearth → status
      pill goes *starting…* then *ready*; first message answers without
      manually starting Ollama.
- [ ] Quit Hearth → `pgrep ollama` shows the daemon is gone **only if** Hearth
      started it (leave it running beforehand to check the inverse).
- [ ] Set a bogus model name in Settings → send a message → clear "model isn't
      installed" reply naming the `ollama pull` command; no crash.
- [ ] Press **Stop** mid-generation → streaming halts, "Stopped." note, app
      stays responsive.

## Chat & agent
- [ ] "What time is it?" → uses time_now, correct local time.
- [ ] "What is 379 * 214?" → uses calculate, answers 81,106.
- [ ] Ten rapid messages → no overlapping generations (send disabled while busy).

## Files
- [ ] Read before approving a folder → tool refused with pointer to Permission
      Center.
- [ ] Approve a folder → "list the files in <folder>" works.
- [ ] "Create test-note.txt with 'hello'" → card shows exact path+content;
      **Reject** → file does not exist.
- [ ] Repeat → **Approve** → file exists with right content; both attempts in
      History (rejected / completed).
- [ ] "Delete test-note.txt" → Approve → file is in the Trash, not gone.
- [ ] Ask for a file outside the approved folder by absolute path → refused.

## Calendar
- [ ] Grant Calendar → "what's on my calendar tomorrow" lists real events.
- [ ] "Create 'Hearth smoke test' tomorrow 09:00–09:30" → card shows exact
      times → Approve → event visible in Calendar app.
- [ ] Update its title via chat → Approve → title changed.
- [ ] Delete it via chat → Approve → gone (macOS: also gone from Calendar app).
- [ ] Any mutation → Reject → calendar untouched.

## Gmail
- [ ] Connect Gmail (browser flow completes, status shows Connected).
- [ ] "Summarize my 5 most recent emails" → accurate senders/subjects.
- [ ] "Draft a reply to <someone> saying thanks" → card shows full draft →
      Approve → draft appears in Gmail Drafts (nothing sent).
- [ ] Send-message card → **Reject** → Gmail Sent folder unchanged.
- [ ] Disconnect → search tool now refused.

## System / misc
- [ ] "Open example.com" → card shows exact URL → Approve → browser opens it.
- [ ] "Copy 'hello' to my clipboard" → Approve → paste gives *hello*.
- [ ] (macOS) Approve a harmless Shortcut by name → run via chat → confirmed,
      runs; an unlisted Shortcut name is refused.
- [ ] Web access off → "read example.com" refused; enable → fetch works.

## Reminders
- [ ] Enable Reminders → "what's on my reminder list" works (macOS prompts once).
- [ ] "Remind me to buy milk tomorrow 9am" → card shows title+due → Approve →
      appears in Reminders app (macOS) / listed by Hearth (other OS).
- [ ] Complete it via chat → Approve → gone from open list.

## Weather
- [ ] Weather disabled → "weather in Tokyo" politely refused.
- [ ] Enable Weather → returns current conditions with temperature and wind.

## Skills
- [ ] "/help" (or any unknown /command) lists available commands without
      the model loading.
- [ ] "/today" produces a brief using only the permissions that are granted.
- [ ] A custom skill file in the data-dir skills folder appears and expands.

## MCP (if a server is configured)
- [ ] Server tools appear only after enabling the MCP permission and restart.
- [ ] Every MCP tool call shows a card naming the server; Reject runs nothing.

## Appearance
- [ ] Settings → Theme Light/Dark switch applies immediately; System follows
      the OS appearance change live.

## History & persistence
- [ ] History tab lists every action above with correct status.
- [ ] Relaunch Hearth → approved folders, permissions, history persist;
      no OAuth secrets anywhere in `~/Library/Application Support/Hearth`
      (grep the SQLite file for `ya29` / `refresh_token` → nothing).

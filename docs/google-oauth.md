# Getting your Google OAuth credentials file

Hearth talks to Gmail (and, on Windows/Linux, Google Calendar) with **your own**
Google Cloud OAuth client. That keeps the trust relationship between you and
Google — no third-party server ever sees your mail. One-time setup, ~5 minutes.

## Steps

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign
   in with the Google account you'll use with Hearth.
2. Create a project (top bar → project picker → **New project**). Name it
   anything, e.g. `hearth-personal`.
3. **Enable APIs:** APIs & Services → Library → search **Gmail API** → Enable.
   On Windows/Linux also enable **Google Calendar API**.
4. **OAuth consent screen:** APIs & Services → OAuth consent screen.
   - Audience: **External** is fine.
   - Fill in the app name (`Hearth`) and your email; skip optional branding.
   - Add your own Google account under **Test users**. (In "Testing" mode only
     test users can sign in — that's exactly what you want, and it never needs
     Google review.)
5. **Create the credential:** APIs & Services → Credentials → Create
   credentials → **OAuth client ID** → Application type: **Desktop app**.
6. Click **Download JSON**. You'll get a file like
   `client_secret_1234…apps.googleusercontent.com.json`. Keep it somewhere
   private (not in a synced/public folder).
7. In Hearth: **Settings → Google credentials → Browse…**, pick the file, Save.
8. **Permissions → Connect Gmail.** Your browser opens; sign in and allow the
   requested scopes. Google will warn the app is unverified — that's your own
   test-mode app; click *Continue*.

## What Hearth asks for, and where secrets live

- Scopes: `gmail.readonly`, `gmail.compose`, `gmail.send` — and on
  Windows/Linux `calendar.readonly` + `calendar.events`. Nothing else.
- The refresh token is stored in your OS credential store (macOS Keychain,
  Windows Credential Locker, Linux Secret Service). It is never written to
  disk, SQLite, or logs.
- Disconnecting in the Permission Center deletes the stored token. You can also
  revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Notes

- In Testing mode Google expires refresh tokens after ~7 days of non-use;
  reconnecting takes one click. To avoid that, publish the consent screen to
  "In production" (no review needed for these scopes with a personal app).
- If the browser flow fails with `redirect_uri_mismatch`, re-check that the
  client type is **Desktop app**, not Web.

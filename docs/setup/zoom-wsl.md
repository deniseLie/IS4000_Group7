# Zoom meetings: running the backend in WSL

Checked 11 Nov 2026 on Windows 11 with WSL 2 and Ubuntu 24.04 (Python 3.12.3).

Zoom is **optional**. Without it, everything else works, including meeting minutes: the RM uploads a transcript file (`.vtt` or `.txt`, for example [`demo-files/lombard-call.vtt`](../workflows/demo-files/lombard-call.vtt)). You only need this page to press **Create Zoom link** on a meeting, or to fetch Zoom My Notes automatically.

## Why WSL

The backend keeps the Zoom sign-in (an OAuth grant) in two files, and refuses to read them unless only their owner can open them (folder `chmod 700`, files `chmod 600`; `app/services/meetings/runtime.py`). Windows has no such permission bits, so on Windows the check always fails with *"owner-only checks need macOS or Linux"*. On macOS or Linux, skip the WSL parts and follow the same steps.

So on Windows:

| Part | Where it runs |
|---|---|
| Backend API and the meeting worker (`uvicorn app.main:app`) | WSL (Ubuntu) |
| Frontend (`npm run dev`) | Windows, as usual |
| Repository and `.env` | The Windows folder, read from WSL through `/mnt/c/...` |
| Zoom grant folder | The WSL home folder (`/home/<you>/...`), **never** under `/mnt/c` |

The frontend reaches the backend through Vite's `/api` proxy on `127.0.0.1:8000`; WSL 2 forwards that port from Windows to Ubuntu.

## 1. Install WSL and a Linux venv (once)

In PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Then in the Ubuntu terminal (Python 3.12 is already installed on 24.04):

```bash
sudo apt-get update && sudo apt-get install -y python3.12-venv
python3 -m venv ~/.venvs/demobank
cd /mnt/c/<path to the repository>/backend
~/.venvs/demobank/bin/python -m pip install -e '.[dev]'
```

Keep this venv in the Linux home folder. The Windows `.venv` cannot run in Linux (it has `Scripts\python.exe`), and a venv under `/mnt/c` is slow.

## 2. Create the Zoom app (once per Zoom account)

The meetings are hosted by the Zoom account that signs in at step 3, not the account signed in to your browser or the Zoom app. Sign in to the [Zoom App Marketplace](https://marketplace.zoom.us/) **as that account**, then **Develop → Build App → General App**:

1. **OAuth:** user-managed app; turn **public client** on (PKCE, no client secret is needed).
2. **Redirect URL:** `http://localhost:8765/callback`, in both the redirect URL and the OAuth allow list.
3. **Scopes:** `meeting:write:meeting`, `meeting:read:meeting` and `user:read:user` (the last one lets the script show which account signed in; the automatic My Notes path needs it too).
4. Copy the app's **Client ID**. It is not a secret, but keep it out of Git anyway. There is no client secret to copy.

## 3. Sign in and save the grant

In Ubuntu, from `backend/`:

```bash
~/.venvs/demobank/bin/python -m scripts.zoom_authorize --client-id <the app's Client ID> --directory ~/.demobank-zoom
```

The script prints a link and waits 5 minutes. Open the link in your Windows browser, sign in as the hosting Zoom account and approve. Zoom sends the browser to `localhost:8765`, which reaches the script in WSL. The script then:

- saves `zoom-tokens.json` and `zoom-public-client.json` in the new folder, owner-only (it never overwrites an existing grant, so each account gets its own folder);
- prints which account signed in and the granted scopes, and never prints a token.

If Zoom refuses an `http://localhost` redirect, register another redirect URL on the app and pass it with `--redirect <url>`; the script then asks you to paste the address the browser ends on (even if that page does not load).

## 4. Settings in `.env`

The repository's root `.env` (on Windows) is read by the backend in WSL as well. Add:

```dotenv
MEETINGS_ENABLED=true
# Linux path: only a backend running in WSL can use it. A Windows backend refuses it.
ZOOM_OAUTH_DIRECTORY=/home/<you>/.demobank-zoom
# Run the meeting worker inside the API process (or start it on its own, see step 5).
RUN_MEETING_WORKER_IN_API=true
MEETINGS_DATABASE_URL=postgresql://<meeting login>.<project-ref>:<password>@<pooler host>:5432/postgres?sslmode=require
MEETINGS_CHECKPOINT_SIGNING_KEY=<43 or more random characters>
```

- **`MEETINGS_DATABASE_URL`** is a restricted database login for the meeting worker, never the `postgres` owner. Create it once in the Supabase SQL editor (choose your own password and keep it out of Git):
  ```sql
  create role demobank_meeting_worker_runtime login password '<your password>';
  grant demobank_meetings to demobank_meeting_worker_runtime;
  ```
  Then copy the **Session pooler** string from Supabase **Connect**, replace the user `postgres` with `demobank_meeting_worker_runtime` (keep the `.<project-ref>` after it) and put in the password. The worker checks at start that the login has only `demobank_meetings` membership and no owner or admin rights.
- **`MEETINGS_CHECKPOINT_SIGNING_KEY`**: generate once with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and keep it stable.
- `ZOOM_CAPTURE_ENABLED` stays `false` unless you set up the automatic path (section 7).

## 5. Start

Ubuntu terminal (backend and meeting worker):

```bash
cd /mnt/c/<path to the repository>/backend
~/.venvs/demobank/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows terminal (frontend), as usual:

```powershell
cd frontend
npm run dev
```

With `RUN_MEETING_WORKER_IN_API=false`, start the worker in a second Ubuntu terminal instead: `~/.venvs/demobank/bin/python -m app.services.meetings.worker` (from `backend/`).

Alternative without database URLs in `.env`: put them in an owner-only runtime file and start the API with `python -m scripts.run_meeting_api --runtime <file> --port 8000` and the worker with `python -m scripts.run_meeting_worker --runtime <file>` (see the scripts' docstrings for the file's fields).

## 6. Check it works

1. `curl http://127.0.0.1:8000/health` from Windows answers (the port is forwarded).
2. Signed in as Sophie, the browser's `GET /api/meetings/capabilities` returns `"zoom_configured": true`.
3. Sophie: **Schedule → Appointments → Open meeting → Create Zoom link** on a confirmed meeting that has not started. Both Sophie and Michelle see the same join link, and Michelle gets an Inbox notice.

A Zoom link can only be created before the meeting's start time, and only by the client's RM.

## 7. Optional: automatic transcripts (Zoom My Notes)

Set `ZOOM_CAPTURE_ENABLED=true` as well. Sophie starts **My Notes** in the Zoom call from the hosting account, completes the note, ends the meeting once, presses **Meeting ended** and then **Check completed My Notes**; the worker fetches the note for that meeting and she maps the speakers. Without anyone pressing, the worker checks 2 hours after the recorded end.

For Zoom to report call start and end by itself, run the separate webhook listener behind a tunnel (never expose the customer API through it), with its own restricted login (`demobank_meeting_ingress`) in `ZOOM_INGRESS_DATABASE_URL` and the app's webhook secret in `ZOOM_WEBHOOK_SECRET`:

```bash
~/.venvs/demobank/bin/python -m uvicorn app.services.meetings.zoom_events:create_app --factory --host 127.0.0.1 --port 8090
```

Keep the transcript upload as the fallback: My Notes only exists if someone in the call started and completed it.

## Switching the hosting Zoom account

Create a Zoom app as the new account (step 2), run step 3 with a **new** `--directory`, point `ZOOM_OAUTH_DIRECTORY` at it and restart the backend and worker. Meetings that already have a link keep the old account's link.

## If something doesn't work

| What happens | What to check |
|---|---|
| *"…must be regular owner-only files (chmod 600) … owner-only checks need macOS or Linux"* | The backend is running on Windows. Start it in WSL (step 5). |
| *"…must be regular owner-only files (chmod 600)"* or *"…owner-only (chmod 700)"* in WSL | The folder is under `/mnt/c`, or its permissions changed. Keep it in `/home/<you>`; `chmod 700 <folder>` and `chmod 600 <folder>/*.json`. |
| `zoom_not_configured` (503) | `ZOOM_OAUTH_DIRECTORY` is not set in the backend's environment. Restart after editing `.env`. |
| *"Zoom OAuth refresh remained busy for 35 seconds"* | The API and the worker refreshed the token at the same moment. Try again. |
| Zoom answers 401 or *invalid_grant* | The grant was revoked or the app deleted or changed. Run step 3 again into a new folder. |
| The script says the grant lacks a scope | Add the scope to the app (step 2) and sign in again; nothing was saved. |
| No sign-in arrives within 5 minutes | The browser could not reach `localhost:8765` in WSL. Check WSL's localhost forwarding, or use `--redirect` with another registered URL and paste the address. |
| The frontend cannot reach the backend | `curl http://127.0.0.1:8000/health` from Windows. If it fails, start Vite with `BACKEND_HOST=<address from hostname -I in Ubuntu>` and the backend with `--host 0.0.0.0`. |
| The meeting worker stops with *"requires a non-owner LOGIN with only demobank_meetings membership"* | `MEETINGS_DATABASE_URL` uses the owner or a login with other roles. Create the restricted login in step 4. |

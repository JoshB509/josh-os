# Josh OS

Josh OS (also called DaJoshiResolve) is Josh's local desktop app for running the CareerOS business and his YouTube production. It runs only on his Windows PC as a local web app on 127.0.0.1, opened in a Chrome or Edge app window. It covers video editing passes (transcribe, clean cut, flubs, keying, animations and Premiere XML export), course lesson recording with OBS, thumbnails, writing and email drafts, Kit, and an Admin area for clients and customers that pulls from Stripe and Calendly.

Status is pre-alpha (version 0.1.0-alpha).

## How it starts

`Josh OS.exe` is a small launcher compiled from `ui/StudioLauncher.cs` by `build-josh-os.ps1`. It reads the Python path from `settings.json` and runs `studio_app.py`.

`studio_app.py` is the real entry point. It starts the server in `studio/server.py` on a free local port, writes the address and session token to `Data/runtime.json`, and opens the browser window. If an instance is already running it reuses that one, and `Restart DaJoshiResolve.bat` stops the old server and opens a fresh one. To run it by hand:

```
python studio_app.py              # start and open the window
python studio_app.py --no-browser # server only
python studio_app.py --demo       # demo data
```

`studio/server.py` has no entry point of its own, so always start it through `studio_app.py`.

`worker.py` is the command-line worker for the video editing jobs. The app and `DaJoshiResolve.exe` (built from `ui/Program.cs` by `build.ps1`) call it with a run folder, so you only run it directly when debugging a job:

```
python worker.py --job <run folder> [--request <request file>] [--resume]
```

## Setup

Copy `settings.example.json` to `settings.json` and fill in the three paths. `settings.json` stays out of git because it holds paths that only exist on this PC.

The app runs on Python 3.12 on Windows (some older caches were built with 3.10). It needs these packages:

```
numpy pillow av faster-whisper opencv-python scipy lxml imageio-ffmpeg websocket-client playwright torch
```

Playwright needs its browser installed with `playwright install chromium`, since it renders the HTML animations and runs the browser tests. torch is only used by the keying step.

**ffmpeg** is expected at `tools/ffmpeg.exe`, or wherever the `ffmpeg` path in `settings.json` points.

**The Whisper model** is faster-whisper `medium.en`, stored as a local folder with `model.bin` inside. Point the `model` path in `settings.json` at it. The app never downloads models, so it has to be on disk already.

**The keying engine** (CorridorKey) is expected in `Engines/CorridorKey` with its own `venv`.

No API keys are written in the code. Stripe and Calendly keys are entered on the Admin page and saved to `Data/admin.json`, and the Kit key is entered on the Email page and saved to `Data/kit.json`. See `.env.example`.

## What's left out of git

| Left out | Why |
|---|---|
| `Data/` | 4+ GB of live app data, including the saved Stripe, Calendly and Kit keys, client details, the app's browser profile with its sign-ins, and the key cache |
| `browser-profile/`, `key-cache/` | Sign-ins and cached keys, wherever they sit |
| `.env`, `*.key`, `*.pem` | Secrets |
| `settings.json` | Paths for this PC only |
| `Runs/` | Output from every editing run, which is large and can be regenerated |
| `Engines/` | The CorridorKey keying engine and its virtual environment, which is large and installed separately |
| `Backups/`, `Evidence/` | Old copies and test evidence, which are large and not needed to run the app |
| `References/` | 165 MB of reference libraries (creator hooks, email examples, thumbnail sources) |
| `tools/` | ffmpeg and other binaries |
| `*.exe`, `build/`, `dist/` | Built launchers, rebuilt from `ui/*.cs` with `build.ps1` and `build-josh-os.ps1` |
| `__pycache__/`, `*.pyc`, `*.log` | Caches and logs |

## Workflows and the team

The side rail is grouped by workflow. **Calendar** shows videos, carousels, emails and tasks together. **YouTube videos**, **Carousels**, **Editing** and **Email** each have their own page, and **Clients** sits at the bottom until the client workflow is built.

**Team** holds the AI team members, who are Sam (Content & YouTube Manager), Tariq (Video Editor), Laia (Email Writer) and Max (Business Manager, coming later). Each profile has a name, photo, colour, mission, responsibilities, workflows, skills and a check-in schedule, all editable on the page. The **Lessons learned** log on the same page collects what works and what doesn't, so every agent can read it before starting work. The agents don't run yet, and the chat box on each profile is a placeholder for the next build. Profiles and lessons live in `studio/team.py` (default photos in `studio/team_photos/`), carousels in `studio/carousels.py`.

Video pages, carousels and team profiles save themselves a moment after you stop typing. Ctrl+S still saves a video straight away. Script edits made within 15 minutes of each other are folded into one draft version, so the version history stays readable.

## Using Josh OS on your phone

Josh OS only listens on this PC. To reach it from a phone, it uses Tailscale, which gives your phone a private, encrypted route to this PC that nobody outside your Tailscale account can use.

1. Install Tailscale on this PC and on your phone, and sign in to the same account on both.
2. Find this PC's Tailscale name in the Tailscale app (it looks like `josh-pc.tail1234.ts.net`) and add it to `settings.json`:
   ```json
   "phone_access": {"host": "josh-pc.tail1234.ts.net", "port": 8765}
   ```
3. Restart Josh OS. It now always runs on port 8765.
4. On this PC, run `tailscale serve --bg 8765` once. Tailscale keeps forwarding `https://josh-pc.tail1234.ts.net` to Josh OS after restarts.
5. In Josh OS, click **Use on your phone** at the bottom of the side rail, copy the link, and open it on your phone. Then use **Add to Home Screen** so it opens like an app.

The phone link contains a key, which is stored in `Data/phone-access.json` and stays the same across restarts. To sign every phone out, delete that file and restart Josh OS. Never use `tailscale funnel`, because that would put Josh OS on the public internet.

## Layout

- `studio/` is the app itself. It holds the server, its modules (admin, Kit, lessons, thumbnails, writing, tasks, jobs, team, carousels, phone access) and the web UI in `studio/web/`.
- `dajoshi/` is the video engine (media, transcription, editing plan, keying, animation, export).
- `worker.py`, `keying_runner.py`, `export_saved.py`, `reedit_saved.py` and `script_bridge.py` are the command-line tools.
- `ui/` holds the C# launchers.
- `specs/` and `writing_passes/` hold the editing and writing rules, and `tests/` holds the tests.
- `Inputs/`, `examples/` and `reference_sources/` are small sample and reference inputs.

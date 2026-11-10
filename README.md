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

**Team** holds the AI team members, who are Sam (YouTube Channel Manager), Tariq (Video Editor), Laia (Email Copywriter), Saskia (Learning & Development / Course Designer), Anouk (Customer Success Manager) and Max (Business Manager, coming later). Who each of them is, and how they talk to you, is set in `team/personalities.md`, which every team member reads before they answer. Edit that file to change how they come across. Each profile has a name, photo, colour, mission, responsibilities, workflows, skills and a check-in schedule, all editable on the page. The **Lessons learned** log on the same page collects what works and what doesn't, so every agent can read it before starting work. The agents don't run yet, and the chat box on each profile is a placeholder for the next build. Profiles and lessons live in `studio/team.py` (default photos in `studio/team_photos/`), carousels in `studio/carousels.py`.

Video pages, carousels and team profiles save themselves a moment after you stop typing. Ctrl+S still saves a video straight away. Script edits made within 15 minutes of each other are folded into one draft version, so the version history stays readable.

## Video results and Sam's goals

Every video has a **Results** tab. It holds the funnel stage (top, middle or bottom), the chain from views to subscribers to email signups to customers with the rates between them, each round of thumbnail testing (the Test & Compare screenshot, what changed, each thumbnail's text, look and watch-time share, the winner and why it won), and the retention graph with each drop and hold matched to the script line at that point. Drop screenshots straight onto it. Everything saves as you type.

"Ask Sam to review this video" sends Sam the video's script and results. He opens the screenshots, checks YouTube Studio and Linklr for the numbers, fills in the tab, writes a short review and saves what the video taught to the team's lessons. He can also do this from his chat whenever you mention a video.

What Sam is aiming for, and how he judges a video, is in `team/goals/sam.md`. Edit it to change his priorities. Any team member can have a goals file at `team/goals/<id>.md`.

## Customer success and Anouk

The **Customer success** page is Anouk's tracker. It holds everyone she looks after across four pathways: people who replied to a newsletter, leads from calls, clients, and course buyers. Each person has a stage, a summary, the next step with a date, anything you promised them, and a history of every touchpoint. Overdue check-ins and promises rise to the top, and you can edit any of it yourself. Anouk keeps it up to date from her chat, and the dock on that page is Anouk, so she knows who you have open.

What she's aiming for, and how each pathway works, is in `team/goals/anouk.md`. Step-by-step flows you write for her, such as your nurture conversation flow, go in `team/playbooks/anouk/` as Markdown files. She gets each one in full and follows it exactly.

Anouk starts in **setup mode**. She can read Josh OS and her files, keep the tracker, and write messages in chat for you to send, but she has no Gmail, Calendly, Kit, Linklr or Chrome. When you're ready, add `"team_live": ["anouk"]` to `settings.json`. She can then read Gmail and save replies as Gmail drafts (never send), read Calendly and make single-use booking links, and read Kit and Linklr.

## The team at work

Sam, Tariq, Laia and Max run through Claude Code on this PC. When you ask Sam something in the script studio, Josh OS calls `claude -p` with his profile from the Team page, the team's lessons learned, and his skills from `.claude/skills/` (Sam has `script-touch-up`, and Tariq has `video-editing-prep`). He gets read-only access to `reference_sources/`, which holds the Ed Lawrence movement library, curiosity lines, audience notes and magnet index, so he can look things up but can't change files. Every suggestion comes back as a rewrite you can replace or copy. Nothing changes in the script until you click Replace, and each change is kept in the script's version history.

Claude Code needs to be installed on this PC and signed in to your Claude account. Josh OS finds it on the PATH, or you can set its full path in `settings.json` as `"claude": "C:\\Users\\you\\.local\\bin\\claude.exe"`. Conversations are saved per video in the database, so you can pick them up from your phone.

To update a skill, replace its `SKILL.md` in `.claude/skills/`. Claude Code sessions opened in this folder use the same files.

## Talking to the team on Discord

Each team member can have their own Discord bot, so you can message Sam, Tariq, Laia, Saskia or Anouk from the Discord app on any device. Josh OS runs a small bridge on this PC that picks up your message, passes it to that person's normal chat (the same conversation you see in the app, with the same tools), and posts the reply back from their bot. You can attach images and files, and they can send files back to you.

1. Go to the Discord Developer Portal, click **New Application** and name it after the team member (for example Sam). On the **Bot** page, set the bot's name and picture, turn on **Message Content Intent**, then click **Reset Token** and keep the token on screen for step 4.
2. On the **OAuth2** page, open the URL Generator, tick `bot`, then tick **View Channels**, **Send Messages**, **Attach Files** and **Read Message History**. Open the link it makes and add the bot to your private server.
3. In your server, make a channel for each bot, such as `#sam`. Each bot answers in its own channel, in direct messages, and anywhere you @mention it.
4. On this PC, run `pip install "discord.py>=2.3"`, then `python discord_setup.py`. It asks for your Discord user id and each bot's token. Tokens are hidden while you type and are saved in `Data/discord.json`, which stays out of git. Never paste a token into a chat.
5. Restart Josh OS.

The bots only answer your Discord user id, so nobody else in a server can make a team member do anything. Files you send are kept in `Data/discord/inbox/`, and images go into Josh OS like any other image. Anouk stays in setup mode on Discord too, so she can't use Gmail, Kit, Linklr or Calendly until she is listed in `team_live`. To check which bots are connected, open `/api/discord` in Josh OS.

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

## What the team's jobs can touch

Every chat with a team member (their Team page, the dock, the chat beside a script, and Discord) goes through the same code in `studio/agent.py`, so a request reaches the same actions wherever Josh asks. Ordinary chat is read-only (Read, Grep, Glob). Work that changes files runs as a job with limits Josh OS enforces:

- **Sam's thumbnails** (`studio/video_thumbnails.py`, `studio/thumbnail_build.py`). Sam only picks options with read-only tools. Josh OS checks every value (format middle or close, a pose by name from the Pose Catalogue, numbers within set ranges, plain words), names the output itself inside the video's `thumbnails` folder, and runs the one fixed builder with an argument list, never a shell. The builder imports only `oneline.py` or `close_unit.py` from `E:\CareerOS\12_YouTube\_thumbnail-system\tools`, reads the template backgrounds, the pose cutout and the chosen font, and refuses any output that isn't a new PNG in a video's thumbnails folder. Sam picks a font by name from the list `FONTS` in `thumbnail_build.py` (local files only, never a path), plus a weight, colours by name or hex code, a size from 60 to 360 px, bar on or off, outline, thickening, shadow and upper case. Each value is checked in `video_thumbnails.py` and again in the builder. Words that would run off the frame are shrunk and Josh is told. Words that won't fit at 60 px are refused, and so is a font whose file is missing. To add a font, add its file path to `FONTS`.
- **Tariq's deck changes**, from the animation review or any chat. Write and Edit are allowed only inside that video's folder, using Claude Code's `Edit(//e/<folder>/**)` rule. A bare Write or Edit would allow writing anywhere, which was tested.
- **Saskia's lesson jobs.** Write and Edit are held to the course's folder the same way. Her guide and thumbnail jobs still have Bash, which is not path-limited.

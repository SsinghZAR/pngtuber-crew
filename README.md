# PNGTuberBot (pngtuber-crew)

Windows-first desktop bot that:
- Monitors a Discord voice channel
- Joins the voice channel (listen-only) to detect who is speaking
- Controls individual OBS sources (per-user avatar + mute/deaf icons)

## Download & run (recommended)
1. Go to the repo’s **Releases** page on GitHub and download `PNGTuberBot-windows.zip`.
2. Extract it to a folder (e.g. `C:\PNGTuberBot\`).
3. Run `Setup.exe` to create/edit `config.yaml` (it saves **next to the EXE**).
4. Run `RunBot.exe` when you want the automation running during stream.

## Quick start (run from source)

### Prereqs
- Windows 10/11
- Python 3.12+
- OBS Studio 28+ (built-in WebSocket v5)

### Install deps (no venv activation required)
PowerShell execution policies sometimes block `Activate.ps1`. You can still use the venv directly:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Create config
- Copy `config.yaml.example` → `config.yaml`
- Fill in:
  - `discord.bot_token`
  - `discord.guild_id`
  - `discord.voice_channel_id`
  - `obs.scene_name`
  - users list + GIF paths

### Run Setup GUI (recommended)

```powershell
$env:PYTHONPATH="src"
.\venv\Scripts\python.exe -m pngtuberbot.setup_gui
```

### Run the bot

```powershell
$env:PYTHONPATH="src"
.\venv\Scripts\python.exe -m pngtuberbot --config config.yaml
```

## OBS setup
1. OBS → Tools → WebSocket Server Settings
2. Enable WebSocket Server
3. Set password (optional)
4. Port `4455` (default)

## Discord bot setup
1. Create an application + bot in the Discord Developer Portal
2. Enable intents:
   - Server Members Intent
   - (Voice States are required)
3. Invite the bot to your server with permissions:
   - View Channels
   - Connect

## What gets created in OBS
Per user ID `123...`:
- Avatar: `pngtuber_123...`
- Mute icon: `pngtuber_123..._mute`
- Deaf icon: `pngtuber_123..._deaf`

## Privacy notes
- The bot **does not record or save audio**.
- It joins the voice channel only to detect speaking state and receives PCM frames in-memory.

## Building Windows EXEs (PyInstaller)
Install build deps:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

Build:

```powershell
.\venv\Scripts\python.exe -m PyInstaller RunBot.spec
.\venv\Scripts\python.exe -m PyInstaller Setup.spec
```

Outputs appear under `dist/`.


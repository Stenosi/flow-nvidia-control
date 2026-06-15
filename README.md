# Flow.Launcher.Plugin.NvidiaControl

[![Build & Release](https://github.com/Stenosi/Flow.Launcher.Plugin.NvidiaControl/actions/workflows/release.yml/badge.svg)](https://github.com/Stenosi/Flow.Launcher.Plugin.NvidiaControl/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/Stenosi/Flow.Launcher.Plugin.NvidiaControl)](https://github.com/Stenosi/Flow.Launcher.Plugin.NvidiaControl/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A [Flow Launcher](https://www.flowlauncher.com/) plugin to control your NVIDIA GPU without leaving the keyboard.

## Features

| Command | Description |
| --- | --- |
| `nv info` | GPU name, installed driver version, total VRAM |
| `nv driver` | Compare installed driver with the latest from NVIDIA |
| `nv changelog` | Open the latest NVIDIA driver release notes in the browser |
| `nv stats` | Live GPU utilization %, VRAM usage, core temperature |
| `nv clips [game]` | List recent NVIDIA recordings, filterable by game name |
| `nv shots [game]` | List recent NVIDIA screenshots, filterable by game name |
| `nv open` | Launch NVIDIA App |

## Requirements

- Windows 10/11 64-bit
- [Flow Launcher](https://github.com/Flow-Launcher/Flow.Launcher/releases/latest) 1.8.0+
- NVIDIA GPU with drivers installed

## Installation

### Via Flow Launcher Plugin Store (recommended)

Open Flow Launcher → Settings → Plugin Store → search **NvidiaControl** → Install.

### Manual

1. Download `Flow.Launcher.Plugin.NvidiaControl.zip` from the [latest release](https://github.com/Stenosi/Flow.Launcher.Plugin.NvidiaControl/releases/latest).
2. Extract into `%APPDATA%\FlowLauncher\Plugins\NvidiaControl-73bb4ffd-4f56-461b-99ca-d9ddee0a61dc\`.
3. Restart Flow Launcher.

## Usage

Type `nv` in Flow Launcher followed by a subcommand:

```text
nv              → show all available subcommands
nv info         → GPU name, driver version, VRAM
nv driver       → check for driver updates
nv changelog    → open latest driver release notes
nv stats        → live GPU %, VRAM, temperature
nv clips        → list all recent NVIDIA recordings
nv clips fortnite → list clips from Fortnite sessions (fuzzy match)
nv shots        → list all recent screenshots
nv shots cyberpunk → list Cyberpunk 2077 screenshots (fuzzy match)
nv open         → launch NVIDIA App
```

## Configuration

Default paths used by the plugin:

| Setting | Default path |
| --- | --- |
| Video clips | `~/Videos/NVIDIA App` |
| Screenshots | `~/Pictures/NVIDIA App` |

If NVIDIA App uses a custom save location, update `CLIPS_DIR` and `SHOTS_DIR` at the top of `main.py`.

## Development

```powershell
git clone https://github.com/Stenosi/Flow.Launcher.Plugin.NvidiaControl
cd Flow.Launcher.Plugin.NvidiaControl

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Link the repo into Flow Launcher (run PowerShell as Administrator)
$uuid = "73bb4ffd-4f56-461b-99ca-d9ddee0a61dc"
New-Item -ItemType Junction `
  -Path "$env:APPDATA\FlowLauncher\Plugins\NvidiaControl-$uuid" `
  -Target (Get-Location)
```

Press `Ctrl+R` in Flow Launcher to reload plugins after changes.

### Releasing a new version

```bash
# 1. Bump Version in plugin.json
# 2. Commit and tag
git add plugin.json
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push && git push origin vX.Y.Z
# GitHub Actions will build and attach the zip to the release automatically
```

## License

MIT — see [LICENSE](LICENSE)

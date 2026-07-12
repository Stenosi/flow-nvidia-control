# Flow.Launcher.Plugin.NvidiaControl

[![Build & Release](https://github.com/Stenosi/flow-nvidia-control/actions/workflows/release.yml/badge.svg)](https://github.com/Stenosi/flow-nvidia-control/actions/workflows/release.yml)
[![GitHub release](https://img.shields.io/github/v/release/Stenosi/flow-nvidia-control)](https://github.com/Stenosi/flow-nvidia-control/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A [Flow Launcher](https://www.flowlauncher.com/) plugin to monitor and control your NVIDIA GPU without leaving the keyboard.

## Features

| Command | Description |
| --- | --- |
| `nv info` | GPU name, driver type (Game Ready / Studio), update status, total VRAM |
| `nv stats` | Live GPU utilization %, VRAM usage, core temperature |
| `nv changelog` | Open the latest NVIDIA driver release notes in the browser |
| `nv clips [game]` | List recent NVIDIA recordings, filterable by game name |
| `nv shots [game]` | List recent NVIDIA screenshots, filterable by game name |
| `nv settings` | View and edit clips/screenshots directory paths |

## Requirements

- Windows 10/11 64-bit
- [Flow Launcher](https://github.com/Flow-Launcher/Flow.Launcher/releases/latest) 1.8.0+
- NVIDIA GPU with drivers installed
- Python 3.11+ (installed automatically by Flow Launcher)

## Installation

### Via Flow Launcher Plugin Store (recommended)

Open Flow Launcher → Settings → Plugin Store → search **NvidiaControl** → Install.

### Manual

1. Download `Flow.Launcher.Plugin.NvidiaControl.zip` from the [latest release](https://github.com/Stenosi/flow-nvidia-control/releases/latest).
2. Extract into `%LocalAppData%\FlowLauncher\app-<version>\Plugins`.
3. Restart Flow Launcher.

## Usage

Type `nv` in Flow Launcher followed by a subcommand:

```text
nv                    → show all available subcommands
nv info               → GPU name, driver type, update status, VRAM
nv stats              → live GPU %, VRAM, temperature (refresh with Ctrl+R)
nv changelog          → open latest driver release notes in browser
nv clips              → list all recent NVIDIA recordings
nv clips [game]       → list clips for [game] (fuzzy match)
nv shots              → list all recent NVIDIA screenshots
nv shots [game]       → list [game] screenshots (fuzzy match)
nv settings           → view current directory paths, open config file
```

### nv info - driver update check

`nv info` shows whether your installed driver is up to date by querying NVIDIA's download database. It detects the driver type from your installation.
- **Game Ready Driver** - checked against the latest GRD release
- **Studio Driver** - checked against the latest NSD release, with fallback to GRD if not available for your GPU
When an update is available, clicking the result opens the **NVIDIA App** or **GeForce Experience** directly (if installed), otherwise opens the specific driver download page on nvidia.com.

### nv clips / nv shots - media browser

Clips and screenshots are listed sorted by most recently modified. Subdirectories are treated as game folders:
```text
nv clips              → list game folders
nv clips fortnite     → list clips inside the Fortnite folder (fuzzy match)
```
Clicking a file opens it. Clicking a folder opens it in Explorer.

## Configuration

Type `nv settings` to open `config.json` directly from Flow Launcher.
Default paths:

| Setting | Default |
| --- | --- |
| Video clips | `%USERPROFILE%\Videos\NVIDIA` |
| Screenshots | `%USERPROFILE%\Videos\NVIDIA` |

To use custom location, edit `config.json` in the plugin folder:

```json
{
  "clips_dir": "D:\\Recordings\\NVIDIA",
  "shots_dir": "D:\\Screenshots\\NVIDIA"
}
```

Changes take effect immediately - no restart required.

## Development

```powershell
git clone https://github.com/Stenosi/flow-nvidia-control
cd flow-nvidia-control

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

To test changes, copy `main.py` over the installed plugin file:
`Copy-Item ".\main.py" "$env:LOCALAPPDATA\FlowLauncher\app-<version>\UserData\Plugins\NvidiaControl-1.0.4\main.py"`

Then press `Ctrl+R` in Flow Launcher to reload plugins.

### Releasing a new version

```bash
# 1. Bump Version in plugin.json
# 2. Commit, tag and push
git add plugin.json main.py
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push && git push origin vX.Y.Z
# GitHub Actions builds the zip and creates the release automatically.
# The Flow Launcher plugin manifest CI picks up the new release within 3 hours.
```

## License

MIT - see [LICENSE](LICENSE)

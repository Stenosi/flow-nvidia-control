"""
flow-nvidia-control
Keyword: nv
Subcommands: info | changelog | stats | clips [game] | shots [game]
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Add lib/ to sys.path so Flow Launcher can find bundled dependencies
_lib = _Path(__file__).parent / "lib"
if _lib.exists() and str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests
from pyflowlauncher import Plugin, Result, send_results
from pyflowlauncher.result import JsonRPCResponse as ResultResponse
from pyflowlauncher.api import open_url, open_uri, shell_run

plugin = Plugin()


def _change_query(query: str) -> dict:
    """ChangeQuery action that keeps Flow Launcher open after execution."""
    return {
        "Method": "Flow.Launcher.ChangeQuery",
        "Parameters": [query, True],
        "DontHideAfterAction": True,
    }

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ICON = "Images/icon.png"

_DEFAULT_CLIPS = r"%USERPROFILE%\Videos\NVIDIA"
_DEFAULT_SHOTS = r"%USERPROFILE%\Pictures\NVIDIA"
_CONFIG_PATH = _Path(__file__).parent / "config.json"

GPU_DATA_URL = "https://raw.githubusercontent.com/ZenitH-AT/nvidia-data/main/gpu-data.json"
PROCESS_FIND_URL = "https://www.nvidia.com/Download/processFind.aspx"
NVIDIA_DRIVERS_URL = "https://www.nvidia.com/en-us/drivers/results/"
NVIDIA_RESULTS_URL = "https://www.nvidia.com/download/driverResults.aspx/{id}/en-us"
OS_ID = 57   # Windows 10/11 64-bit
DCH_ID = 1   # DCH (modern) driver packaging
HTTP_TIMEOUT = 5  # seconds

# Driver check cache — avoids HTTP calls on every keystroke
_driver_cache: dict = {"data": None, "timestamp": 0.0}
CACHE_TTL = 300  # 5 minutes

# Optional dependencies — degrade gracefully if missing
try:
    import pynvml as _pynvml
    _NVML_AVAILABLE = True
except ImportError:
    _NVML_AVAILABLE = False

try:
    import wmi as _wmi
    _WMI_AVAILABLE = True
except ImportError:
    _WMI_AVAILABLE = False

try:
    from thefuzz import fuzz as _fuzz
    from thefuzz import process as _fuzz_process
    _FUZZ_AVAILABLE = True
except ImportError:
    _FUZZ_AVAILABLE = False


# ---------------------------------------------------------------------------
# WMI helpers
# ---------------------------------------------------------------------------

def get_gpu_info_wmi() -> dict:
    """Return GPU name, driver version and VRAM via WMI."""
    if not _WMI_AVAILABLE:
        raise RuntimeError("'wmi' library not installed. Run: pip install wmi")

    c = _wmi.WMI()
    gpus = c.Win32_VideoController()
    nvidia_gpu = next(
        (
            g for g in gpus
            if "NVIDIA" in (g.AdapterCompatibility or "")
            or "NVIDIA" in (g.Name or "")
        ),
        None,
    )
    if not nvidia_gpu:
        raise RuntimeError("No NVIDIA GPU detected by the system")

    raw_ver = nvidia_gpu.DriverVersion or ""
    driver_ver = _parse_wmi_driver_version(raw_ver)
    vram_bytes = nvidia_gpu.AdapterRAM or 0
    vram_mb = vram_bytes // (1024 * 1024)

    return {
        "name": nvidia_gpu.Name or "NVIDIA GPU",
        "driver_version": driver_ver,
        "vram_mb": vram_mb,
    }


def _parse_wmi_driver_version(raw: str) -> str:
    """Convert WMI version '31.0.15.3162' to NVIDIA display format '531.62'."""
    digits = raw.replace(".", "")
    if len(digits) >= 5:
        return f"{digits[-5:-2]}.{digits[-2:]}"
    return raw




# ---------------------------------------------------------------------------
# pynvml helpers
# ---------------------------------------------------------------------------

def get_gpu_stats_nvml() -> dict:
    """Return live GPU utilization %, VRAM and temperature via pynvml."""
    if not _NVML_AVAILABLE:
        raise RuntimeError("pynvml not installed. Run: pip install nvidia-ml-py")

    _pynvml.nvmlInit()
    try:
        handle = _pynvml.nvmlDeviceGetHandleByIndex(0)
        util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = _pynvml.nvmlDeviceGetMemoryInfo(handle)
        temp = _pynvml.nvmlDeviceGetTemperature(handle, _pynvml.NVML_TEMPERATURE_GPU)
        return {
            "utilization": util.gpu,
            "vram_used_mb": mem.used // (1024 * 1024),
            "vram_total_mb": mem.total // (1024 * 1024),
            "temperature": temp,
        }
    finally:
        _pynvml.nvmlShutdown()


# ---------------------------------------------------------------------------
# NVIDIA API helpers
# ---------------------------------------------------------------------------

def fetch_gpu_pfid(gpu_name: str) -> Optional[str]:
    """Search the ZenitH-AT gpu-data.json (notebook then desktop) for the pfid.

    Lets requests.Timeout and requests.ConnectionError propagate so the caller
    can show a meaningful error.  Returns None only when the GPU is genuinely
    absent from the database.
    """
    search_name = gpu_name
    if search_name.upper().startswith("NVIDIA "):
        search_name = search_name[7:]

    r = requests.get(GPU_DATA_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data: dict = r.json()

    # Try notebook first (laptop GPUs), then desktop
    for section in ("notebook", "desktop"):
        pfid = _match_pfid(search_name, data.get(section, {}))
        if pfid:
            return pfid
    return None


def _match_pfid(search_name: str, gpu_data: dict) -> Optional[str]:
    if not gpu_data:
        return None
    if _FUZZ_AVAILABLE:
        result = _fuzz_process.extractOne(search_name, gpu_data.keys())
        if result and result[1] >= 60:
            return gpu_data[result[0]]
    else:
        name_lower = search_name.lower()
        for key, pfid in gpu_data.items():
            if key.lower() in name_lower or name_lower in key.lower():
                return pfid
    return None


def check_latest_driver(pfid: str, installed_version: str) -> dict:
    """Query processFind.aspx for the latest driver version for this GPU."""
    r = requests.get(
        PROCESS_FIND_URL,
        params={"pfid": pfid, "osid": OS_ID, "dtcid": DCH_ID},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    html = r.text

    version_match = re.search(r'<td class="gridItem">(\d{3}\.\d{2})</td>', html)
    if not version_match:
        raise RuntimeError("No driver found in NVIDIA download database")
    latest_ver = version_match.group(1)

    id_match = re.search(r'driverResults\.aspx/(\d+)/', html)
    download_url = (
        NVIDIA_RESULTS_URL.format(id=id_match.group(1)) if id_match else NVIDIA_DRIVERS_URL
    )

    return {
        "latest_version": latest_ver,
        "installed_version": installed_version,
        "is_up_to_date": latest_ver == installed_version,
        "download_url": download_url,
        "release_notes_url": download_url,
    }


def _get_cached_driver_check(gpu_name: str, installed_version: str) -> dict:
    """Return driver check result, re-fetching only when the 5-minute cache expires."""
    now = time.time()
    if _driver_cache["data"] and (now - _driver_cache["timestamp"]) < CACHE_TTL:
        return _driver_cache["data"]

    pfid = fetch_gpu_pfid(gpu_name)
    if not pfid:
        raise RuntimeError("GPU not found in NVIDIA database")

    result = check_latest_driver(pfid, installed_version)
    _driver_cache["data"] = result
    _driver_cache["timestamp"] = now
    return result


# ---------------------------------------------------------------------------
# Media file helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _clips_dir() -> Path:
    raw = _load_config().get("clips_dir", _DEFAULT_CLIPS).strip() or _DEFAULT_CLIPS
    return Path(os.path.expandvars(raw))


def _shots_dir() -> Path:
    raw = _load_config().get("shots_dir", _DEFAULT_SHOTS).strip() or _DEFAULT_SHOTS
    return Path(os.path.expandvars(raw))


def list_media_files(
    base_dir: Path,
    extensions: list[str],
    game_filter: Optional[str],
    limit: int = 10,
) -> list[Path]:
    """Scan base_dir recursively, sort by modification date, optionally filter by game name."""
    if not base_dir.exists():
        return []

    seen: set[Path] = set()
    files: list[Path] = []
    for ext in extensions:
        for f in base_dir.rglob(f"*.{ext}"):
            if f not in seen:
                seen.add(f)
                files.append(f)

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if game_filter:
        if _FUZZ_AVAILABLE:
            files = [
                f for f in files
                if _fuzz.partial_ratio(game_filter.lower(), f.parent.name.lower()) >= 60
            ]
        else:
            gl = game_filter.lower()
            files = [f for f in files if gl in f.parent.name.lower()]

    return files[:limit]


def _make_media_result(f: Path) -> Result:
    mtime = time.strftime("%d/%m/%Y %H:%M", time.localtime(f.stat().st_mtime))
    return Result(
        title=f.name,
        subtitle=f"{f.parent.name}  —  {mtime}",
        icon=str(f),
        json_rpc_action=open_uri(f.as_uri()),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_info() -> list[Result]:
    try:
        gpu = get_gpu_info_wmi()
    except RuntimeError as e:
        return [Result(title="NVIDIA GPU not detected", subtitle=str(e), icon=ICON)]
    except Exception as e:
        return [Result(title="WMI error", subtitle=str(e), icon=ICON)]

    # VRAM: WMI AdapterRAM is 32-bit and caps at 4 GB; fall back to pynvml
    vram_mb = gpu["vram_mb"]
    vram_error: Optional[str] = None
    if vram_mb <= 0 and _NVML_AVAILABLE:
        try:
            vram_mb = get_gpu_stats_nvml()["vram_total_mb"]
        except Exception as e:
            vram_error = f"{type(e).__name__}: {e}"
    vram_str = f"{vram_mb} MB" if vram_mb > 0 else "N/A"

    results = [
        Result(
            title=gpu["name"],
            subtitle="GPU",
            icon=ICON,
            score=300_000,
            copy_text=gpu["name"],
        ),
    ]

    # Driver version + update check — errors are shown in the subtitle
    driver_subtitle = "Installed driver"
    driver_action = None
    try:
        info = _get_cached_driver_check(gpu["name"], gpu["driver_version"])
        if info["is_up_to_date"]:
            driver_subtitle = "Up to date"
        else:
            dl_url = info["download_url"] or NVIDIA_DRIVERS_URL
            driver_subtitle = f"Update available: {info['latest_version']} — click to download"
            driver_action = open_url(dl_url)
    except requests.Timeout:
        driver_subtitle = "Driver check timed out — click to open NVIDIA website"
        driver_action = open_url(NVIDIA_DRIVERS_URL)
    except requests.ConnectionError:
        driver_subtitle = "No internet — driver check skipped"
    except RuntimeError as e:
        driver_subtitle = f"Driver DB: {e}"
        driver_action = open_url(NVIDIA_DRIVERS_URL)
    except Exception as e:
        driver_subtitle = f"Driver check error: {type(e).__name__}: {e}"
        driver_action = open_url(NVIDIA_DRIVERS_URL)

    results.append(Result(
        title=f"Driver {gpu['driver_version']}",
        subtitle=driver_subtitle,
        icon=ICON,
        score=200_000,
        copy_text=gpu["driver_version"],
        json_rpc_action=driver_action,
    ))

    results.append(Result(
        title=f"VRAM: {vram_str}",
        subtitle=vram_error if vram_error else "Total video memory",
        icon=ICON,
        score=100_000,
    ))

    return results


def handle_changelog() -> list[Result]:
    url = NVIDIA_DRIVERS_URL
    cached = _driver_cache.get("data")
    if cached and cached.get("release_notes_url"):
        url = cached["release_notes_url"]

    return [Result(
        title="Open NVIDIA Release Notes",
        subtitle=url,
        icon=ICON,
        json_rpc_action=open_url(url),
    )]


def handle_stats() -> list[Result]:
    if not _NVML_AVAILABLE:
        return [Result(
            title="pynvml not installed",
            subtitle="In the plugin folder run: pip install nvidia-ml-py",
            icon=ICON,
        )]

    try:
        s = get_gpu_stats_nvml()
    except _pynvml.NVMLError as e:
        return [Result(title="pynvml error", subtitle=str(e), icon=ICON)]
    except Exception as e:
        return [Result(title="GPU stats error", subtitle=str(e), icon=ICON)]

    vram_pct = (s["vram_used_mb"] * 100 // s["vram_total_mb"]) if s["vram_total_mb"] else 0
    return [
        Result(
            title=f"GPU: {s['utilization']}%",
            subtitle="Graphics processor utilization",
            icon=ICON,
        ),
        Result(
            title=f"VRAM: {s['vram_used_mb']} / {s['vram_total_mb']} MB  ({vram_pct}%)",
            subtitle="Video memory in use",
            icon=ICON,
        ),
        Result(
            title=f"Temperature: {s['temperature']}°C",
            subtitle="GPU core temperature",
            icon=ICON,
        ),
    ]


def _get_game_dirs(base_dir: Path) -> list[Path]:
    """Return immediate subdirectories sorted by most recently modified."""
    dirs = [p for p in base_dir.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs


def _match_game_dir(base_dir: Path, game_filter: str) -> Optional[Path]:
    """Return the best-matching game subdirectory for game_filter, or None."""
    dirs = _get_game_dirs(base_dir)
    if not dirs:
        return None
    if _FUZZ_AVAILABLE:
        names = [d.name for d in dirs]
        result = _fuzz_process.extractOne(game_filter, names)
        if result and result[1] >= 60:
            return base_dir / result[0]
    else:
        fl = game_filter.lower()
        for d in dirs:
            if fl in d.name.lower():
                return d
    return None


def _folder_result(label: str, path: Path, query_cmd: str) -> Result:
    return Result(
        title=label,
        subtitle=str(path),
        icon=ICON,
        score=100_000,
        json_rpc_action=shell_run(str(path), "explorer.exe"),
    )


def handle_clips(game_filter: Optional[str]) -> list[Result]:
    clips_dir = _clips_dir()
    results: list[Result] = []

    if not clips_dir.exists():
        return [Result(
            title="Clips folder not found",
            subtitle=str(clips_dir),
            icon=ICON,
        )]

    if game_filter:
        # Level 2: show contents of the matched game folder
        game_dir = _match_game_dir(clips_dir, game_filter)
        target = game_dir or clips_dir
        results.append(_folder_result(f"Open {target.name} folder", target, "clips"))
        files = list_media_files(target, ["mp4"], None)
        if files:
            results.extend(_make_media_result(f) for f in files)
        else:
            results.append(Result(
                title=f"No clips found in '{target.name}'",
                subtitle=str(target),
                icon=ICON,
            ))
    else:
        # Level 1: show game folders
        results.append(_folder_result("Open clips folder", clips_dir, "clips"))
        game_dirs = _get_game_dirs(clips_dir)
        if game_dirs:
            for d in game_dirs:
                results.append(Result(
                    title=d.name,
                    subtitle=str(d),
                    icon=ICON,
                    json_rpc_action=_change_query(f"nv clips {d.name} "),
                ))
        else:
            # No subfolders — fall back to listing files directly
            files = list_media_files(clips_dir, ["mp4"], None)
            if files:
                results.extend(_make_media_result(f) for f in files)
            else:
                results.append(Result(title="No clips found", subtitle=str(clips_dir), icon=ICON))

    return results


def handle_shots(game_filter: Optional[str]) -> list[Result]:
    shots_dir = _shots_dir()
    results: list[Result] = []

    if not shots_dir.exists():
        return [Result(
            title="Screenshots folder not found",
            subtitle=str(shots_dir),
            icon=ICON,
        )]

    if game_filter:
        # Level 2: show contents of the matched game folder
        game_dir = _match_game_dir(shots_dir, game_filter)
        target = game_dir or shots_dir
        results.append(_folder_result(f"Open {target.name} folder", target, "shots"))
        files = list_media_files(target, ["jpg", "jpeg", "png"], None)
        if files:
            results.extend(_make_media_result(f) for f in files)
        else:
            results.append(Result(
                title=f"No screenshots found in '{target.name}'",
                subtitle=str(target),
                icon=ICON,
            ))
    else:
        # Level 1: show game folders
        results.append(_folder_result("Open screenshots folder", shots_dir, "shots"))
        game_dirs = _get_game_dirs(shots_dir)
        if game_dirs:
            for d in game_dirs:
                results.append(Result(
                    title=d.name,
                    subtitle=str(d),
                    icon=ICON,
                    json_rpc_action=_change_query(f"nv shots {d.name} "),
                ))
        else:
            # No subfolders — fall back to listing files directly
            files = list_media_files(shots_dir, ["jpg", "jpeg", "png"], None)
            if files:
                results.extend(_make_media_result(f) for f in files)
            else:
                results.append(Result(title="No screenshots found", subtitle=str(shots_dir), icon=ICON))

    return results


def handle_settings() -> list[Result]:
    config = _load_config()
    clips_raw = config.get("clips_dir", _DEFAULT_CLIPS) or _DEFAULT_CLIPS
    shots_raw = config.get("shots_dir", _DEFAULT_SHOTS) or _DEFAULT_SHOTS

    # Auto-create config.json with defaults if it doesn't exist yet
    if not _CONFIG_PATH.exists():
        _CONFIG_PATH.write_text(
            json.dumps({"clips_dir": _DEFAULT_CLIPS, "shots_dir": _DEFAULT_SHOTS}, indent=2),
            encoding="utf-8",
        )

    return [
        Result(
            title="Open config.json",
            subtitle=str(_CONFIG_PATH),
            icon=ICON,
            score=300_000,
            json_rpc_action=open_uri(_CONFIG_PATH.as_uri()),
        ),
        Result(
            title=f"Clips: {clips_raw}",
            subtitle=os.path.expandvars(clips_raw),
            icon=ICON,
            score=200_000,
        ),
        Result(
            title=f"Shots: {shots_raw}",
            subtitle=os.path.expandvars(shots_raw),
            icon=ICON,
            score=100_000,
        ),
    ]


def _help_results(partial: str) -> list[Result]:
    commands = [
        ("info",      "GPU name, driver status, total VRAM"),
        ("changelog", "Open latest NVIDIA driver release notes in browser"),
        ("stats",     "Live GPU utilization %, VRAM usage, temperature"),
        ("clips",     "List recent video clips  (e.g. nv clips fortnite)"),
        ("shots",     "List recent screenshots  (e.g. nv shots cyberpunk)"),
        ("settings",  "Configure clips and screenshots directories"),
    ]
    return [
        Result(
            title=cmd,
            subtitle=desc,
            icon=ICON,
            json_rpc_action=_change_query(f"nv {cmd} "),
        )
        for cmd, desc in commands
        if not partial or cmd.startswith(partial)
    ]


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------

@plugin.on_method
def query(query_text: str) -> ResultResponse:
    parts = query_text.strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else None

    dispatch = {
        "info":      lambda: handle_info(),
        "changelog": lambda: handle_changelog(),
        "stats":     lambda: handle_stats(),
        "clips":     lambda: handle_clips(arg),
        "shots":     lambda: handle_shots(arg),
        "settings":  lambda: handle_settings(),
    }

    if cmd in dispatch:
        results = dispatch[cmd]()
    else:
        results = _help_results(cmd)

    return send_results(results)


if __name__ == "__main__":
    plugin.run()

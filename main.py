"""
flow-nvidia-control
Keyword: nv
Subcommands: info | driver | changelog | stats | clips [game] | shots [game] | open
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

# Add lib/ to sys.path so Flow Launcher can find bundled dependencies
_lib = _Path(__file__).parent / "lib"
if _lib.exists() and str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

import time
from pathlib import Path
from typing import Optional

import requests
from pyflowlauncher import Plugin, Result, send_results
from pyflowlauncher.result import JsonRPCResponse as ResultResponse
from pyflowlauncher.api import open_url, open_uri, shell_run

plugin = Plugin()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ICON = "Images/icon.png"
NVIDIA_APP_PATHS = [
    Path(r"C:\Program Files\NVIDIA Corporation\NVIDIA app\CEF\NVIDIA app.exe"),
    Path(r"C:\Program Files\NVIDIA Corporation\NVIDIA Control Panel\nvcplui.exe"),
    Path(r"C:\Windows\System32\nvcplui.exe"),
]
CLIPS_DIR = Path.home() / "Videos" / "NVIDIA App"
SHOTS_DIR = Path.home() / "Pictures" / "NVIDIA App"
GPU_DATA_URL = "https://raw.githubusercontent.com/ZenitH-AT/nvidia-data/main/desktop-gpu.json"
AJAX_DRIVER_URL = (
    "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
)
NVIDIA_DRIVERS_URL = "https://www.nvidia.com/en-us/drivers/results/"
OS_ID = 57  # Windows 10/11 64-bit
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
    """Download ZenitH-AT gpu-data.json and return the pfid for this GPU."""
    try:
        r = requests.get(GPU_DATA_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        gpu_data: dict = r.json()
    except Exception:
        return None

    if _FUZZ_AVAILABLE:
        result = _fuzz_process.extractOne(gpu_name, gpu_data.keys())
        if result and result[1] >= 60:
            return gpu_data[result[0]]
    else:
        gpu_lower = gpu_name.lower()
        for key, pfid in gpu_data.items():
            if key.lower() in gpu_lower or gpu_lower in key.lower():
                return pfid

    return None


def check_latest_driver(pfid: str, installed_version: str) -> dict:
    """Call AjaxDriverService and compare with installed driver version."""
    params = {
        "func": "DriverManualLookup",
        "pfid": pfid,
        "osID": OS_ID,
        "dch": 1,
        "languageCode": 1,
        "numberOfResults": 1,
    }
    r = requests.get(AJAX_DRIVER_URL, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    ids = data.get("IDS", [])
    if not ids:
        raise RuntimeError("No driver found for this GPU in the NVIDIA API")

    dl_info = ids[0].get("downloadInfo", {})
    latest_ver = dl_info.get("Version", "").strip()
    download_url = dl_info.get("DownloadURL", "").strip()
    release_notes_url = dl_info.get("releaseNotes", "").strip()

    if not latest_ver:
        raise RuntimeError(f"Unexpected NVIDIA API response structure: {data}")

    return {
        "latest_version": latest_ver,
        "installed_version": installed_version,
        "is_up_to_date": latest_ver == installed_version,
        "download_url": download_url,
        "release_notes_url": release_notes_url,
    }


def _get_cached_driver_check() -> dict:
    """Cache wrapper (TTL=5 min) around the driver check to avoid repeated HTTP calls."""
    now = time.time()
    if _driver_cache["data"] and (now - _driver_cache["timestamp"]) < CACHE_TTL:
        return _driver_cache["data"]

    gpu_info = get_gpu_info_wmi()
    pfid = fetch_gpu_pfid(gpu_info["name"])
    if not pfid:
        raise RuntimeError(
            f"GPU '{gpu_info['name']}' not found in NVIDIA database. "
            "Check manually at nvidia.com/drivers"
        )

    result = check_latest_driver(pfid, gpu_info["driver_version"])
    _driver_cache["data"] = result
    _driver_cache["timestamp"] = now
    return result


# ---------------------------------------------------------------------------
# Media file helpers
# ---------------------------------------------------------------------------

def list_media_files(
    base_dir: Path,
    extensions: list[str],
    game_filter: Optional[str],
    limit: int = 10,
) -> list[Path]:
    """Scan base_dir recursively, sort by modification date, optionally filter by game name."""
    if not base_dir.exists():
        return []

    files: list[Path] = []
    for ext in extensions:
        files.extend(base_dir.rglob(f"*.{ext}"))
        files.extend(base_dir.rglob(f"*.{ext.upper()}"))

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
        icon=ICON,
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

    vram_str = f"{gpu['vram_mb']} MB" if gpu["vram_mb"] > 0 else "N/A (> 4 GB or unreadable)"
    return [
        Result(
            title=gpu["name"],
            subtitle="GPU name",
            icon=ICON,
            copy_text=gpu["name"],
        ),
        Result(
            title=f"Driver {gpu['driver_version']}",
            subtitle="Installed driver version  —  Click to copy",
            icon=ICON,
            copy_text=gpu["driver_version"],
        ),
        Result(
            title=f"VRAM: {vram_str}",
            subtitle="Total video memory",
            icon=ICON,
        ),
    ]


def handle_driver() -> list[Result]:
    try:
        info = _get_cached_driver_check()
    except requests.Timeout:
        return [Result(
            title="NVIDIA server timeout",
            subtitle=f"No response within {HTTP_TIMEOUT}s. Try again later.",
            icon=ICON,
        )]
    except requests.ConnectionError:
        return [Result(
            title="No Internet connection",
            subtitle="Check your network and try again",
            icon=ICON,
        )]
    except RuntimeError as e:
        return [Result(title="Driver check error", subtitle=str(e), icon=ICON)]
    except Exception as e:
        return [Result(title="Unexpected error", subtitle=str(e), icon=ICON)]

    if info["is_up_to_date"]:
        return [Result(
            title=f"Driver up to date ({info['installed_version']})",
            subtitle="You are running the latest available driver",
            icon=ICON,
        )]

    dl_url = info["download_url"] or NVIDIA_DRIVERS_URL
    return [
        Result(
            title=f"Update available: {info['latest_version']}",
            subtitle=f"Installed: {info['installed_version']}  —  Click to download",
            icon=ICON,
            json_rpc_action=open_url(dl_url),
        ),
        Result(
            title="Open NVIDIA driver download page",
            subtitle=dl_url,
            icon=ICON,
            json_rpc_action=open_url(dl_url),
        ),
    ]


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


def handle_clips(game_filter: Optional[str]) -> list[Result]:
    files = list_media_files(CLIPS_DIR, ["mp4"], game_filter)
    if not files:
        suffix = f" for '{game_filter}'" if game_filter else ""
        return [Result(
            title=f"No clips found{suffix}",
            subtitle=str(CLIPS_DIR),
            icon=ICON,
        )]
    return [_make_media_result(f) for f in files]


def handle_shots(game_filter: Optional[str]) -> list[Result]:
    shots_dir = SHOTS_DIR
    alt_dir = CLIPS_DIR / "Screenshots"
    if not shots_dir.exists() and alt_dir.exists():
        shots_dir = alt_dir

    files = list_media_files(shots_dir, ["jpg", "jpeg", "png"], game_filter)
    if not files:
        suffix = f" for '{game_filter}'" if game_filter else ""
        return [Result(
            title=f"No screenshots found{suffix}",
            subtitle=str(shots_dir),
            icon=ICON,
        )]
    return [_make_media_result(f) for f in files]


def handle_open() -> list[Result]:
    exe = next((p for p in NVIDIA_APP_PATHS if p.exists()), None)
    if exe:
        return [Result(
            title="Open NVIDIA App",
            subtitle=str(exe),
            icon=ICON,
            json_rpc_action=shell_run(str(exe)),
        )]
    return [Result(
        title="NVIDIA App not found",
        subtitle="Download NVIDIA App from nvidia.com",
        icon=ICON,
        json_rpc_action=open_url("https://www.nvidia.com/en-us/software/nvidia-app/"),
    )]


def _help_results(partial: str) -> list[Result]:
    commands = [
        ("info",      "GPU name, installed driver version, total VRAM"),
        ("driver",    "Compare installed driver with latest from NVIDIA"),
        ("changelog", "Open latest NVIDIA driver release notes in browser"),
        ("stats",     "Live GPU utilization %, VRAM usage, temperature"),
        ("clips",     "List recent video clips  (e.g. nv clips fortnite)"),
        ("shots",     "List recent screenshots  (e.g. nv shots cyberpunk)"),
        ("open",      "Launch NVIDIA App"),
    ]
    return [
        Result(title=f"nv {cmd}", subtitle=desc, icon=ICON)
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
        "driver":    lambda: handle_driver(),
        "changelog": lambda: handle_changelog(),
        "stats":     lambda: handle_stats(),
        "clips":     lambda: handle_clips(arg),
        "shots":     lambda: handle_shots(arg),
        "open":      lambda: handle_open(),
    }

    if cmd in dispatch:
        results = dispatch[cmd]()
    else:
        results = _help_results(cmd)

    return send_results(results)


if __name__ == "__main__":
    plugin.run()

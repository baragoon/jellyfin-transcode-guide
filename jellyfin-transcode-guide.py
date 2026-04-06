import platform
import psutil
import subprocess
import argparse
import requests
from typing import Dict, Any
import jellyfin_config as config

# === CONFIGURATION ===
JELLYFIN_URL: str = config.JELLYFIN_SERVER.rstrip("/")
API_KEY: str = config.API_KEY
USER_ID: str = config.USER_ID
HEADERS: Dict[str, str] = {"X-Emby-Token": API_KEY, "Accept": "application/json"}

# Multi-line ASCII art
header_ascii_art: str = r"""
___________                                            .___         ________      .__    .___      
\__    ___/___________    ____   ______ ____  ____   __| _/____    /  _____/ __ __|__| __| _/____  
  |    |  \_  __ \__  \  /    \ /  ___// ___\/  _ \ / __ |/ __ \  /   \  ___|  |  \  |/ __ |/ __ \ 
  |    |   |  | \// __ \|   |  \\___ \\  \__(  <_> ) /_/ \  ___/  \    \_\  \  |  /  / /_/ \  ___/ 
  |____|   |__|  (____  /___|  /____  >\___  >____/\____ |\___  >  \______  /____/|__\____ |\___  >
                      \/     \/     \/     \/           \/    \/          \/              \/    \/ 
"""

# -----------------------------
# Helper functions: type validation
# -----------------------------
def _require_type(name: str, value: Any, expected_type: type) -> None:
    """Ensure the value is of the expected type."""
    if not isinstance(value, expected_type):
        raise TypeError(f"{name} must be {expected_type.__name__}, got {type(value).__name__}")

def _require_keys(d: Dict[str, Any], required: Dict[str, type]) -> None:
    """Ensure dictionary contains required keys of expected types."""
    _require_type("dict input", d, dict)
    _require_type("required keys", required, dict)
    for key, typ in required.items():
        if key not in d:
            raise KeyError(f"Missing required key: {key}")
        if not isinstance(d[key], typ):
            raise TypeError(f"{key} must be {typ.__name__}, got {type(d[key]).__name__}")

# -----------------------------
# Hardware detection
# -----------------------------
def get_cpu() -> Dict[str, Any]:
    """Return CPU model and logical core count."""
    cores = psutil.cpu_count(logical=True)
    model = platform.processor().lower()
    _require_type("cores", cores, int)
    _require_type("model", model, str)
    return {"cores": cores, "model": model}

def get_ram_gb() -> float:
    """Return total system RAM in GB."""
    ram = psutil.virtual_memory().total / (1024 ** 3)
    _require_type("ram", ram, float)
    return round(ram, 2)

def has_nvidia() -> bool:
    """Detect if NVIDIA GPU is present."""
    try:
        return subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False

def has_intel() -> bool:
    """Detect if CPU is Intel."""
    cpu_str = platform.processor().lower()
    _require_type("cpu_str", cpu_str, str)
    return "intel" in cpu_str

def has_amd() -> bool:
    """Detect if CPU is AMD."""
    cpu_str = platform.processor().lower()
    _require_type("cpu_str", cpu_str, str)
    return "amd" in cpu_str

def detect_gpu_generation() -> str:
    """Return GPU generation estimate."""
    if has_nvidia():
        return "NVIDIA Turing or newer"
    elif has_intel():
        return "Intel Gen11 or newer"
    elif has_amd():
        return "AMD Vega or newer"
    return "Unknown"

def detect_gpu_model_vram() -> Dict[str, str]:
    """
    Detect GPU model and VRAM (approximate) for NVIDIA, Intel, AMD.
    Returns: {"model": str, "vram": str}, 'Unknown' if detection fails.
    """
    _require_type("no input", None, type(None))
    gpu_info = {"model": "Unknown", "vram": "Unknown"}
    os_name = platform.system().lower()

    try:
        if os_name == "windows":
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                    capture_output=True, text=True, check=True
                )
                line = result.stdout.strip().split(",")
                if len(line) >= 2:
                    gpu_info["model"] = line[0].strip()
                    gpu_info["vram"] = line[1].strip()
                    return gpu_info
            except Exception:
                pass

            try:
                result = subprocess.run(
                    ["wmic", "path", "win32_videocontroller", "get", "name,adapterram"],
                    capture_output=True, text=True, check=True
                )
                lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "Name" not in l]
                if lines:
                    parts = lines[0].split()
                    gpu_info["model"] = " ".join(parts[:-1])
                    vram_bytes = int(parts[-1])
                    gpu_info["vram"] = f"{round(vram_bytes / (1024**3), 1)} GB"
                    return gpu_info
            except Exception:
                pass

        elif os_name == "linux":
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                    capture_output=True, text=True, check=True
                )
                line = result.stdout.strip().split(",")
                if len(line) >= 2:
                    gpu_info["model"] = line[0].strip()
                    gpu_info["vram"] = line[1].strip()
                    return gpu_info
            except Exception:
                pass

        elif os_name == "darwin":
            try:
                result = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, check=True)
                for line in result.stdout.splitlines():
                    if "Chipset Model" in line:
                        gpu_info["model"] = line.split(":")[1].strip()
                    if "VRAM" in line or "VRAM (Dynamic, Max)" in line:
                        gpu_info["vram"] = line.split(":")[1].strip()
                return gpu_info
            except Exception:
                pass
    except Exception:
        pass

    return gpu_info

# -----------------------------
# Jellyfin API: fetch server transcoding settings
# -----------------------------
def fetch_server_transcoding_config() -> Dict[str, Any]:
    """Fetch system-wide transcoding configuration from Jellyfin server."""
    try:
        url = f"{JELLYFIN_URL}/System/Configuration/Transcoding"
        resp = requests.get(url, headers=HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        required_fields = {
            "HardwareAccelerationEnabled": bool,
            "EnableToneMapping": bool,
            "MaxBitrate": int,
            "TranscodingThreads": int
        }
        _require_keys(data, required_fields)
        return data
    except Exception:
        return {}

# -----------------------------
# Recommendation engine
# -----------------------------
def build_recommendations(server_config: Dict[str, Any] = {}) -> Dict[str, Any]:
    """Build transcoding recommendations based on hardware and server info."""
    _require_type("server_config", server_config, dict)

    cpu = get_cpu()
    ram = get_ram_gb()
    nvidia = has_nvidia()
    intel = has_intel()
    amd = has_amd()
    gpu_gen = detect_gpu_generation()
    gpu_info = detect_gpu_model_vram()

    rec: Dict[str, Any] = {
        "hardware_acceleration": "none",
        "codec": "H264",
        "threads": max(2, cpu["cores"] // 2),
        "tone_mapping": False,
        "max_bitrate": "8M",
        "gpu_model": gpu_info["model"],
        "gpu_vram": gpu_info["vram"],
        "reason_log": []
    }

    if nvidia:
        rec.update({"hardware_acceleration": "NVENC", "codec": "H264", "threads": 0, "tone_mapping": True})
        rec["reason_log"].append(f"NVIDIA GPU detected ({gpu_info['model']}, {gpu_info['vram']}) → NVENC selected")
    elif intel:
        rec.update({"hardware_acceleration": "QSV", "codec": "H264", "threads": 0, "tone_mapping": True})
        rec["reason_log"].append(f"Intel CPU detected → QSV selected")
    elif amd:
        rec.update({"hardware_acceleration": "AMF/VAAPI", "codec": "H264", "threads": 0, "tone_mapping": True})
        rec["reason_log"].append(f"AMD hardware detected → AMF/VAAPI selected")
    else:
        rec["reason_log"].append("No GPU detected → software encoding")

    if ram < 8.0:
        rec["max_bitrate"] = "6M"
        rec["tone_mapping"] = False
        rec["reason_log"].append("Low RAM → reducing max bitrate and disabling tone mapping")
    else:
        rec["max_bitrate"] = "20M"

    if server_config:
        hw_accel = server_config.get("HardwareAccelerationEnabled", None)
        tone_map = server_config.get("EnableToneMapping", None)
        max_br = server_config.get("MaxBitrate", None)
        threads = server_config.get("TranscodingThreads", None)

        if hw_accel is False and rec["hardware_acceleration"] != "none":
            rec["reason_log"].append("Server has hardware acceleration disabled → enabling would improve performance")
        if tone_map is False and rec["tone_mapping"]:
            rec["reason_log"].append("Server tone mapping disabled → enabling improves HDR → SDR playback")
        if max_br is not None and int(max_br) < int(rec["max_bitrate"].replace("M","")):
            rec["reason_log"].append(f"Server max bitrate {max_br}M is lower than hardware potential")
        if threads is not None and rec["threads"] != 0 and int(threads) < rec["threads"]:
            rec["reason_log"].append(f"Server thread count {threads} is lower than recommended")

    _require_keys(rec, {
        "hardware_acceleration": str,
        "codec": str,
        "threads": int,
        "tone_mapping": bool,
        "max_bitrate": str,
        "gpu_model": str,
        "gpu_vram": str,
        "reason_log": list
    })
    return rec

# -----------------------------
# Output renderers
# -----------------------------
def print_standard(rec: Dict[str, Any]) -> None:
    """Display simple, clean recommendations for standard users."""
    _require_keys(rec, {
        "hardware_acceleration": str,
        "codec": str,
        "threads": int,
        "tone_mapping": bool,
        "max_bitrate": str,
        "gpu_model": str,
        "gpu_vram": str,
        "reason_log": list
    })

    # print ascii header
    print(header_ascii_art)

    print("\n=== JELLYFIN TRANSCODING RECOMMENDATIONS ===")
    print(f"Hardware Acceleration: {rec['hardware_acceleration']}")
    print(f"Video Codec: {rec['codec']}")
    print(f"Tone Mapping: {'On (for HDR → SDR)' if rec['tone_mapping'] else 'Off'}")
    print(f"Maximum Bitrate: {rec['max_bitrate']}")
    threads_info = "handled by GPU" if rec['threads'] == 0 else f"{rec['threads']} threads"
    print(f"Transcoding Threads: {threads_info}")
    if rec["gpu_model"] != "Unknown":
        print(f"GPU Model: {rec['gpu_model']}")
    if rec["gpu_vram"] != "Unknown":
        print(f"GPU VRAM: {rec['gpu_vram']}")
    print("\nUse these settings as your starting point for optimal playback based on your computer's capabilities.")

def print_advanced(rec: Dict[str, Any]) -> None:
    """Display detailed recommendations, reasoning, and practical guidance."""
    _require_keys(rec, {
        "hardware_acceleration": str,
        "codec": str,
        "threads": int,
        "tone_mapping": bool,
        "max_bitrate": str,
        "gpu_model": str,
        "gpu_vram": str,
        "reason_log": list
    })

    # print ascii header
    print(header_ascii_art)

    print("\n=== JELLYFIN RECOMMENDATION SUMMARY ===")
    print(f"- Hardware Acceleration: {rec['hardware_acceleration']} ({', '.join(rec['reason_log'])})")
    print(f"- Codec: {rec['codec']}")
    print(f"- Tone Mapping: {'Enabled' if rec['tone_mapping'] else 'Disabled'} (for HDR → SDR)")
    print(f"- Max Bitrate: {rec['max_bitrate']}")
    threads_info = "handled by GPU" if rec['threads'] == 0 else f"{rec['threads']}"
    print(f"- Transcoding Threads: {threads_info}")
    if rec["gpu_model"] != "Unknown":
        print(f"- GPU Model: {rec['gpu_model']}")
    if rec["gpu_vram"] != "Unknown":
        print(f"- GPU VRAM: {rec['gpu_vram']}")

    print("\n=== WHY THIS MAKES SENSE ===")
    if rec["reason_log"]:
        first_reason = rec["reason_log"][0]
        if "NVIDIA GPU detected" in first_reason:
            print(f"- GPU encoding (NVENC) uses {rec['gpu_model']} with {rec['gpu_vram']} → keeps CPU free.")
        elif "Intel CPU detected" in first_reason:
            print("- GPU encoding (QSV) is efficient on Intel hardware, preserving CPU for other tasks.")
        elif "AMD hardware detected" in first_reason:
            print("- GPU encoding (AMF/VAAPI) uses AMD GPU to speed up transcoding and reduce CPU load.")
        else:
            print("- Software CPU encoding chosen; more control over quality but uses more CPU resources.")
    print(f"- {rec['codec']} is widely compatible with most clients; HEVC or AV1 could reduce compatibility.")
    if rec['tone_mapping']:
        print("- Tone mapping ensures HDR content displays correctly on SDR devices.")
    print(f"- Bitrate of {rec['max_bitrate']} balances visual quality and performance for your hardware.")

    print("\n=== PRACTICAL GUIDANCE ===")
    print("- Prefer Direct Play whenever possible to avoid unnecessary transcoding.")
    print("- Avoid 4K → 1080p conversions unless required.")
    print("- For streams to weaker devices or limited networks, consider lowering bitrate slightly.")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Jellyfin Transcoding Recommendation Tool")
        parser.add_argument("--advanced", action="store_true", help="Enable advanced output mode")
        args = parser.parse_args()

        server_config = fetch_server_transcoding_config()
        recs = build_recommendations(server_config)

        if args.advanced:
            print_advanced(recs)
        else:
            print_standard(recs)

    except Exception as e:
        print(f"Oops! Something went wrong: {e}. The script could not complete, but nothing was harmed.")
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import logging
import time 
import os 
import signal
import json 
from typing import Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re

from config import COOKIES_FILE, MAX_RETRIES, RETRY_BACKOFF

def clip_number(p: Path):
    m = re.search(r"clip_(\d+)", p.name)
    return int(m.group(1)) if m else -1

def save_json(data: Dict[str, Any], filename: str = "followed_rooms.json"):
    """Save JSON to file."""

    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        print(f"💾 Saved JSON → {filename}")

    except Exception as e:
        print("❌ Save error:", e)

def load_json(filename: str = "followed_rooms.json") -> Dict[str, Any]:
    """Load JSON from file."""

    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        print(f"📂 Loaded JSON → {filename}")
        return data

    except Exception as e:
        print("❌ Load error:", e)
        return {}

def check_disk_space():
    total, used, free = shutil.disk_usage("/")
    return (free / (1024 ** 3)) >= MIN_FREE_GB

def get_today_folder(model_name: str):
    folder = BASE_RECORDINGS_DIR / model_name / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def merge_segments(folder: Path, model_name: str):
    """
    Merge all segments in folder into a single FULL_<model>.mp4 file.
    If FULL_<model>.mp4 already exists, append timestamp to avoid overwrite.
    After merging, delete all segment files.
    """
    time.sleep(2)
    
    clips = sorted(
        folder.glob("clip_*.mp4"),
        key=clip_number
    )
    # print(clips)
    if not clips:
        logging.info(f"{model_name}: No segments to merge.")
        return None
    
    # printing all clips files
    # for c in clips:
    #     print(c.name)

    # Generate merged file name
    merged_file = folder / f"FULL_{model_name}.mp4"
    # if merged_file.exists():
    #     timestamp = time.strftime("%H%M%S")
    #     merged_file = folder / f"FULL_{model_name}_{timestamp}.mp4"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    merged_file = folder / f"FULL_{model_name}_{timestamp}.mp4"

    list_file = folder / "files.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{clip.name}'\n")

    logging.info(f"{model_name}: Merging {len(clips)} segments into {merged_file.name}")
    subprocess.run([
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(merged_file)
    ])

    # Delete the segment files after merging
    for clip in clips:
        try:
            clip.unlink()
        except Exception as e:
            logging.warning(f"{model_name}: Failed to delete segment {clip.name}: {e}")

    # list_file.unlink(missing_ok=True)
    logging.info(f"{model_name}: Merge complete and segments deleted.")
    return merged_file

def kill_process(process):
    try:
        os.killpg(
            os.getpgid(process.pid),
            signal.SIGKILL,
        )
    except Exception:
        process.terminate()
        
        
def ensure_disk_space(model_name: str) -> bool:

    if not check_disk_space():
        logging.warning(
            f"{model_name}: Low disk space. Sleeping 60s."
        )

        time.sleep(10)
        return False

    return True


def load_cookies(path: Path = COOKIES_FILE) -> Dict[str, str]:
    """Load cookies from a browser-export JSON (list) or a plain name->value dict."""

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}

    cookies: Dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict) and "name" in item and "value" in item:
            cookies[item["name"]] = item["value"]

    return cookies


def create_session(cookies: Dict[str, str]) -> requests.Session:
    """Create a session with cookies, retries and default headers."""

    session = requests.Session()
    session.cookies.update(cookies)

    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def sanitize_filename(name: str, fallback: str = "video") -> str:
    """Turn an arbitrary string into a safe file name."""

    name = re.sub(r"[\x00-\x1f]", "", str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name[:150] or fallback


def duration_to_seconds(value) -> float:
    """Convert 'HH:MM:SS', 'MM:SS', or a plain number of seconds to float."""

    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"




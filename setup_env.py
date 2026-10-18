#!/usr/bin/env python3
"""One-shot environment bootstrap for rare-study-downloader.

Creates a local ``.venv``, installs every Python dependency (engine + GUI), and
makes ffmpeg/ffprobe available. Runs on macOS and Windows with any stock
CPython 3.12+ - no uv, no pipx, no admin rights:

    python setup_env.py                    # engine + GUI + ffmpeg automation
    python setup_env.py --cli-only         # engine only, skips PyQt6
    python setup_env.py --recreate         # delete .venv and start clean
    python setup_env.py --download-ffmpeg  # keep a private copy in .tools/ffmpeg
    python setup_env.py --skip-ffmpeg      # do not touch ffmpeg at all

ffmpeg setup picks the best option automatically: an existing install, a package
manager (Homebrew on macOS, winget/choco/scoop on Windows), or a downloaded
standalone build placed in ``.tools/ffmpeg`` (the app finds that automatically).
Dependencies come from ``pyproject.toml``, so this stays in sync. Safe to re-run.
"""

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
TOOLS_DIR = PROJECT_DIR / ".tools" / "ffmpeg"
MIN_PYTHON = (3, 12)
USER_AGENT = "rare-study-downloader-setup"

FFMPEG_PAGE = "https://ffmpeg.martin-riedl.de"
WINDOWS_FFMPEG_SOURCES = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip",
)


def die(message: str) -> None:
    print(f"\n[error] {message}", file=sys.stderr)
    raise SystemExit(1)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        want = ".".join(map(str, MIN_PYTHON))
        got = ".".join(map(str, sys.version_info[:3]))
        die(
            f"Python {want}+ is required, but this is {got}.\n"
            "  macOS:   brew install python@3.12   (or https://www.python.org/downloads/)\n"
            "  Windows: winget install Python.Python.3.12\n"
            "           or https://www.python.org/downloads/ and tick 'Add python.exe to PATH'"
        )


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def read_dependencies(cli_only: bool) -> list:
    import tomllib

    with open(PROJECT_DIR / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]

    deps = list(project.get("dependencies", []))
    if not cli_only:
        deps += project.get("optional-dependencies", {}).get("gui", [])
    if not deps:
        die("No dependencies found in pyproject.toml.")
    return deps


def run(command: list) -> None:
    print(f"\n$ {' '.join(str(part) for part in command)}")
    subprocess.check_call([str(part) for part in command])


def create_venv(recreate: bool) -> Path:
    if recreate and VENV_DIR.exists():
        print(f"Removing existing environment: {VENV_DIR}")
        shutil.rmtree(VENV_DIR)
    if not VENV_DIR.exists():
        print(f"Creating virtual environment: {VENV_DIR}")
        run([sys.executable, "-m", "venv", VENV_DIR])
    else:
        print(f"Reusing virtual environment: {VENV_DIR}")
    return venv_python()


def install(python: Path, cli_only: bool) -> None:
    deps = read_dependencies(cli_only)
    run([python, "-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check"])
    print(f"\nInstalling {len(deps)} dependenc{'y' if len(deps) == 1 else 'ies'}:")
    for dep in deps:
        print(f"  - {dep}")
    run([python, "-m", "pip", "install", "--disable-pip-version-check", *deps])


# --------------------------------------------------------------------- ffmpeg


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _bundled_paths() -> dict:
    return {name: TOOLS_DIR / _exe(name) for name in ("ffmpeg", "ffprobe")}


def _available_paths() -> dict:
    """Resolve each tool from env override, the .tools copy, then PATH."""

    found = {}
    for name, bundled in _bundled_paths().items():
        override = os.environ.get(name.upper())
        found[name] = override or (str(bundled) if bundled.exists() else shutil.which(name))
    return found


def _report(paths: dict) -> None:
    for name, path in paths.items():
        print(f"  [ok] {name}: {path}")


def _ffmpeg_hint() -> None:
    print("  [warning] could not set up ffmpeg automatically. Install it manually:")
    print("    macOS:   brew install ffmpeg")
    print("    Windows: winget install Gyan.FFmpeg")
    print("    or set the FFMPEG and FFPROBE environment variables to the binary paths.")


def _fetch(url: str, destination: Path, attempts: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        print(f"  downloading {url} (attempt {attempt}/{attempts})")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=180) as response, open(destination, "wb") as out:
                shutil.copyfileobj(response, out)
        except (OSError, urllib.error.URLError) as exc:
            print(f"  [warn] download failed: {exc}")
            continue
        print(f"  got {destination.stat().st_size / 1_048_576:.1f} MB")
        return True
    return False


def _get_text(url: str, timeout: int = 30, attempts: int = 3) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (OSError, urllib.error.URLError) as exc:
            print(f"  [warn] {url} (attempt {attempt}/{attempts}): {exc}")
    return None


def _clear_quarantine(path: Path) -> None:
    if sys.platform == "darwin" and shutil.which("xattr"):
        subprocess.run(
            ["xattr", "-d", "com.apple.quarantine", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _extract_tools(archive_path: Path, wanted: set, target_dir: Path) -> set:
    try:
        zipfile.ZipFile(archive_path).testzip()
    except zipfile.BadZipFile:
        print("  [warn] downloaded file is not a valid zip archive.")
        return set()

    installed: set = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            base = os.path.basename(member)
            if not base or member.endswith("/"):
                continue
            stem = base[:-4] if base.lower().endswith(".exe") else base
            if stem not in ("ffmpeg", "ffprobe") or stem not in wanted:
                continue
            dest = target_dir / _exe(stem)
            try:
                with archive.open(member) as source, open(dest, "wb") as target:
                    shutil.copyfileobj(source, target)
            except OSError as exc:
                print(f"  [warn] could not write {dest}: {exc}")
                continue
            if os.name != "nt":
                dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                _clear_quarantine(dest)
            print(f"  [ok] extracted {dest.name}")
            installed.add(stem)
    return installed


def _macos_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


def _macos_sources() -> list:
    """Discover the newest standalone ffmpeg/ffprobe zips for this Mac."""

    arch = _macos_arch()
    html = _get_text(FFMPEG_PAGE + "/", timeout=60)
    if html is None:
        print(f"  [warn] could not reach {FFMPEG_PAGE}.")
        return []

    pattern = re.compile(rf'href="(/download/macos/{arch}/(\d+)_[^"/]+/(ffmpeg|ffprobe)\.zip)"')
    newest: dict = {}
    for href, stamp, tool in pattern.findall(html):
        if tool not in newest or int(stamp) > newest[tool][0]:
            newest[tool] = (int(stamp), f"{FFMPEG_PAGE}{href}")
    return [{"url": url, "tools": [tool]} for tool, (_stamp, url) in newest.items()]


def _ffmpeg_sources() -> list:
    if sys.platform == "darwin":
        return _macos_sources()
    if sys.platform == "win32":
        return [{"url": url, "tools": ["ffmpeg", "ffprobe"]} for url in WINDOWS_FFMPEG_SOURCES]
    return []


def download_ffmpeg(target_dir: Path | None = None) -> bool:
    """Download standalone ffmpeg/ffprobe into target_dir. Returns True on success."""

    target_dir = Path(target_dir) if target_dir else TOOLS_DIR
    sources = _ffmpeg_sources()
    if not sources:
        print(f"  [warn] no automatic ffmpeg download for {sys.platform}/{platform.machine()}.")
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    needed = {"ffmpeg", "ffprobe"}
    for source in sources:
        if not needed:
            break
        archive = target_dir / "ffmpeg-download.zip"
        if not _fetch(source["url"], archive):
            continue
        installed = _extract_tools(archive, needed & set(source["tools"]), target_dir)
        archive.unlink(missing_ok=True)
        needed -= installed

    return not needed


def install_ffmpeg_with_package_manager() -> bool:
    candidates = []
    if sys.platform == "darwin":
        candidates.append(("brew", ["brew", "install", "ffmpeg"]))
    elif sys.platform == "win32":
        candidates.append((
            "winget",
            ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
             "--accept-source-agreements", "--accept-package-agreements"],
        ))
        candidates.append(("choco", ["choco", "install", "ffmpeg", "-y"]))
        candidates.append(("scoop", ["scoop", "install", "ffmpeg"]))

    for manager, command in candidates:
        if shutil.which(manager) is None:
            continue
        print(f"  Trying {manager}: {' '.join(command)}")
        try:
            subprocess.check_call(command)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"  [warn] {manager} failed: {exc}")
            continue
        return True
    return False


def ensure_ffmpeg(skip: bool = False, prefer_download: bool = False) -> bool:
    print("\nffmpeg / ffprobe:")

    if os.environ.get("FFMPEG") and os.environ.get("FFPROBE"):
        _report(_available_paths())
        print("  Using the FFMPEG / FFPROBE environment variables.")
        return True

    if not prefer_download:
        found = _available_paths()
        if found["ffmpeg"] and found["ffprobe"]:
            _report(found)
            return True

    if skip:
        _ffmpeg_hint()
        return False

    if not prefer_download and install_ffmpeg_with_package_manager():
        found = _available_paths()
        if found["ffmpeg"] and found["ffprobe"]:
            _report(found)
            return True
        print("  Installed. Reopen your terminal so PATH picks it up.")
        return True

    print("  Downloading standalone binaries into .tools/ffmpeg ...")
    download_ffmpeg()
    bundled = _bundled_paths()
    if all(path.exists() for path in bundled.values()):
        _report({name: str(path) for name, path in bundled.items()})
        print("  The app finds this copy automatically - no PATH changes needed.")
        return True

    _ffmpeg_hint()
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up a .venv with all dependencies.")
    parser.add_argument("--cli-only", action="store_true", help="skip the PyQt6 GUI dependency")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv from scratch")
    parser.add_argument(
        "--download-ffmpeg",
        action="store_true",
        help="download a private ffmpeg/ffprobe copy into .tools/ffmpeg (skips package managers)",
    )
    parser.add_argument(
        "--skip-ffmpeg", action="store_true", help="do not install or check ffmpeg/ffprobe"
    )
    args = parser.parse_args()

    print("rare-study-downloader setup")
    print(f"  python : {sys.executable}")
    print(f"  project: {PROJECT_DIR}")

    check_python()
    python = create_venv(args.recreate)
    install(python, args.cli_only)

    ensure_ffmpeg(skip=args.skip_ffmpeg, prefer_download=args.download_ffmpeg)

    print("\nNext steps:")
    print("  1. Export your cookies to cookies.json next to this script.")
    if args.cli_only:
        print(f'  2. Run the CLI:  "{python}" main.py --help')
    else:
        print(f'  2. Launch the GUI:  "{python}" gui.py')
        print(f'     or the CLI:      "{python}" main.py --help')
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

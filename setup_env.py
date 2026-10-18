#!/usr/bin/env python3
"""One-shot environment bootstrap for rare-study-downloader.

Creates a local ``.venv``, installs every Python dependency (engine + GUI),
and checks that ffmpeg/ffprobe are on PATH. Runs on macOS and Windows with any
stock CPython 3.12+ - no uv, no pipx, no admin rights:

    python setup_env.py              # engine + GUI (PyQt6)
    python setup_env.py --cli-only   # engine only, skips PyQt6
    python setup_env.py --recreate   # delete .venv and start clean

Dependencies come straight from ``pyproject.toml``, so this stays in sync.
Safe to re-run at any time.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
MIN_PYTHON = (3, 12)


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


def check_ffmpeg() -> bool:
    ok = True
    for name in ("ffmpeg", "ffprobe"):
        override = os.environ.get(name.upper())
        found = override or shutil.which(name)
        if found:
            print(f"  [ok] {name}: {found}")
        else:
            ok = False
            print(f"  [missing] {name} is not on PATH")
    if not ok:
        print(
            "\n[warning] ffmpeg/ffprobe were not found. Install them, then reopen the terminal:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  Or set the FFMPEG and FFPROBE environment variables to the binary paths."
        )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up a .venv with all dependencies.")
    parser.add_argument("--cli-only", action="store_true", help="skip the PyQt6 GUI dependency")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv from scratch")
    args = parser.parse_args()

    print("rare-study-downloader setup")
    print(f"  python : {sys.executable}")
    print(f"  project: {PROJECT_DIR}")

    check_python()
    python = create_venv(args.recreate)
    install(python, args.cli_only)

    print("\nChecking ffmpeg/ffprobe:")
    check_ffmpeg()

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

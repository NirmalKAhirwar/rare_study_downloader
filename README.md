# rare-study-downloader

Download DASH lectures from `https://rarestudy.testuk.org` (PenPencil / PW backend).
Ships a **command-line tool** (`main.py`) and a **PyQt6 desktop app** (`gui.py`) that
share the same download engine.

The site embeds a server-rendered `MEDIA_TOKEN`; this tool renders the page, calls
`/v1/videos/video-url-details`, parses the `master.mpd`, downloads segments concurrently,
decrypts ClearKey (CENC) with `ffmpeg`, and muxes video + audio.

Runs **from source only** - there is no compiled/frozen binary, and nothing is embedded.
`cookies.json`, settings and history are plain files you can edit or replace at any time.

## Platforms

Runs on **macOS** and **Windows**, from source (no frozen binaries). See
[Setup from scratch](#setup-from-scratch) for installing Python, ffmpeg and the Python
dependencies.

## Requirements

- Python 3.12+
- `ffmpeg` + `ffprobe` on `PATH`
- A valid `cookies.json` exported from your logged-in browser session
- Python dependencies are installed for you in step 4 (`requests`, `beautifulsoup4`, and
  `PyQt6` only for the GUI)

## Setup from scratch

The same steps work on macOS and Windows. Install the two prerequisites, then run
`setup_env.py`, which creates a `.venv` and installs every Python dependency in one go.

### 1. Install Python 3.12+

| macOS | Windows |
| --- | --- |
| `brew install python@3.12` | `winget install Python.Python.3.12` |
| or <https://www.python.org/downloads/macos/> | or <https://www.python.org/downloads/windows/> (tick **Add python.exe to PATH**) |

Confirm it is on `PATH`: `python3 --version` (macOS) / `python --version` (Windows) —
should print `3.12` or newer.

### 2. Install ffmpeg + ffprobe

| macOS | Windows |
| --- | --- |
| `brew install ffmpeg` | `winget install Gyan.FFmpeg` |

Reopen the terminal, then confirm with `ffmpeg -version` and `ffprobe -version`. No
Homebrew/winget? Grab a build from <https://www.gyan.dev/ffmpeg/builds/> and add its `bin`
folder to `PATH`. You can also point the `FFMPEG` and `FFPROBE` environment variables
straight at the binaries.

### 3. Get the code

```bash
git clone <your-repo-url> rare_study_downloader
cd rare_study_downloader
```

Downloading and unzipping the project instead of cloning works too.

### 4. Create the environment (one command)

macOS:

```bash
python3 setup_env.py
```

Windows (PowerShell or Command Prompt):

```bat
python setup_env.py
```

`setup_env.py` creates `.venv/`, installs the engine and GUI dependencies read straight
from `pyproject.toml`, and reports whether `ffmpeg`/`ffprobe` were found. It is safe to
re-run at any time.

Handy flags:

- `python setup_env.py --cli-only` — skip `PyQt6` (smaller, command line only)
- `python setup_env.py --recreate` — delete `.venv/` and rebuild it cleanly

Prefer [uv](https://docs.astral.sh/uv/)? The equivalent is:

```bash
uv sync --extra gui     # engine + GUI;  uv sync for CLI only
uv run gui.py
```

### 5. Add your cookies

Put your exported cookies in `cookies.json` next to the code: either a browser-extension
JSON array or a plain `{"name": "value"}` map. See
[Cookies are re-read automatically](#cookies-are-re-read-automatically) below.

### 6. Run

| | macOS | Windows |
| --- | --- | --- |
| GUI | `.venv/bin/python gui.py` | `.venv\Scripts\python gui.py` |
| CLI | `.venv/bin/python main.py --help` | `.venv\Scripts\python main.py --help` |

## Cookies are re-read automatically

Cookies expire and are usually refreshed daily. The app **re-reads `cookies.json`
whenever the file changes** - no restart needed:

- It checks the file before every video resolve.
- The GUI has a **Reload cookies** button and shows `Loaded N cookies` / an error badge.
- When a session has expired mid-run, the video is marked **Cookies Expired**. Replace
  `cookies.json` (or fix the path in Settings), press **Retry**, and it continues.

`cookies.json` is git-ignored; never commit it.

## GUI usage

```bash
uv run gui.py          # macOS/Linux-style venv
.venv\Scripts\python gui.py     # Windows
.venv/bin/python gui.py         # macOS
```

Three tabs:

**Browse & Select**

1. Paste a `khazana-chapters` URL (or a single `khazana-video` URL) and press **Load**.
   Previously loaded links are kept in the drop-down, so you rarely paste twice - pick one
   and it loads. **Refresh** re-crawls the site and updates the saved chapter list.
2. Tick chapters/lectures (the tree uses the full tab height), then
   **Add selected to Queue** - this switches to the **Downloads** tab.

Lectures you already downloaded are found in your download folder and **highlighted in
green** with an `[already downloaded]` tag (hover to see the path and size); a chapter is
highlighted once every lecture in it is present. Existing files are **skipped when adding**
to the queue, so you never re-download a lecture by accident. Tick **Force re-download**
(near the Add button, remembers its state) to add and download them again anyway - the
queue marks those rows as forced in their tooltip.

Chapter/lecture crawling is parallel, and each crawl is cached for 12 h
(`~/.rarestudy_cache/`), so pressing **Load** again on a known link is instant.

**Downloads**

Press **Start**. Use **Pause** (partial segments are kept, so it resumes), **Cancel**,
**Retry**, **Remove**, **Clear finished**. **Open folder** reveals output. The tab label
shows the queue size.

Each row has a separate **Video** and **Audio** progress bar (both tracks are downloaded
and muxed by ffmpeg), plus Speed, ETA and Size. The status bar shows the **free space** on
the download volume and, while downloading, how much space the remaining videos need
(highlighted when it will not fit).

**Settings**

Everything is live-applied and saved to `~/.rarestudy_settings.json`:

- **Download folder** - defaults to `downloads/` next to the source code. Nothing prompts
  during loading; change it here (or **Browse**) only when you want a different location.
- **Default quality**, **cookies.json** (with **Reload cookies**), container, videos in
  parallel, segment workers, retries, ffprobe verification, theme.
- **Instant reload** toggle, **Clear saved links**, **Clear cached chapter lists**.

Queue history is kept in `~/.rarestudy_history.json` and restored as paused after a restart.
Cookies are re-read automatically whenever the file changes, so you can replace
`cookies.json` while the app runs and just press **Start** again.

## CLI usage

### Single video

```bash
.venv/bin/python main.py "https://rarestudy.testuk.org/khazana-video?..."
.venv/bin/python main.py --list "https://rarestudy.testuk.org/khazana-video?..."     # list qualities
.venv/bin/python main.py -q 720 -n "my-lecture" "https://rarestudy.testuk.org/khazana-video?..."
```

### Whole chapters / subject (khazana-chapters URL)

```bash
URL="https://rarestudy.testuk.org/khazana-chapters?..."
.venv/bin/python main.py --list "$URL"                 # show chapters + video counts
.venv/bin/python main.py --dry-run "$URL"              # preview selection without downloading
.venv/bin/python main.py "$URL"                         # interactive: pick chapters, then videos
.venv/bin/python main.py -y "$URL"                      # download everything, no prompts
.venv/bin/python main.py --chapters 4,7 --videos 1,3-5 "$URL"
```

### Options

| Flag | Description |
| --- | --- |
| `-q, --quality` | `best` or a height such as `720`, `480`, `360`, `240` |
| `-o, --output-dir` | Base output directory (default `downloads/`) |
| `-n, --name` | Output file name (single video mode) |
| `-c, --cookies` | Path to `cookies.json` |
| `--container` | `DASH` (default) or `HLS` |
| `--list` | List streams (video) or chapters (collection) and exit |
| `-y, --all` | Download everything without prompting |
| `--chapters` | Chapter selection: `all`, `1,3`, `2-5` |
| `--videos` | Video selection per chapter: `all`, `1,3`, `2-5`, `none` |
| `--force` | Re-download videos that already exist |
| `--no-verify` | Skip ffprobe duration verification of finished files |
| `--dry-run` | Show what would be downloaded and exit |
| `--retries` | Per-video download attempts (default 3) |
| `--workers` | Parallel segment downloads per track (default 16) |
| `--parallel` | Videos downloaded in parallel (default 3) |

### Interactive selection

When run in a terminal without `--all`/`--chapters`/`--videos`:

- Chapters are listed with video counts; enter `all`, `1,3`, `2-5`, or `none`.
- For each selected chapter its videos are listed; select the same way.

Ranges and lists can be mixed, e.g. `1, 3-5, 8`. In a non-interactive shell (piped),
everything is selected by default; use `--chapters`/`--videos` to filter.

## Output layout

```
downloads/
└── Computer Organisation and Architecture - Computer Science/   # subject
    ├── .state.json                                               # download manifest
    ├── CH 01 _ Basics of COA/                                    # chapter
    │   ├── 01 - Basics of COA 01 _ Basics and Prerequisites...mp4
    │   ├── 02 - Basics of COA 02 _ Basics and Registers.mp4
    │   └── ...
    └── CH 07 _ Cache Organization/
        └── ...
```

Files are prefixed with the lecture's position in its chapter (`01 - `, `02 - `, ...),
so a folder sorts in the same order the lectures are meant to be watched. The prefix
uses the position within the whole chapter, so it stays stable even when you download
only a subset.

## Resume, skip, retry, verify

- **Manifest**: `<subject>/.state.json` records every completed video (video id →
  file, size, duration) so finished files are recognized without re-resolving them.
- **Skip**: a video is skipped when its manifest entry matches the file on disk, or
  when an existing `.mp4` (≥ 10 KB) passes verification. `--force` overrides.
- **Verify**: each finished file is probed with `ffprobe` and its duration compared
  to the catalog duration (tolerance ≈ 20 s or 2%). Mismatches/truncated files are
  re-downloaded. Disable with `--no-verify`.
- **Resume**: segments are cached in `downloads/.tmp/<videoId>_<quality>/`; an interrupted
  or paused video continues where it left off on the next run.
- **Retry**: each segment is retried, and each whole video is retried `--retries`
  times with backoff. Failures are listed in the summary and do not stop the batch.

## Performance

Downloads are parallel at two levels:

- **Segments per track**: `--workers` (default 16) threads fetch DASH segments concurrently.
- **Videos at once**: `--parallel` (default 3) videos download simultaneously; each video
  uses its own temp dir and thread-local HTTP session, so runs are safe and resumable.

Higher values are faster but open more simultaneous connections to one host. If you start
seeing `403`/Cloudflare errors, lower them:

```bash
.venv/bin/python main.py -y --workers 8 --parallel 2 "$URL"
```

## Project layout

| File | Purpose |
| --- | --- |
| `setup_env.py` | One-command setup: creates `.venv`, installs all dependencies, checks ffmpeg/ffprobe |
| `main.py` | CLI: argument parsing, mode detection, interactive selection, batch orchestration |
| `gui.py` | GUI entry point (`python gui.py`) |
| `jobs.py` | Shared job layer: collection planning, parallel crawl + chapter-list cache, per-video job with status/progress/cancel |
| `cookies_store.py` | Live-reloading `cookies.json` store (detects file changes) |
| `catalog.py` | Proxy/API client (subject name, chapters, sections, lectures) |
| `downloader.py` | MPD resolution, concurrent segment download, decrypt, mux, ffprobe |
| `state.py` | Completed-download manifest (`.state.json`) |
| `utils.py` | Cookie loading, filename sanitizing, duration/byte helpers, tool hints |
| `config.py` | Base URL, API proxy base, headers, paths, tunables |
| `ui/` | PyQt6 GUI: tabs (browse, downloads, settings), task model, queue manager, loader, dialogs, theme, persistence |

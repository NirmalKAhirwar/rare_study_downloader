# rare-study-downloader

Download DASH lectures from `https://rarestudy.testuk.org` (PenPencil / PW backend).

The site embeds a server-rendered `MEDIA_TOKEN`; this tool renders the page, calls
`/v1/videos/video-url-details`, parses the `master.mpd`, downloads segments concurrently,
decrypts ClearKey (CENC) with `ffmpeg`, and muxes video + audio.

## Requirements

- Python 3.12+
- `ffmpeg` + `ffprobe` on `PATH`
- Python deps (already in `pyproject.toml`): `requests`
- A valid `cookies.json` exported from your logged-in browser session

## Setup

```bash
uv sync            # or: pip install requests
```

Put your exported cookies in `cookies.json` (browser extension JSON array or a plain
`{"name": "value"}` map). Cookies expire - refresh them if you get "session expired".

## Usage

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

### Dry-run

`--dry-run` builds the chapter/video tree, applies your selection, and prints the
destination path for each video (counted as `planned`). It never resolves the stream
or downloads bytes. It also ignores already-downloaded files, so it reports every
selected video as planned.

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
- **Resume**: segments are cached in `downloads/.tmp/<videoId>/`; an interrupted
  video continues where it left off on the next run.
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

When multiple videos run in parallel, per-segment progress is replaced by
`start`/`done`/`skip`/`FAILED` lines to keep the console readable.

## Project layout

| File | Purpose |
| --- | --- |
| `main.py` | CLI, mode detection, interactive selection, batch orchestration |
| `catalog.py` | Proxy/API client (subject name, chapters, sections, lectures) |
| `downloader.py` | MPD resolution, concurrent segment download, decrypt, mux, ffprobe |
| `state.py` | Completed-download manifest (`.state.json`) |
| `utils.py` | Cookie loading, retrying session, filename/duration helpers |
| `config.py` | Base URL, API proxy base, headers, paths, tunables |

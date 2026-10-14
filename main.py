import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import requests

from catalog import Catalog, CatalogError, Chapter, CourseRef, Lecture
from config import (
    BASE_URL,
    COOKIES_FILE,
    DOWNLOAD_DIR,
    DOWNLOAD_WORKERS,
    VIDEO_DOWNLOAD_ATTEMPTS,
    VIDEO_DOWNLOAD_WORKERS,
)
from downloader import DownloadError, RareStudyDownloader, ResolveError, probe_duration
from state import StateStore
from utils import duration_to_seconds, load_cookies, sanitize_filename

MIN_VALID_SIZE = 10 * 1024
VERIFY_TOLERANCE_RATIO = 0.02
VERIFY_TOLERANCE_MIN = 20.0
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rare-study-downloader",
        description="Download DASH lectures from rarestudy.testuk.org",
    )
    parser.add_argument("url", nargs="?", help="khazana-video / khazana-chapters URL")
    parser.add_argument("-q", "--quality", default="best", help="best or a height like 720/480/360")
    parser.add_argument("-o", "--output-dir", default=str(DOWNLOAD_DIR), help="Base output directory")
    parser.add_argument("-n", "--name", default=None, help="Output file name (single video mode)")
    parser.add_argument("-c", "--cookies", default=str(COOKIES_FILE), help="Path to cookies.json")
    parser.add_argument("--container", default="DASH", choices=["DASH", "HLS"], help="Stream container")
    parser.add_argument("--list", action="store_true", help="List streams/chapters and exit")
    parser.add_argument("-y", "--all", action="store_true", help="Download everything without prompting")
    parser.add_argument("--chapters", default=None, help="Chapter selection: all | 1,3 | 2-5")
    parser.add_argument("--videos", default=None, help="Video selection per chapter: all | 1,3 | 2-5")
    parser.add_argument("--force", action="store_true", help="Re-download videos that already exist")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="Skip ffprobe duration verification")
    parser.set_defaults(verify=True)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded and exit")
    parser.add_argument("--retries", type=int, default=VIDEO_DOWNLOAD_ATTEMPTS, help="Per-video download attempts")
    parser.add_argument("--workers", type=int, default=DOWNLOAD_WORKERS, help="Parallel segment downloads per track")
    parser.add_argument("--parallel", type=int, default=VIDEO_DOWNLOAD_WORKERS, help="Videos downloaded in parallel")
    return parser


def parse_spec(spec: str, count: int) -> List[int]:
    spec = (spec or "").strip().lower()
    if spec in ("", "all", "a", "*"):
        return list(range(count))
    if spec in ("none", "n", "skip", "-"):
        return []
    chosen = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                chosen.update(range(int(start) - 1, int(end)))
        elif part.isdigit():
            chosen.add(int(part) - 1)
    return sorted(i for i in chosen if 0 <= i < count)


def prompt_spec(count: int, label: str) -> List[int]:
    if not sys.stdin.isatty():
        return list(range(count))
    raw = input(f"Select {label} [all]: ").strip()
    return parse_spec(raw or "all", count)


def print_lectures(lectures: List[Lecture]) -> None:
    for idx, lecture in enumerate(lectures, 1):
        dur = f" ({lecture.duration})" if lecture.duration else ""
        print(f"  {idx:>3}. {lecture.name}{dur}")


def print_chapters(chapters: List[Chapter]) -> None:
    for idx, chapter in enumerate(chapters, 1):
        print(f"  {idx:>3}. {chapter.name}  [{len(chapter.lectures)} videos]")


def unique_name(name: str, used: set) -> str:
    base = sanitize_filename(name, "video")
    candidate = base
    counter = 2
    while candidate.lower() in used:
        candidate = f"{base} ({counter})"
        counter += 1
    used.add(candidate.lower())
    return candidate


def verify_output(output: Path, expected: float, enabled: bool):
    """Probe output with ffprobe and compare against expected seconds.

    Returns (ok, measured_seconds).
    """

    if not enabled:
        return True, None
    measured = probe_duration(output)
    if measured is None:
        return False, None
    if not expected:
        return True, measured
    tolerance = max(VERIFY_TOLERANCE_MIN, expected * VERIFY_TOLERANCE_RATIO)
    return abs(measured - expected) <= tolerance, measured


def select_chapters(args, chapters: List[Chapter]) -> List[Chapter]:
    if args.chapters is not None:
        return [chapters[i] for i in parse_spec(args.chapters, len(chapters))]
    if args.all:
        return chapters
    if not sys.stdin.isatty():
        print("Non-interactive shell: selecting all chapters (use --chapters to filter).")
        return chapters
    print("\nChapters:")
    print_chapters(chapters)
    indices = prompt_spec(len(chapters), "chapters")
    return [chapters[i] for i in indices]


def select_lectures(args, chapter: Chapter) -> List[Lecture]:
    if not chapter.lectures:
        return []
    if args.videos is not None:
        return [chapter.lectures[i] for i in parse_spec(args.videos, len(chapter.lectures))]
    if args.all or not sys.stdin.isatty():
        return chapter.lectures
    print(f"\nVideos in '{chapter.name}':")
    print_lectures(chapter.lectures)
    indices = prompt_spec(len(chapter.lectures), "videos (comma/range, 'none' to skip)")
    return [chapter.lectures[i] for i in indices]


def download_single(args, downloader: RareStudyDownloader) -> int:
    output_dir = Path(args.output_dir)
    try:
        info = downloader.resolve(args.url, container=args.container)
    except (ResolveError, requests.RequestException) as exc:
        print(f"Could not resolve stream: {exc}", file=sys.stderr)
        return 1

    if args.list:
        downloader.list_streams(info)
        return 0

    name = sanitize_filename(args.name or info.title, "video")
    output = output_dir / f"{name}.mp4"
    if not args.force and output.exists() and output.stat().st_size >= MIN_VALID_SIZE:
        print(f"Already downloaded, skipping: {output}")
        return 0

    try:
        result = downloader.download(info, output, quality=args.quality, work_key=info.title)
    except DownloadError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    if args.verify:
        ok, _ = verify_output(output, 0.0, True)
        if not ok:
            print("Downloaded file failed verification (unreadable by ffprobe).", file=sys.stderr)
            return 1
    print(f"Saved: {result}")
    return 0


def download_collection(args, downloader: RareStudyDownloader, cookies) -> int:
    ref = CourseRef.from_url(args.url)
    catalog = Catalog(cookies)
    ref.subject_name = catalog.subject_name(ref.program_id, ref.subject_id) or ref.batch_name or ref.subject_id

    chapters = catalog.chapters(ref)
    if not chapters:
        print("No chapters found for this subject.", file=sys.stderr)
        return 1

    for chapter in chapters:
        catalog.lectures(ref, chapter)

    if args.list:
        print(f"Subject: {ref.subject_name}")
        total = sum(len(c.lectures) for c in chapters)
        print(f"Chapters: {len(chapters)}  Videos: {total}")
        print_chapters(chapters)
        return 0

    selected_chapters = select_chapters(args, chapters)
    if not selected_chapters:
        print("Nothing selected.")
        return 0

    subject_dir = Path(args.output_dir) / sanitize_filename(ref.subject_name, ref.subject_id)
    subject_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(subject_dir / ".state.json")
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "planned": 0}
    failures: List[str] = []

    tasks: List[tuple] = []
    for chapter in selected_chapters:
        lectures = select_lectures(args, chapter)
        if not lectures:
            continue
        chapter_dir = subject_dir / chapter.folder
        chapter_dir.mkdir(parents=True, exist_ok=True)
        used: set = set()
        width = max(2, len(str(len(chapter.lectures))))
        print(f"\n=== {chapter.name} ({len(lectures)} videos) ===")
        for lecture in lectures:
            base = unique_name(lecture.name, used)
            prefix = f"{lecture.order:0{width}d} - " if lecture.order else ""
            output = chapter_dir / f"{prefix}{base}.mp4"
            if args.dry_run:
                print(f"  plan   {output.relative_to(Path(args.output_dir))}")
                stats["planned"] += 1
                continue
            tasks.append((lecture, output))

    if tasks:
        parallel = max(1, min(args.parallel, len(tasks)))
        if parallel == 1:
            for lecture, output in tasks:
                status = download_lecture(args, downloader, ref, lecture, output, state, subject_dir, show_progress=True)
                stats[status] += 1
                if status == "failed":
                    failures.append(lecture.name)
        else:
            log(f"\nDownloading {len(tasks)} videos with {parallel} parallel workers...")
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = {
                    pool.submit(download_lecture, args, downloader, ref, lecture, output, state, subject_dir, False): lecture
                    for lecture, output in tasks
                }
                for future in as_completed(futures):
                    lecture = futures[future]
                    status = future.result()
                    stats[status] += 1
                    if status == "failed":
                        failures.append(lecture.name)

    print("\nSummary: " + ", ".join(f"{k}={v}" for k, v in stats.items() if v))
    if failures:
        print("Failed videos:")
        for name in failures:
            print(f"  - {name}")
    return 0 if stats["failed"] == 0 else 1


def download_lecture(args, downloader: RareStudyDownloader, ref: CourseRef, lecture: Lecture, output: Path, state: StateStore, base_dir: Path, show_progress: bool = True) -> str:
    expected = duration_to_seconds(lecture.duration)

    if not args.force:
        if state.is_complete(lecture.video_id, output, MIN_VALID_SIZE):
            log(f"  skip   {lecture.name} (verified)")
            return "skipped"
        if output.exists() and output.stat().st_size >= MIN_VALID_SIZE:
            ok, measured = verify_output(output, expected, args.verify)
            if ok:
                state.record(lecture.video_id, output, base_dir, measured, verified=args.verify)
                log(f"  skip   {lecture.name} (verified)")
                return "skipped"
            log(f"  redo   {lecture.name} (existing file failed verification)")

    page_url = lecture.page_url(ref.program_id, ref.subject_id)
    attempts = max(1, args.retries)
    for attempt in range(1, attempts + 1):
        try:
            info = downloader.resolve(page_url, container=args.container)
            downloader.download(info, output, quality=args.quality, work_key=lecture.video_id, show_progress=show_progress, logger=log)
            ok, measured = verify_output(output, expected, args.verify)
            if not ok:
                raise DownloadError(
                    f"verification failed: got {measured}s, expected {lecture.duration or 'unknown'}"
                )
            state.record(lecture.video_id, output, base_dir, measured, verified=args.verify)
            log(f"  done   {lecture.name}")
            return "downloaded"
        except (DownloadError, ResolveError, requests.RequestException) as exc:
            if attempt < attempts:
                wait = min(30, 2 * attempt)
                log(f"  retry  {lecture.name} (attempt {attempt}/{attempts}): {exc}")
                time.sleep(wait)
            else:
                log(f"  FAILED {lecture.name}: {exc}")
    return "failed"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.url:
        build_parser().print_help()
        return 1

    if not Path(args.cookies).exists():
        print(f"Cookies file not found: {args.cookies}", file=sys.stderr)
        return 1

    try:
        cookies = load_cookies(Path(args.cookies))
    except Exception as exc:
        print(f"Failed to load cookies: {exc}", file=sys.stderr)
        return 1

    downloader = RareStudyDownloader(cookies=cookies, base_url=BASE_URL, workers=args.workers)
    path = urlparse(args.url).path

    try:
        if path.endswith("/khazana-chapters"):
            return download_collection(args, downloader, cookies)
        return download_single(args, downloader)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except CatalogError as exc:
        print(f"Catalog error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

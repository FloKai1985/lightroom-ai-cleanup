"""Milestone-1 CLI: run the standalone analyzer without the HTTP/plugin layers.

This is a thin wrapper over `service.analyzer.AnalyzerService` and the
repository — it exists so the analysis pipeline can be exercised end-to-end
against a folder of images before the FastAPI service (Milestone 2) or the
Lightroom plugin (Milestone 3) exist.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from PIL import ExifTags, Image
from sqlalchemy.orm import Session, sessionmaker

from lr_cleanup.config import get_settings
from lr_cleanup.database.repository import PhotoInput, Repository
from lr_cleanup.database.session import init_db, make_engine, make_session_factory, session_scope
from lr_cleanup.logging_config import configure_logging
from lr_cleanup.service.analyzer import AnalyzerService

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
_EXIF_DATETIME_ORIGINAL = next(
    (k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal"), 36867
)


def _read_image_info(path: Path) -> tuple[int | None, int | None, datetime | None]:
    """Best-effort width/height/capture-time from the file itself.

    Falls back to the filesystem mtime for capture_time when there is no
    EXIF DateTimeOriginal — most real Lightroom-managed originals have EXIF,
    but this keeps `register` usable against arbitrary image folders too.
    """
    capture_time: datetime | None = None
    width: int | None = None
    height: int | None = None
    try:
        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
            raw = exif.get(_EXIF_DATETIME_ORIGINAL)
            if raw:
                try:
                    capture_time = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
                except ValueError:
                    capture_time = None
    except Exception:
        pass

    if capture_time is None:
        capture_time = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)

    return width, height, capture_time


def _open_repository_session() -> sessionmaker[Session]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    return make_session_factory(engine)


def cmd_register(args: argparse.Namespace) -> None:
    directory = Path(args.directory)
    if not directory.is_dir():
        raise SystemExit(f"Not a directory: {directory}")

    paths = sorted(p for p in directory.rglob("*") if p.suffix.lower() in _IMAGE_SUFFIXES)
    session_factory = _open_repository_session()
    registered = 0
    with session_scope(session_factory) as session:
        repo = Repository(session)
        for path in paths:
            stat = path.stat()
            width, height, capture_time = _read_image_info(path)
            repo.upsert_photo(
                PhotoInput(
                    original_path=str(path.resolve()),
                    file_size=stat.st_size,
                    file_mtime=stat.st_mtime,
                    width=width,
                    height=height,
                    capture_time=capture_time,
                )
            )
            registered += 1
    print(f"Registered {registered} photo(s) from {directory}")


def cmd_analyze(_args: argparse.Namespace) -> None:
    session_factory = _open_repository_session()
    with session_scope(session_factory) as session:
        repo = Repository(session)
        job = AnalyzerService(repo).run_job()
    print(
        f"Job {job.id}: {job.status.value} "
        f"({job.processed_photos} processed, {job.failed_photos} failed, "
        f"{job.total_photos} total)"
    )


def cmd_group(_args: argparse.Namespace) -> None:
    session_factory = _open_repository_session()
    with session_scope(session_factory) as session:
        repo = Repository(session)
        groups = AnalyzerService(repo).regenerate_groups()
    by_type: dict[str, int] = {}
    for g in groups:
        by_type[g.group_type.value] = by_type.get(g.group_type.value, 0) + 1
    print(f"Regenerated {len(groups)} group(s): {by_type}")


def cmd_groups(_args: argparse.Namespace) -> None:
    session_factory = _open_repository_session()
    with session_scope(session_factory) as session:
        repo = Repository(session)
        groups = repo.list_groups(limit=1000)
        photo_ids = {member.photo_id for group in groups for member in group.members}
        photos_by_id = repo.get_photos_by_ids(list(photo_ids))
        for group in groups:
            print(f"\nGroup #{group.id} [{group.group_type.value}]")
            for member in sorted(group.members, key=lambda m: m.rank):
                photo = photos_by_id.get(member.photo_id)
                path = photo.original_path if photo else "?"
                print(
                    f"  rank={member.rank} {member.recommendation.value:16s} "
                    f"score={member.keeper_score:.3f} conf={member.confidence:.2f} "
                    f"reasons={member.reasons} path={path}"
                )


def cmd_blurry(args: argparse.Namespace) -> None:
    session_factory = _open_repository_session()
    with session_scope(session_factory) as session:
        repo = Repository(session)
        analyses = repo.list_blurry_photos(blur_confidence_min=args.threshold, limit=args.limit)
        photos_by_id = repo.get_photos_by_ids([a.photo_id for a in analyses])
        for analysis in analyses:
            photo = photos_by_id.get(analysis.photo_id)
            path = photo.original_path if photo else "?"
            print(f"blur_confidence={analysis.blur_confidence:.3f} path={path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lr-cleanup", description="Lightroom AI Cleanup analyzer"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser("register", help="Register all images in a directory")
    p_register.add_argument("directory", help="Directory to scan for images")
    p_register.set_defaults(func=cmd_register)

    p_analyze = sub.add_parser("analyze", help="Run analysis over all registered photos")
    p_analyze.set_defaults(func=cmd_analyze)

    p_group = sub.add_parser("group", help="Recompute duplicate/near-duplicate groups + rankings")
    p_group.set_defaults(func=cmd_group)

    p_groups = sub.add_parser("groups", help="List current groups and their ranked members")
    p_groups.set_defaults(func=cmd_groups)

    p_blurry = sub.add_parser("blurry", help="List probable-blur photos")
    p_blurry.add_argument("--threshold", type=float, default=0.5)
    p_blurry.add_argument("--limit", type=int, default=100)
    p_blurry.set_defaults(func=cmd_blurry)

    return parser


def main() -> None:
    configure_logging(get_settings().log_level)
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

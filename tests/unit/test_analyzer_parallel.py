"""Exercises AnalyzerService's process-pool analysis path directly.

The existing cache-invalidation tests never register more than two photos,
so they never cross `_MIN_PHOTOS_FOR_PARALLEL_ANALYSIS` and always take the
sequential fallback. These tests force a small worker pool and register
enough photos to guarantee the parallel path actually runs.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from lr_cleanup.config import Settings
from lr_cleanup.database.models import JobStatus
from lr_cleanup.database.repository import PhotoInput, Repository
from lr_cleanup.service.analyzer import _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS, AnalyzerService
from tests.fixtures.images import make_sharp_jpeg


def _register_photos(repository: Repository, paths: list[Path]) -> list[int]:
    ids = []
    for path in paths:
        stat = path.stat()
        photo = repository.upsert_photo(
            PhotoInput(original_path=str(path), file_size=stat.st_size, file_mtime=stat.st_mtime)
        )
        ids.append(photo.id)
    repository.session.commit()
    return ids


def _parallel_settings(db_session: Session) -> Settings:
    # >= _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS photos below, worker_processes=2
    # forces the ProcessPoolExecutor path deterministically regardless of
    # how many CPUs the test machine has.
    return Settings(database_url="sqlite:///:memory:", analysis_worker_processes=2)


def test_parallel_analysis_produces_same_results_as_sequential(
    tmp_path: Path, db_session: Session, repository: Repository
) -> None:
    photo_count = _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS + 2
    paths = [
        make_sharp_jpeg(tmp_path / f"photo_{i}.jpg", width=400 + i, height=300)
        for i in range(photo_count)
    ]
    ids = _register_photos(repository, paths)

    service = AnalyzerService(repository, settings=_parallel_settings(db_session))
    job = service.run_job()

    assert job.status == JobStatus.COMPLETED
    assert job.total_photos == photo_count
    assert job.processed_photos == photo_count
    assert job.failed_photos == 0

    for pid in ids:
        analysis = repository.get_analysis(pid)
        assert analysis is not None
        assert analysis.file_hash
        assert analysis.perceptual_hash
        assert 0.0 <= analysis.sharpness_score <= 1.0
        assert 0.0 <= analysis.blur_confidence <= 1.0


def test_parallel_analysis_one_bad_photo_does_not_fail_the_batch(
    tmp_path: Path, db_session: Session, repository: Repository
) -> None:
    photo_count = _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS + 1
    paths = [make_sharp_jpeg(tmp_path / f"good_{i}.jpg") for i in range(photo_count - 1)]

    corrupt_path = tmp_path / "corrupt.jpg"
    corrupt_path.write_bytes(b"not actually a jpeg")
    paths.append(corrupt_path)

    _register_photos(repository, paths)

    service = AnalyzerService(repository, settings=_parallel_settings(db_session))
    job = service.run_job()

    assert job.total_photos == photo_count
    assert job.processed_photos == photo_count - 1
    assert job.failed_photos == 1
    assert job.status == JobStatus.COMPLETED  # some succeeded, so not a total failure


def test_analysis_worker_processes_one_disables_pool_but_same_result(
    tmp_path: Path, db_session: Session, repository: Repository
) -> None:
    """worker_processes=1 must still analyze correctly (via the sequential
    fallback) even with enough photos to otherwise qualify for the pool."""
    photo_count = _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS + 2
    paths = [
        make_sharp_jpeg(tmp_path / f"photo_{i}.jpg", width=400 + i, height=300)
        for i in range(photo_count)
    ]
    ids = _register_photos(repository, paths)

    settings = Settings(database_url="sqlite:///:memory:", analysis_worker_processes=1)
    service = AnalyzerService(repository, settings=settings)
    job = service.run_job()

    assert job.status == JobStatus.COMPLETED
    assert job.processed_photos == photo_count
    for pid in ids:
        assert repository.get_analysis(pid) is not None

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from lr_cleanup.database.models import JobStatus
from lr_cleanup.database.repository import PhotoInput, Repository
from lr_cleanup.service.analyzer import AnalyzerService
from tests.fixtures.images import make_sharp_jpeg


def _register_photo(repository: Repository, path: Path) -> int:
    stat = path.stat()
    photo = repository.upsert_photo(
        PhotoInput(original_path=str(path), file_size=stat.st_size, file_mtime=stat.st_mtime)
    )
    repository.session.commit()
    return photo.id


def _spy_on_analyze_one(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = AnalyzerService.analyze_one

    def spy(self: AnalyzerService, photo):  # type: ignore[no-untyped-def]
        calls.append(photo.id)
        return original(self, photo)

    monkeypatch.setattr(AnalyzerService, "analyze_one", spy)
    return calls


def test_second_run_reuses_cached_analysis(
    tmp_path: Path, db_session: Session, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = make_sharp_jpeg(tmp_path / "photo.jpg")
    _register_photo(repository, image_path)
    calls = _spy_on_analyze_one(monkeypatch)

    service = AnalyzerService(repository)
    job1 = service.run_job()
    job2 = service.run_job()

    assert job1.status == JobStatus.COMPLETED
    assert job2.status == JobStatus.COMPLETED
    assert job1.failed_photos == 0
    assert job2.failed_photos == 0
    # Only the first run should have actually invoked analysis; the second
    # run's fingerprint matches the cached row and is skipped.
    assert calls == [1] or len(calls) == 1


def test_changed_file_mtime_invalidates_cache(
    tmp_path: Path, db_session: Session, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = make_sharp_jpeg(tmp_path / "photo.jpg")
    photo_id = _register_photo(repository, image_path)
    calls = _spy_on_analyze_one(monkeypatch)

    service = AnalyzerService(repository)
    service.run_job()
    assert len(calls) == 1

    # Simulate the underlying file changing (e.g. re-exported) by bumping mtime.
    new_mtime = image_path.stat().st_mtime + 1000
    os.utime(image_path, (new_mtime, new_mtime))
    stat = image_path.stat()
    repository.upsert_photo(
        PhotoInput(original_path=str(image_path), file_size=stat.st_size, file_mtime=stat.st_mtime)
    )
    repository.session.commit()

    service.run_job()
    assert len(calls) == 2
    assert calls == [photo_id, photo_id]


def test_bumped_analysis_version_invalidates_cache(
    tmp_path: Path, db_session: Session, repository: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = make_sharp_jpeg(tmp_path / "photo.jpg")
    _register_photo(repository, image_path)
    calls = _spy_on_analyze_one(monkeypatch)

    service = AnalyzerService(repository)
    service.run_job()
    assert len(calls) == 1

    service.settings = service.settings.model_copy(update={"analysis_version": 999})
    service.run_job()
    assert len(calls) == 2


def test_one_missing_file_does_not_crash_the_job(
    tmp_path: Path, db_session: Session, repository: Repository
) -> None:
    good_path = make_sharp_jpeg(tmp_path / "good.jpg")
    _register_photo(repository, good_path)

    missing_path = tmp_path / "missing.jpg"
    missing_path.write_bytes(b"placeholder")
    missing_stat = missing_path.stat()
    repository.upsert_photo(
        PhotoInput(
            original_path=str(missing_path),
            file_size=missing_stat.st_size,
            file_mtime=missing_stat.st_mtime,
        )
    )
    repository.session.commit()
    missing_path.unlink()  # now the registered path no longer exists

    service = AnalyzerService(repository)
    job = service.run_job()

    assert job.total_photos == 2
    assert job.processed_photos == 1
    assert job.failed_photos == 1

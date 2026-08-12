from __future__ import annotations

from sqlalchemy.orm import Session

from lr_cleanup.database.models import GroupType, Recommendation
from lr_cleanup.database.repository import (
    AnalysisResult,
    GroupMemberInput,
    PhotoInput,
    Repository,
)


def _make_photo(repository: Repository, db_session: Session, path: str) -> int:
    photo = repository.upsert_photo(PhotoInput(original_path=path, file_size=1, file_mtime=1.0))
    db_session.commit()
    return photo.id


def test_count_photos_matches_number_registered(
    repository: Repository, db_session: Session
) -> None:
    assert repository.count_photos() == 0
    _make_photo(repository, db_session, "/tmp/a.jpg")
    _make_photo(repository, db_session, "/tmp/b.jpg")
    assert repository.count_photos() == 2


def test_list_jobs_orders_most_recent_first(repository: Repository, db_session: Session) -> None:
    job1 = repository.create_job(total_photos=1)
    job2 = repository.create_job(total_photos=2)
    db_session.commit()

    jobs = repository.list_jobs()
    assert [j.id for j in jobs] == [job2.id, job1.id]


def test_list_jobs_respects_limit(repository: Repository, db_session: Session) -> None:
    for _ in range(5):
        repository.create_job(total_photos=0)
    db_session.commit()

    assert len(repository.list_jobs(limit=2)) == 2


def test_get_latest_job_returns_none_when_empty(repository: Repository) -> None:
    assert repository.get_latest_job() is None


def test_get_latest_job_returns_most_recent(repository: Repository, db_session: Session) -> None:
    repository.create_job(total_photos=1)
    job2 = repository.create_job(total_photos=2)
    db_session.commit()

    latest = repository.get_latest_job()
    assert latest is not None
    assert latest.id == job2.id


def test_list_groups_filters_by_multiple_types(repository: Repository, db_session: Session) -> None:
    photo_id = _make_photo(repository, db_session, "/tmp/a.jpg")
    member = GroupMemberInput(
        photo_id=photo_id,
        keeper_score=0.9,
        rank=1,
        recommendation=Recommendation.KEEPER,
        confidence=0.5,
    )
    repository.create_group(GroupType.EXACT_DUPLICATE, [member])
    repository.create_group(GroupType.BURST, [member])
    repository.create_group(GroupType.NEAR_DUPLICATE, [member])
    db_session.commit()

    assert len(repository.list_groups()) == 3
    exact_only = repository.list_groups(group_types=[GroupType.EXACT_DUPLICATE])
    assert [g.group_type for g in exact_only] == [GroupType.EXACT_DUPLICATE]

    near_like = repository.list_groups(group_types=[GroupType.BURST, GroupType.NEAR_DUPLICATE])
    assert {g.group_type for g in near_like} == {GroupType.BURST, GroupType.NEAR_DUPLICATE}


def test_count_groups_by_type(repository: Repository, db_session: Session) -> None:
    photo_id = _make_photo(repository, db_session, "/tmp/a.jpg")
    member = GroupMemberInput(
        photo_id=photo_id,
        keeper_score=0.9,
        rank=1,
        recommendation=Recommendation.KEEPER,
        confidence=0.5,
    )
    repository.create_group(GroupType.EXACT_DUPLICATE, [member])
    repository.create_group(GroupType.EXACT_DUPLICATE, [member])
    repository.create_group(GroupType.BURST, [member])
    db_session.commit()

    counts = repository.count_groups_by_type()
    assert counts[GroupType.EXACT_DUPLICATE] == 2
    assert counts[GroupType.BURST] == 1
    assert GroupType.NEAR_DUPLICATE not in counts


def test_count_blurry_photos_respects_threshold(
    repository: Repository, db_session: Session
) -> None:
    photo_id = _make_photo(repository, db_session, "/tmp/a.jpg")
    repository.upsert_analysis(
        photo_id,
        AnalysisResult(
            file_hash="h",
            perceptual_hash="p",
            sharpness_score=0.1,
            blur_confidence=0.8,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            analysis_version=1,
            fingerprint="f",
        ),
    )
    db_session.commit()

    assert repository.count_blurry_photos(0.5) == 1
    assert repository.count_blurry_photos(0.9) == 0


def test_count_analyzed_photos(repository: Repository, db_session: Session) -> None:
    assert repository.count_analyzed_photos() == 0
    photo_id = _make_photo(repository, db_session, "/tmp/a.jpg")
    repository.upsert_analysis(
        photo_id,
        AnalysisResult(
            file_hash="h",
            perceptual_hash="p",
            sharpness_score=0.5,
            blur_confidence=0.5,
            exposure_score=0.5,
            highlight_clipping=0.0,
            shadow_clipping=0.0,
            analysis_version=1,
            fingerprint="f",
        ),
    )
    db_session.commit()
    assert repository.count_analyzed_photos() == 1

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.images import make_blurred_jpeg, make_sharp_jpeg

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _register_payload(path: Path, capture_time: datetime, **overrides: object) -> dict:
    stat = path.stat()
    payload = {
        "original_path": str(path),
        "file_size": stat.st_size,
        "file_mtime": stat.st_mtime,
        "capture_time": capture_time.isoformat(),
        "width": 400,
        "height": 300,
    }
    payload.update(overrides)
    return payload


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_register_photos_empty_list_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/photos/register", json={"photos": []})
    assert response.status_code == 400


def test_register_photo_is_idempotent_upsert(client: TestClient, tmp_path: Path) -> None:
    image = make_sharp_jpeg(tmp_path / "a.jpg")
    payload = {"photos": [_register_payload(image, T0)]}

    first = client.post("/api/v1/photos/register", json=payload)
    second = client.post("/api/v1/photos/register", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["registered"][0]["photo_id"] == second.json()["registered"][0]["photo_id"]


def test_job_not_found_returns_404(client: TestClient) -> None:
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404
    assert client.get("/api/v1/jobs/does-not-exist/results").status_code == 404


def test_group_not_found_returns_404(client: TestClient) -> None:
    assert client.get("/api/v1/groups/999999").status_code == 404


def test_create_job_analyzes_registered_photo(client: TestClient, tmp_path: Path) -> None:
    image = make_sharp_jpeg(tmp_path / "sharp.jpg")
    client.post("/api/v1/photos/register", json={"photos": [_register_payload(image, T0)]})

    response = client.post("/api/v1/jobs", json={"regenerate_groups": False})
    assert response.status_code == 202
    job = response.json()
    assert job["status"] in ("pending", "running", "completed")

    status = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert status["status"] == "completed"
    assert status["total_photos"] == 1
    assert status["processed_photos"] == 1
    assert status["failed_photos"] == 0


def test_one_missing_file_does_not_fail_the_whole_job(client: TestClient, tmp_path: Path) -> None:
    good = make_sharp_jpeg(tmp_path / "good.jpg")
    missing = tmp_path / "missing.jpg"
    missing.write_bytes(b"placeholder")
    missing_payload = _register_payload(missing, T0)
    missing.unlink()

    client.post(
        "/api/v1/photos/register",
        json={"photos": [_register_payload(good, T0), missing_payload]},
    )

    response = client.post("/api/v1/jobs", json={"regenerate_groups": False})
    job_id = response.json()["job_id"]

    status = client.get(f"/api/v1/jobs/{job_id}").json()
    assert status["total_photos"] == 2
    assert status["processed_photos"] == 1
    assert status["failed_photos"] == 1


def test_job_results_produce_exact_duplicate_and_burst_groups(
    client: TestClient, tmp_path: Path
) -> None:
    # Two independently generated "sharp" checkerboards are byte-identical
    # (deterministic pattern) -> exact duplicate. A blurred version of the
    # same content, captured moments later, is a near-duplicate/burst.
    sharp_a = make_sharp_jpeg(tmp_path / "sharp_a.jpg")
    sharp_b = make_sharp_jpeg(tmp_path / "sharp_b.jpg")
    blurred = make_blurred_jpeg(tmp_path / "blurred.jpg")

    photos = [
        _register_payload(sharp_a, T0),
        _register_payload(sharp_b, T0 + timedelta(seconds=1)),
        _register_payload(blurred, T0 + timedelta(seconds=2)),
    ]
    client.post("/api/v1/photos/register", json={"photos": photos})

    response = client.post("/api/v1/jobs", json={"regenerate_groups": True})
    job_id = response.json()["job_id"]

    results = client.get(f"/api/v1/jobs/{job_id}/results").json()
    assert results["status"] == "completed"
    assert results["groups_regenerated"] is True
    group_types = sorted(g["group_type"] for g in results["groups"])
    assert group_types == ["burst", "exact_duplicate"]

    burst_group = next(g for g in results["groups"] if g["group_type"] == "burst")
    assert burst_group["generated_by_job_id"] == job_id
    assert len(burst_group["members"]) == 3
    ranks = sorted(m["rank"] for m in burst_group["members"])
    assert ranks == [1, 2, 3]
    keeper = next(m for m in burst_group["members"] if m["rank"] == 1)
    assert keeper["recommendation"] == "KEEPER"

    # The dedicated group-detail endpoint returns the same data.
    group_detail = client.get(f"/api/v1/groups/{burst_group['group_id']}").json()
    assert group_detail == burst_group


def test_job_without_regeneration_reports_no_groups(client: TestClient, tmp_path: Path) -> None:
    image = make_sharp_jpeg(tmp_path / "solo.jpg")
    client.post("/api/v1/photos/register", json={"photos": [_register_payload(image, T0)]})

    response = client.post("/api/v1/jobs", json={"regenerate_groups": False})
    job_id = response.json()["job_id"]

    results = client.get(f"/api/v1/jobs/{job_id}/results").json()
    assert results["groups_regenerated"] is False
    assert results["groups"] == []


def test_older_job_results_stay_populated_after_a_later_regeneration(
    client: TestClient, tmp_path: Path
) -> None:
    """Regression test: group regeneration is a full, idempotent recompute
    (docs/algorithms.md), so an older job's `analysis_job_id` tag on its
    groups gets overwritten the moment a later job regenerates groups
    again. Before the fix, `/jobs/{old_job}/results` would then report an
    empty group list even though the duplicate pair it found is still
    exactly as duplicate as before — indistinguishable from a data-loss
    bug. The fix: the endpoint returns the *current* group set for any job
    that requested regeneration, not a (nonexistent) private snapshot.
    """
    sharp_a = make_sharp_jpeg(tmp_path / "sharp_a.jpg")
    sharp_b = make_sharp_jpeg(tmp_path / "sharp_b.jpg")
    client.post(
        "/api/v1/photos/register",
        json={"photos": [_register_payload(sharp_a, T0), _register_payload(sharp_b, T0)]},
    )

    first_job_id = client.post("/api/v1/jobs", json={"regenerate_groups": True}).json()["job_id"]
    first_results = client.get(f"/api/v1/jobs/{first_job_id}/results").json()
    assert first_results["groups"], "sanity check: the first job should have found a group"

    # A second, unrelated photo triggers another full group regeneration.
    unrelated = make_blurred_jpeg(tmp_path / "unrelated.jpg")
    client.post(
        "/api/v1/photos/register",
        json={"photos": [_register_payload(unrelated, T0 + timedelta(hours=5))]},
    )
    second_job_id = client.post("/api/v1/jobs", json={"regenerate_groups": True}).json()["job_id"]
    assert second_job_id != first_job_id

    # The first job's results must still show the (still-valid) duplicate
    # group — not silently go empty just because a later job re-ran grouping.
    stale_results = client.get(f"/api/v1/jobs/{first_job_id}/results").json()
    assert stale_results["groups_regenerated"] is True
    assert len(stale_results["groups"]) == len(first_results["groups"]) > 0
    for group in stale_results["groups"]:
        assert group["generated_by_job_id"] == second_job_id

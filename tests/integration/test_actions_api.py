from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fixtures.images import make_sharp_jpeg

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _register_one_photo(client: TestClient, tmp_path: Path) -> int:
    image = make_sharp_jpeg(tmp_path / "solo.jpg")
    stat = image.stat()
    response = client.post(
        "/api/v1/photos/register",
        json={
            "photos": [
                {
                    "original_path": str(image),
                    "file_size": stat.st_size,
                    "file_mtime": stat.st_mtime,
                    "capture_time": T0.isoformat(),
                }
            ]
        },
    )
    return response.json()["registered"][0]["photo_id"]


def test_prepare_rejects_empty_items(client: TestClient) -> None:
    response = client.post(
        "/api/v1/actions/prepare", json={"action_type": "set_plugin_metadata", "items": []}
    )
    assert response.status_code == 400


def test_prepare_rejects_unknown_photo_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/actions/prepare",
        json={
            "action_type": "set_plugin_metadata",
            "items": [{"photo_id": 999999, "payload": {}}],
        },
    )
    assert response.status_code >= 400


def test_full_prepare_confirm_lifecycle(client: TestClient, tmp_path: Path) -> None:
    photo_id = _register_one_photo(client, tmp_path)

    prepare = client.post(
        "/api/v1/actions/prepare",
        json={
            "action_type": "add_to_review_collection",
            "items": [{"photo_id": photo_id, "payload": {"collection_name": "06 – Processed"}}],
        },
    )
    assert prepare.status_code == 201
    body = prepare.json()
    batch_id = body["batch_id"]
    assert body["actions"][0]["status"] == "pending"

    pending = client.get("/api/v1/actions/pending", params={"batch_id": batch_id})
    assert pending.status_code == 200
    assert len(pending.json()) == 1

    confirm = client.post(f"/api/v1/actions/{batch_id}/confirm")
    assert confirm.status_code == 200
    assert confirm.json()["actions"][0]["status"] == "confirmed"

    # Confirmed actions no longer show up as pending.
    pending_after = client.get("/api/v1/actions/pending", params={"batch_id": batch_id})
    assert pending_after.json() == []


def test_confirm_unknown_batch_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/actions/does-not-exist/confirm")
    assert response.status_code == 404


def test_confirm_twice_returns_409(client: TestClient, tmp_path: Path) -> None:
    photo_id = _register_one_photo(client, tmp_path)
    prepare = client.post(
        "/api/v1/actions/prepare",
        json={
            "action_type": "set_plugin_metadata",
            "items": [{"photo_id": photo_id, "payload": {}}],
        },
    )
    batch_id = prepare.json()["batch_id"]

    first = client.post(f"/api/v1/actions/{batch_id}/confirm")
    assert first.status_code == 200
    second = client.post(f"/api/v1/actions/{batch_id}/confirm")
    assert second.status_code == 409


def test_undo_cancels_pending_batch(client: TestClient, tmp_path: Path) -> None:
    photo_id = _register_one_photo(client, tmp_path)
    prepare = client.post(
        "/api/v1/actions/prepare",
        json={
            "action_type": "set_plugin_metadata",
            "items": [{"photo_id": photo_id, "payload": {}}],
        },
    )
    batch_id = prepare.json()["batch_id"]

    undo = client.post(f"/api/v1/actions/{batch_id}/undo")
    assert undo.status_code == 200
    assert undo.json()["actions"][0]["status"] == "undone"


def test_undo_unknown_batch_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/actions/does-not-exist/undo")
    assert response.status_code == 404


def test_summary_reflects_registered_photos(client: TestClient, tmp_path: Path) -> None:
    _register_one_photo(client, tmp_path)
    response = client.get("/api/v1/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total_photos"] == 1
    assert body["analyzed_photos"] == 0
    assert body["latest_job"] is None


def test_summary_reports_latest_job(client: TestClient, tmp_path: Path) -> None:
    _register_one_photo(client, tmp_path)
    job_id = client.post("/api/v1/jobs", json={"regenerate_groups": False}).json()["job_id"]

    response = client.get("/api/v1/summary")
    body = response.json()
    assert body["analyzed_photos"] == 1
    assert body["latest_job"]["job_id"] == job_id


def test_list_jobs_orders_most_recent_first(client: TestClient, tmp_path: Path) -> None:
    _register_one_photo(client, tmp_path)
    first_id = client.post("/api/v1/jobs", json={"regenerate_groups": False}).json()["job_id"]
    second_id = client.post("/api/v1/jobs", json={"regenerate_groups": False}).json()["job_id"]

    jobs = client.get("/api/v1/jobs").json()
    job_ids = [j["job_id"] for j in jobs]
    assert job_ids.index(second_id) < job_ids.index(first_id)


def test_list_groups_filters_by_group_type(client: TestClient, tmp_path: Path) -> None:
    image_a = make_sharp_jpeg(tmp_path / "a.jpg")
    image_b = make_sharp_jpeg(tmp_path / "b.jpg")  # byte-identical -> exact duplicate
    for image in (image_a, image_b):
        stat = image.stat()
        client.post(
            "/api/v1/photos/register",
            json={
                "photos": [
                    {
                        "original_path": str(image),
                        "file_size": stat.st_size,
                        "file_mtime": stat.st_mtime,
                        "capture_time": T0.isoformat(),
                    }
                ]
            },
        )
    client.post("/api/v1/jobs", json={"regenerate_groups": True})

    exact = client.get("/api/v1/groups", params={"group_type": "exact_duplicate"})
    assert exact.status_code == 200
    assert all(g["group_type"] == "exact_duplicate" for g in exact.json())

    near = client.get(
        "/api/v1/groups", params=[("group_type", "near_duplicate"), ("group_type", "burst")]
    )
    assert near.status_code == 200
    assert all(g["group_type"] in ("near_duplicate", "burst") for g in near.json())


def test_blurry_photos_endpoint(client: TestClient, tmp_path: Path) -> None:
    _register_one_photo(client, tmp_path)
    client.post("/api/v1/jobs", json={"regenerate_groups": False})

    response = client.get("/api/v1/photos/blurry", params={"min_confidence": 0.0})
    assert response.status_code == 200
    assert len(response.json()) == 1

"""HTTP client the MCP server uses to talk to the local lr_cleanup FastAPI
service — the same 127.0.0.1-only HTTP contract the Lightroom plugin uses
(docs/lightroom-plugin.md). The MCP server has no direct database or
Lightroom access of its own; every tool is a client of the one process
that owns the SQLite file — see docs/architecture.md's Milestone-4
component map for why.
"""

from __future__ import annotations

from typing import Any

import httpx


class BackendUnavailableError(RuntimeError):
    """The backend could not be reached at all (connection refused, timeout, DNS)."""


class BackendRequestError(RuntimeError):
    """The backend was reached but returned an HTTP error status."""


class BackendClient:
    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.Client | Any = None,
        timeout: float = 10.0,
    ) -> None:
        """Pass `client` to talk to something other than a real TCP server —
        e.g. `starlette.testclient.TestClient`, for tests that exercise the
        real FastAPI app in-process. Typed `Any` in addition to
        `httpx.Client` deliberately: this repo's dependency set carries two
        incompatible httpx major versions (`httpx` and the newer `httpx2`,
        which Starlette's own `TestClient` is built on in this environment),
        and this constructor only actually needs whatever object it's given
        to support `.request(method, url, **kwargs)`."""
        self._client = client or httpx.Client(base_url=base_url or "", timeout=timeout)
        self._owns_client = client is None

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise BackendUnavailableError(
                f"Could not reach the local AI Cleanup service at {self.base_url}{path} "
                f"— is it running? (scripts/run-server.sh): {exc}"
            ) from exc
        if response.status_code >= 400:
            raise BackendRequestError(
                f"{method} {path} returned HTTP {response.status_code}: {response.text}"
            )
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def summary(self, blur_confidence_min: float = 0.5) -> dict[str, Any]:
        return self._request(
            "GET", "/api/v1/summary", params={"blur_confidence_min": blur_confidence_min}
        )

    def list_jobs(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/jobs", params={"limit": limit, "offset": offset})

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/jobs/{job_id}")

    def blurry_photos(
        self, min_confidence: float = 0.5, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v1/photos/blurry",
            params={"min_confidence": min_confidence, "limit": limit, "offset": offset},
        )

    def list_groups(
        self, group_types: list[str] | None = None, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if group_types:
            params["group_type"] = group_types
        return self._request("GET", "/api/v1/groups", params=params)

    def get_group(self, group_id: int) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/groups/{group_id}")

    def prepare_actions(self, action_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/actions/prepare",
            json={"action_type": action_type, "items": items},
        )

    def pending_actions(
        self, batch_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if batch_id:
            params["batch_id"] = batch_id
        return self._request("GET", "/api/v1/actions/pending", params=params)

    def undo_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/actions/{batch_id}/undo")

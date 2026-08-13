"""Central, environment-driven configuration.

Every threshold and weight used by the analysis pipeline lives here so
algorithm modules never hardcode a "magic number" — see docs/algorithms.md.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, overridable via `LR_CLEANUP_*` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="LR_CLEANUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Persistence ---
    database_url: str = "sqlite:///./data/lr_cleanup.db"
    cache_dir: Path = Path("./data/render_cache")

    # --- HTTP service (Milestone 2+). Never bind beyond loopback by default. ---
    host: str = "127.0.0.1"
    port: int = 8765

    # --- Analysis pipeline ---
    analysis_version: int = 1
    batch_size: int = 200
    sharpness_working_size: int = 768
    """Long-edge size (px) images are resized to before sharpness metrics."""

    analysis_worker_processes: int = 0
    """Worker processes for parallel per-photo analysis (analyzer.py).
    `0` (default) means "use os.cpu_count()". `1` disables the process
    pool entirely and analyzes sequentially in-process — useful for
    debugging or environments where spawning subprocesses is undesirable
    (e.g. some sandboxes/CI). Each photo's analysis (image decode +
    hashing + metric computation) is CPU-bound and independent of every
    other photo, so this parallelizes well across cores for large
    batches; only the DB writes stay serialized on the main process.

    This value only applies at all above ~35 photos needing analysis
    (see analyzer.py's _MIN_PHOTOS_FOR_PARALLEL_ANALYSIS) — below that,
    spawning worker processes and re-importing their imaging libraries
    costs more than the sequential work itself. Measured against real
    registered photos with repeated trials, not assumed; see analyzer.py's
    module-level comments for the investigation this was based on
    (an earlier, lower threshold turned out to be based on a flawed
    synthetic benchmark and made real jobs slower, not faster)."""

    high_confidence_blur_threshold: float = 0.55
    """blur_confidence at/above which a photo counts as a
    high_confidence_blur_candidate (docs/algorithms.md §3). Also gates
    grouping: a photo this blurry is excluded from near-duplicate/burst
    comparison entirely (docs/algorithms.md §2) — its "reason for cleanup"
    is out-of-focus, not "worse than its sharper duplicate," so it never
    gets a chance to be mislabeled as both.

    Lowered from 0.75 after a real-world false negative showed genuinely
    out-of-focus real photos land around 0.5-0.6 blur_confidence, not
    0.9+ like the heavily-blurred synthetic images the original default
    was tuned against — see sharpness.py's module docstring for the
    full investigation. 0.55 sits well above the 0.02-0.21 range six
    known-sharp real photos scored in that investigation."""

    # --- Similarity / grouping thresholds (docs/algorithms.md §2) ---
    burst_window_seconds: float = 10.0
    phash_max_distance: int = 8
    aspect_ratio_tolerance: float = 0.05
    """Max relative aspect-ratio difference allowed within a group (5%)."""

    # --- Keeper ranking weights (docs/algorithms.md §5). Must sum to 1.0. ---
    weight_sharpness: float = 0.55
    weight_exposure: float = 0.25
    weight_technical: float = 0.10
    weight_existing_preference: float = 0.10

    # --- Exposure thresholds ---
    highlight_clip_threshold: float = 0.98
    """Normalized (0..1) pixel value above which a pixel counts as clipped."""
    shadow_clip_threshold: float = 0.02
    """Normalized (0..1) pixel value below which a pixel counts as clipped."""

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_weights_sum_to_one(self) -> Settings:
        total = (
            self.weight_sharpness
            + self.weight_exposure
            + self.weight_technical
            + self.weight_existing_preference
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Keeper ranking weights must sum to 1.0, got {total}")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton, loading it on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Clear the cached singleton. Intended for tests that mutate env vars."""
    global _settings
    _settings = None

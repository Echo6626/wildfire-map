from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_title: str = "산불 현장 종합 안내 MVP"
    host: str = "0.0.0.0"
    port: int = 8000

    base_dir: Path = Path(__file__).resolve().parent
    data_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "data")
    upload_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "data" / "uploads")
    static_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "static")

    default_center_lat: float = 37.45
    default_center_lng: float = 128.605
    default_zoom: int = 13
    tile_url: str = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "&copy; OpenStreetMap contributors"

    remote_layer_url_roads: str | None = None
    remote_layer_url_shelters: str | None = None
    remote_layer_url_water: str | None = None
    remote_layer_url_staging: str | None = None
    remote_layer_url_fireline: str | None = None
    remote_layer_url_closures: str | None = None

    landslide_wms_url: str | None = None
    landslide_wms_layers: str | None = None
    landslide_wms_format: str = "image/png"
    landslide_wms_transparent: bool = True

    vworld_api_key: str | None = None
    data_go_kr_service_key: str | None = None
    safetydata_api_key: str | None = None

    wildfire_caution_buffer_m: float = 250.0
    wildfire_no_go_buffer_m: float = 80.0
    closure_block_buffer_m: float = 15.0
    night_speed_penalty_multiplier: float = 1.4
    trail_night_fixed_penalty_min: float = 8.0
    hazard_penalty_divisor_m: float = 60.0

    off_network_speed_kph: float = 4.0
    max_snap_distance_m: float = 1200.0
    route_overlap_penalty_multiplier: float = 1.3
    route_overlap_fixed_penalty_min: float = 2.0
    target_pool_size: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.upload_dir.mkdir(parents=True, exist_ok=True)

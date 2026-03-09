from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    start: LatLng
    goal_layer: str | None = Field(
        default=None,
        description="shelters, water, staging 중 하나. goal_id나 goal_point가 없을 때 사용",
    )
    goal_id: str | None = Field(default=None, description="목표 Feature ID")
    goal_point: LatLng | None = Field(default=None, description="직접 지정한 목표 좌표")
    night_mode: bool = True
    max_candidates: int = Field(default=3, ge=1, le=5)
    blocked_segment_ids: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    ok: bool
    layer_name: str
    feature_count: int
    message: str


class LayerMeta(BaseModel):
    name: str
    title: str
    source: str
    feature_count: int
    uploadable: bool = False
    updated_at: str | None = None
    geometry_types: list[str] = Field(default_factory=list)


class RouteCandidate(BaseModel):
    id: str
    target: dict[str, Any]
    distance_m: float
    network_distance_m: float
    connector_distance_m: float
    eta_min: float
    network_eta_min: float
    connector_eta_min: float
    hazard_overlap_m: float
    min_clearance_m: float
    score: int
    severity: str
    reason: str
    warnings: list[str]
    geometry: dict[str, Any]
    segments: list[dict[str, Any]]

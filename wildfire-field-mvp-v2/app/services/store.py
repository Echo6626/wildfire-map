from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.geo import ensure_feature_collection, feature_bounds_wgs84, geometry_types, load_json, save_json, utc_iso_from_timestamp


LAYER_REGISTRY: dict[str, dict[str, Any]] = {
    "roads": {"title": "도로/임도", "file": "roads.geojson", "uploadable": False},
    "shelters": {"title": "대피소", "file": "shelters.geojson", "uploadable": False},
    "water": {"title": "수자원", "file": "water.geojson", "uploadable": False},
    "staging": {"title": "대기/집결지", "file": "staging.geojson", "uploadable": False},
    "fireline": {"title": "화선", "file": "fireline.geojson", "uploadable": True, "upload_path": "uploads/fireline.geojson"},
    "closures": {"title": "통제선", "file": "closures.geojson", "uploadable": True, "upload_path": "uploads/closures.geojson"},
}

REMOTE_URLS = {
    "roads": settings.remote_layer_url_roads,
    "shelters": settings.remote_layer_url_shelters,
    "water": settings.remote_layer_url_water,
    "staging": settings.remote_layer_url_staging,
    "fireline": settings.remote_layer_url_fireline,
    "closures": settings.remote_layer_url_closures,
}


class LayerStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.upload_dir = settings.upload_dir

    def list_layers(self) -> list[dict[str, Any]]:
        return [self.layer_metadata(name) for name in LAYER_REGISTRY]

    def source_name(self, layer_name: str) -> str:
        uploaded_path = self.uploaded_path(layer_name)
        if uploaded_path and uploaded_path.exists():
            return "uploaded"
        if REMOTE_URLS.get(layer_name):
            return "remote"
        return "sample"

    def local_path(self, layer_name: str) -> Path:
        meta = LAYER_REGISTRY[layer_name]
        return self.data_dir / meta["file"]

    def uploaded_path(self, layer_name: str) -> Path | None:
        meta = LAYER_REGISTRY[layer_name]
        upload_path = meta.get("upload_path")
        if not upload_path:
            return None
        return self.data_dir / upload_path

    def active_file_path(self, layer_name: str) -> Path | None:
        uploaded_path = self.uploaded_path(layer_name)
        if uploaded_path and uploaded_path.exists():
            return uploaded_path
        local_path = self.local_path(layer_name)
        if local_path.exists():
            return local_path
        return None

    def _fetch_remote(self, layer_name: str) -> dict[str, Any] | None:
        url = REMOTE_URLS.get(layer_name)
        if not url:
            return None
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                return ensure_feature_collection(payload)
        except Exception:
            return None

    def get_layer(self, layer_name: str) -> dict[str, Any]:
        uploaded_path = self.uploaded_path(layer_name)
        if uploaded_path and uploaded_path.exists():
            return ensure_feature_collection(load_json(uploaded_path))

        remote = self._fetch_remote(layer_name)
        if remote:
            return remote

        return ensure_feature_collection(load_json(self.local_path(layer_name)))

    def layer_metadata(self, layer_name: str) -> dict[str, Any]:
        meta = LAYER_REGISTRY[layer_name]
        payload = self.get_layer(layer_name)
        active_path = self.active_file_path(layer_name)
        updated_at = utc_iso_from_timestamp(active_path.stat().st_mtime) if active_path and active_path.exists() else None
        return {
            "name": layer_name,
            "title": meta["title"],
            "source": self.source_name(layer_name),
            "feature_count": len(payload.get("features", [])),
            "uploadable": bool(meta.get("uploadable", False)),
            "updated_at": updated_at,
            "geometry_types": geometry_types(payload.get("features", [])),
            "bounds": feature_bounds_wgs84(payload.get("features", [])),
        }

    def put_uploaded_layer(self, layer_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        upload_path = self.uploaded_path(layer_name)
        if not upload_path:
            raise ValueError(f"{layer_name} 레이어는 업로드 대상이 아닙니다.")
        feature_collection = ensure_feature_collection(payload)
        save_json(upload_path, feature_collection)
        return feature_collection

    def delete_uploaded_layer(self, layer_name: str) -> bool:
        upload_path = self.uploaded_path(layer_name)
        if not upload_path or not upload_path.exists():
            return False
        upload_path.unlink()
        return True

    def reset_demo(self) -> None:
        for layer_name in ("fireline", "closures"):
            self.delete_uploaded_layer(layer_name)

    def get_feature_by_id(self, layer_name: str, feature_id: str) -> dict[str, Any] | None:
        payload = self.get_layer(layer_name)
        for feature in payload.get("features", []):
            if str(feature.get("properties", {}).get("id")) == str(feature_id):
                return feature
        return None

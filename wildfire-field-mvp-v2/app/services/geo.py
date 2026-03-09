from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pyproj import Transformer
from shapely.geometry import GeometryCollection, Point, box, shape, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

_TO_METERS = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True).transform
_TO_WGS84 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True).transform


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_feature_collection(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, list):
        return {"type": "FeatureCollection", "features": payload}
    if payload.get("type") == "FeatureCollection":
        return payload
    if payload.get("type") == "Feature":
        return {"type": "FeatureCollection", "features": [payload]}
    if "coordinates" in payload:
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": payload}]}
    raise ValueError("GeoJSON FeatureCollection/Feature/Geometry 형식이 아닙니다.")


def geometry_from_feature(feature: dict[str, Any]) -> BaseGeometry:
    return shape(feature["geometry"])


def feature_collection(features: Iterable[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    payload = {"type": "FeatureCollection", "features": list(features)}
    payload.update(kwargs)
    return payload


def to_meters(geom: BaseGeometry) -> BaseGeometry:
    return transform(_TO_METERS, geom)


def to_wgs84(geom: BaseGeometry) -> BaseGeometry:
    return transform(_TO_WGS84, geom)


def buffer_in_meters(geom: BaseGeometry, meters: float) -> BaseGeometry:
    return to_wgs84(to_meters(geom).buffer(meters))


def distance_meters(a: BaseGeometry, b: BaseGeometry) -> float:
    return float(to_meters(a).distance(to_meters(b)))


def length_meters(geom: BaseGeometry) -> float:
    return float(to_meters(geom).length)


def unary_union_in_meters(features: Iterable[dict[str, Any]]) -> BaseGeometry:
    geometries = [to_meters(geometry_from_feature(feature)) for feature in features if feature.get("geometry")]
    if not geometries:
        return GeometryCollection()
    return unary_union(geometries)


def point_feature(lng: float, lat: float, **properties: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(Point(lng, lat)),
    }


def coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def utc_iso_from_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def feature_bounds_wgs84(features: Iterable[dict[str, Any]]) -> list[float] | None:
    geometries = [geometry_from_feature(feature) for feature in features if feature.get("geometry")]
    if not geometries:
        return None
    minx, miny, maxx, maxy = unary_union(geometries).bounds
    return [round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)]


def geometry_types(features: Iterable[dict[str, Any]]) -> list[str]:
    types = sorted({geometry_from_feature(feature).geom_type for feature in features if feature.get("geometry")})
    return types

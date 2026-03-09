from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from app.services.geo import ensure_feature_collection
SUPPORTED_UPLOAD_SUFFIXES = {".json", ".geojson", ".kml", ".gpx"}
def supported_upload_formats() -> list[str]:
    return sorted(SUPPORTED_UPLOAD_SUFFIXES)
def load_uploaded_feature_collection(filename: str | None, payload_bytes: bytes) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
        raise ValueError(f"지원하지 않는 형식입니다. 허용 형식: {allowed}")
    if suffix in {".json", ".geojson"}:
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError("GeoJSON 파싱에 실패했습니다.") from exc
        return ensure_feature_collection(payload)
    if suffix == ".kml":
        return _parse_kml(payload_bytes)
    if suffix == ".gpx":
        return _parse_gpx(payload_bytes)
    raise ValueError("지원하지 않는 형식입니다.")
def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag
def _iter_descendants(node: ET.Element, name: str):
    for child in node.iter():
        if _strip_ns(child.tag) == name:
            yield child
def _direct_child_text(node: ET.Element, name: str) -> str | None:
    for child in node:
        if _strip_ns(child.tag) == name and child.text:
            return child.text.strip()
    return None
def _parse_coordinate_triplets(text: str | None) -> list[tuple[float, float]]:
    if not text:
        return []
    coords: list[tuple[float, float]] = []
    for chunk in text.replace("\n", " ").replace("\t", " ").split():
        parts = [item for item in chunk.split(",") if item != ""]
        if len(parts) < 2:
            continue
        try:
            lng = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append((lng, lat))
    return coords
def _kml_geometry_features(placemark: ET.Element, properties: dict[str, Any]) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for point_el in _iter_descendants(placemark, "Point"):
        coords = _parse_coordinate_triplets(next((item.text for item in _iter_descendants(point_el, "coordinates") if item.text), None))
        if not coords:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(properties),
                "geometry": {"type": "Point", "coordinates": [coords[0][0], coords[0][1]]},
            }
        )
    for line_el in _iter_descendants(placemark, "LineString"):
        coords = _parse_coordinate_triplets(next((item.text for item in _iter_descendants(line_el, "coordinates") if item.text), None))
        if len(coords) < 2:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(properties),
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    for poly_el in _iter_descendants(placemark, "Polygon"):
        outer: list[tuple[float, float]] = []
        holes: list[list[tuple[float, float]]] = []
        for child in poly_el:
            tag = _strip_ns(child.tag)
            if tag == "outerBoundaryIs":
                ring_text = next((item.text for item in _iter_descendants(child, "coordinates") if item.text), None)
                outer = _parse_coordinate_triplets(ring_text)
            elif tag == "innerBoundaryIs":
                ring_text = next((item.text for item in _iter_descendants(child, "coordinates") if item.text), None)
                ring = _parse_coordinate_triplets(ring_text)
                if ring:
                    holes.append(ring)
        if len(outer) < 4:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(properties),
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [outer, *holes],
                },
            }
        )
    return features
def _parse_kml(payload_bytes: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError("KML 파싱에 실패했습니다.") from exc
    features: list[dict[str, Any]] = []
    placemark_index = 0
    for placemark in _iter_descendants(root, "Placemark"):
        placemark_index += 1
        properties: dict[str, Any] = {
            "id": f"kml-{placemark_index}",
            "name": _direct_child_text(placemark, "name") or f"KML 객체 {placemark_index}",
            "description": _direct_child_text(placemark, "description"),
            "source_format": "kml",
        }
        placemark_features = _kml_geometry_features(placemark, properties)
        for index, feature in enumerate(placemark_features, start=1):
            feature["properties"] = dict(feature.get("properties", {}), id=f"kml-{placemark_index}-{index}")
            features.append(feature)
    if not features:
        raise ValueError("KML에서 사용할 수 있는 Point/LineString/Polygon을 찾지 못했습니다.")
    return ensure_feature_collection({"type": "FeatureCollection", "features": features})
def _parse_gpx(payload_bytes: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError("GPX 파싱에 실패했습니다.") from exc
    features: list[dict[str, Any]] = []
    item_index = 0
    for wpt in _iter_descendants(root, "wpt"):
        try:
            lat = float(wpt.attrib["lat"])
            lng = float(wpt.attrib["lon"])
        except (KeyError, ValueError):
            continue
        item_index += 1
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"gpx-wpt-{item_index}",
                    "name": _direct_child_text(wpt, "name") or f"GPX 지점 {item_index}",
                    "description": _direct_child_text(wpt, "desc"),
                    "source_format": "gpx",
                },
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
            }
        )
    for rte in _iter_descendants(root, "rte"):
        coords: list[tuple[float, float]] = []
        for rtept in _iter_descendants(rte, "rtept"):
            try:
                lat = float(rtept.attrib["lat"])
                lng = float(rtept.attrib["lon"])
            except (KeyError, ValueError):
                continue
            coords.append((lng, lat))
        if len(coords) < 2:
            continue
        item_index += 1
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"gpx-rte-{item_index}",
                    "name": _direct_child_text(rte, "name") or f"GPX 경로 {item_index}",
                    "source_format": "gpx",
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    for trk in _iter_descendants(root, "trk"):
        track_name = _direct_child_text(trk, "name") or "GPX 트랙"
        segment_index = 0
        for trkseg in _iter_descendants(trk, "trkseg"):
            coords: list[tuple[float, float]] = []
            for trkpt in _iter_descendants(trkseg, "trkpt"):
                try:
                    lat = float(trkpt.attrib["lat"])
                    lng = float(trkpt.attrib["lon"])
                except (KeyError, ValueError):
                    continue
                coords.append((lng, lat))
            if len(coords) < 2:
                continue
            segment_index += 1
            item_index += 1
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "id": f"gpx-trk-{item_index}",
                        "name": f"{track_name} 구간 {segment_index}",
                        "source_format": "gpx",
                    },
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
    if not features:
        raise ValueError("GPX에서 사용할 수 있는 waypoint/route/track을 찾지 못했습니다.")
    return ensure_feature_collection({"type": "FeatureCollection", "features": features})

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import Point, shape

from app.config import settings
from app.models import RouteRequest, UploadResponse
from app.services.catalog import official_source_catalog
from app.services.geo import distance_meters
from app.services.importers import load_uploaded_feature_collection, supported_upload_formats
from app.services.route_engine import RouteEngine
from app.services.store import LayerStore


app = FastAPI(title=settings.app_title)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

layer_store = LayerStore(settings.data_dir)
route_engine = RouteEngine(layer_store)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "app": settings.app_title}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "appTitle": settings.app_title,
        "defaultCenter": {"lat": settings.default_center_lat, "lng": settings.default_center_lng},
        "defaultZoom": settings.default_zoom,
        "tile": {"url": settings.tile_url, "attribution": settings.tile_attribution},
        "acceptedUploadFormats": supported_upload_formats(),
        "routing": {
            "maxSnapDistanceM": settings.max_snap_distance_m,
            "targetPoolSize": settings.target_pool_size,
        },
        "wmsLayers": [
            {
                "id": "landslide-risk",
                "name": "산사태 위험도 WMS",
                "url": settings.landslide_wms_url,
                "layers": settings.landslide_wms_layers,
                "format": settings.landslide_wms_format,
                "transparent": settings.landslide_wms_transparent,
            }
        ]
        if settings.landslide_wms_url and settings.landslide_wms_layers
        else [],
        "sources": official_source_catalog(),
        "layers": layer_store.list_layers(),
    }


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "layers": layer_store.list_layers(),
    }


@app.get("/api/layers")
def list_layers() -> dict[str, Any]:
    return {"layers": layer_store.list_layers()}


@app.get("/api/layers/{layer_name}")
def get_layer(layer_name: str) -> JSONResponse:
    try:
        payload = layer_store.get_layer(layer_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="알 수 없는 레이어입니다.") from exc
    return JSONResponse(content=payload)


@app.get("/api/nearby")
def nearby(lat: float, lng: float) -> dict[str, Any]:
    origin = Point(lng, lat)
    response: dict[str, Any] = {"origin": {"lat": lat, "lng": lng}, "items": {}}
    for layer_name in ("shelters", "water", "staging"):
        items = []
        for feature in layer_store.get_layer(layer_name).get("features", []):
            point = shape(feature["geometry"])
            if point.geom_type != "Point":
                continue
            items.append(
                {
                    "id": feature.get("properties", {}).get("id"),
                    "name": feature.get("properties", {}).get("name"),
                    "kind": feature.get("properties", {}).get("kind"),
                    "distance_m": round(distance_meters(origin, point), 1),
                    "address": feature.get("properties", {}).get("address"),
                }
            )
        items.sort(key=lambda item: item["distance_m"])
        response["items"][layer_name] = items[:3]
    return response


@app.post("/api/route")
def route(request: RouteRequest) -> dict[str, Any]:
    try:
        return route_engine.route(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/incidents/{layer_name}", response_model=UploadResponse)
async def upload_incident_layer(layer_name: str, file: UploadFile = File(...)) -> UploadResponse:
    if layer_name not in {"fireline", "closures"}:
        raise HTTPException(status_code=400, detail="업로드 가능한 레이어는 fireline, closures 뿐입니다.")
    payload_bytes = await file.read()
    try:
        payload = load_uploaded_feature_collection(file.filename, payload_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved = layer_store.put_uploaded_layer(layer_name, payload)
    return UploadResponse(
        ok=True,
        layer_name=layer_name,
        feature_count=len(saved.get("features", [])),
        message=f"{layer_name} 업로드 완료",
    )


@app.post("/api/reset-demo")
def reset_demo() -> dict[str, Any]:
    layer_store.reset_demo()
    return {"ok": True, "message": "샘플 화선/통제선으로 복구했습니다."}


app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

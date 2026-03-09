from __future__ import annotations

from typing import Any

from app.config import settings


def official_source_catalog() -> list[dict[str, Any]]:
    wms_enabled = bool(settings.landslide_wms_url and settings.landslide_wms_layers)
    return [
        {
            "id": "vworld",
            "name": "브이월드 WMS/WFS",
            "purpose": "배경지도·행정/공간 레이어 연계",
            "status": "ready" if settings.vworld_api_key else "pending_key",
        },
        {
            "id": "wildfire-risk-forecast",
            "name": "국립산림과학원 산불위험예보정보",
            "purpose": "72시간 산불위험 예보",
            "status": "cataloged",
        },
        {
            "id": "civil-defense-shelter",
            "name": "행정안전부 민방위대피시설 조회서비스",
            "purpose": "전국 대피소",
            "status": "cataloged",
        },
        {
            "id": "temporary-housing",
            "name": "행정안전부 이재민 임시주거시설",
            "purpose": "장기 대피·주거 전환",
            "status": "cataloged",
        },
        {
            "id": "emergency-water-facility",
            "name": "행정안전부 비상급수시설",
            "purpose": "음용·생활용수 비상 확보",
            "status": "cataloged",
        },
        {
            "id": "landslide-concern",
            "name": "행정안전부 산사태우려지역 / 산사태위험지도 WMS",
            "purpose": "2차 재난 중첩 판단",
            "status": "ready" if wms_enabled else "pending_config",
        },
        {
            "id": "fire-water",
            "name": "전국소방용수시설표준데이터",
            "purpose": "소화전·급수탑·저수조",
            "status": "cataloged",
        },
        {
            "id": "reservoir",
            "name": "한국농어촌공사 저수지 정보",
            "purpose": "저수지 위치·수위",
            "status": "cataloged",
        },
        {
            "id": "well",
            "name": "한국수자원공사 지하수 관정정보",
            "purpose": "관정/우물 후보",
            "status": "cataloged",
        },
        {
            "id": "forest-trails",
            "name": "산림청 등산로 JSON/GPX/SHP",
            "purpose": "산길·보행 접근성 참고",
            "status": "cataloged",
        },
        {
            "id": "fireline",
            "name": "화선 현황",
            "purpose": "현재 MVP는 상황실 GeoJSON 업로드",
            "status": "manual_upload",
        },
    ]

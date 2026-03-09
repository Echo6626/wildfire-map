from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import networkx as nx
from shapely.geometry import GeometryCollection, LineString, Point, shape, mapping
from shapely.geometry.base import BaseGeometry

from app.config import settings
from app.models import RouteRequest
from app.services.geo import coerce_bool, distance_meters, to_meters, unary_union_in_meters
from app.services.store import LayerStore


@dataclass
class NetworkAnchor:
    point: Point
    node: tuple[float, float]
    snap_distance_m: float
    snap_eta_min: float
    label: str


@dataclass
class ResolvedTarget:
    layer_name: str
    feature_id: str
    feature: dict[str, Any]
    anchor: NetworkAnchor
    search_cost: float | None = None
    target_rank: int = 1


@dataclass
class HazardContext:
    fire_geom: BaseGeometry
    caution_geom: BaseGeometry
    no_go_geom: BaseGeometry
    closure_block_geom: BaseGeometry


class RouteEngine:
    def __init__(self, layer_store: LayerStore):
        self.layer_store = layer_store

    def _iter_line_parts(self, geometry: BaseGeometry) -> Iterable[LineString]:
        if geometry.geom_type == "LineString":
            yield geometry
        elif geometry.geom_type == "MultiLineString":
            for part in geometry.geoms:
                if part.geom_type == "LineString":
                    yield part

    def build_base_graph(self) -> nx.Graph:
        payload = self.layer_store.get_layer("roads")
        graph = nx.Graph()
        for feature in payload.get("features", []):
            geometry = shape(feature["geometry"])
            properties = feature.get("properties", {})
            source_id = str(properties.get("id") or f"road-{graph.number_of_edges() + 1}")
            source_name = properties.get("name", "unnamed")
            speed_kph = max(float(properties.get("speed_kph", 20) or 20), 3.0)
            night_ok = coerce_bool(properties.get("night_ok", True), default=True)
            road_class = properties.get("road_class", "local")
            segment_type = properties.get("segment_type", "road")

            segment_counter = 0
            for part in self._iter_line_parts(geometry):
                coords = list(part.coords)
                if len(coords) < 2:
                    continue
                for left, right in zip(coords[:-1], coords[1:]):
                    segment_counter += 1
                    u = (float(left[0]), float(left[1]))
                    v = (float(right[0]), float(right[1]))
                    segment_line = LineString([u, v])
                    segment_m = to_meters(segment_line)
                    segment_id = source_id if len(coords) == 2 and geometry.geom_type == "LineString" else f"{source_id}:{segment_counter}"
                    edge_payload = {
                        "id": segment_id,
                        "source_id": source_id,
                        "name": source_name,
                        "road_class": road_class,
                        "segment_type": segment_type,
                        "night_ok": night_ok,
                        "speed_kph": speed_kph,
                        "geom_ll": segment_line,
                        "geom_m": segment_m,
                        "length_m": float(segment_m.length),
                    }
                    if graph.has_edge(u, v) and graph[u][v].get("length_m", 10**18) <= edge_payload["length_m"]:
                        continue
                    graph.add_edge(u, v, **edge_payload)
        return graph

    def _point_to_node_distance(self, point: Point, node: tuple[float, float]) -> float:
        return distance_meters(point, Point(node[0], node[1]))

    def snap_to_node(self, graph: nx.Graph, point: Point) -> tuple[float, float]:
        if not graph.nodes:
            raise ValueError("경로 그래프가 비어 있습니다.")
        return min(graph.nodes, key=lambda node: self._point_to_node_distance(point, node))

    def _anchor_point(self, graph: nx.Graph, lng: float, lat: float, label: str) -> NetworkAnchor:
        point = Point(lng, lat)
        node = self.snap_to_node(graph, point)
        snap_distance_m = self._point_to_node_distance(point, node)
        if snap_distance_m > settings.max_snap_distance_m:
            raise ValueError(f"{label}에서 도로/임도 네트워크까지 너무 멉니다. ({round(snap_distance_m)}m)")
        snap_eta_min = snap_distance_m / (max(settings.off_network_speed_kph, 1.0) * 1000 / 60)
        return NetworkAnchor(
            point=point,
            node=node,
            snap_distance_m=float(snap_distance_m),
            snap_eta_min=float(snap_eta_min),
            label=label,
        )

    def _hazard_context(self) -> HazardContext:
        fireline_fc = self.layer_store.get_layer("fireline")
        closures_fc = self.layer_store.get_layer("closures")

        fire_geom = unary_union_in_meters(fireline_fc.get("features", []))
        closure_geom = unary_union_in_meters(closures_fc.get("features", []))

        no_go = fire_geom.buffer(settings.wildfire_no_go_buffer_m) if not fire_geom.is_empty else GeometryCollection()
        caution = fire_geom.buffer(settings.wildfire_caution_buffer_m) if not fire_geom.is_empty else GeometryCollection()
        closure_block = closure_geom.buffer(settings.closure_block_buffer_m) if not closure_geom.is_empty else GeometryCollection()

        return HazardContext(
            fire_geom=fire_geom,
            caution_geom=caution,
            no_go_geom=no_go,
            closure_block_geom=closure_block,
        )

    def _search_graph(
        self,
        night_mode: bool,
        blocked_segment_ids: list[str] | None = None,
    ) -> tuple[nx.Graph, HazardContext]:
        base_graph = self.build_base_graph()
        hazards = self._hazard_context()

        blocked_ids = {str(item) for item in (blocked_segment_ids or [])}
        graph = nx.Graph()

        for u, v, data in base_graph.edges(data=True):
            if data["id"] in blocked_ids or data.get("source_id") in blocked_ids:
                continue

            edge_geom = data["geom_m"]
            if not hazards.closure_block_geom.is_empty and edge_geom.intersects(hazards.closure_block_geom):
                continue
            if not hazards.no_go_geom.is_empty and edge_geom.intersects(hazards.no_go_geom):
                continue

            speed = max(float(data["speed_kph"]), 3.0)
            travel_min = data["length_m"] / (speed * 1000 / 60)

            if night_mode and not data["night_ok"]:
                travel_min *= settings.night_speed_penalty_multiplier
            if night_mode and data["segment_type"] == "trail":
                travel_min += settings.trail_night_fixed_penalty_min

            hazard_overlap = edge_geom.intersection(hazards.caution_geom).length if not hazards.caution_geom.is_empty else 0.0
            min_clearance = edge_geom.distance(hazards.fire_geom) if not hazards.fire_geom.is_empty else 999999.0
            search_cost = travel_min + (hazard_overlap / max(settings.hazard_penalty_divisor_m, 1.0))

            graph.add_edge(
                u,
                v,
                **data,
                base_travel_min=round(float(travel_min), 3),
                hazard_overlap_m=round(float(hazard_overlap), 3),
                min_clearance_m=round(float(min_clearance), 3),
                search_cost=round(float(search_cost), 3),
            )

        if graph.number_of_edges() == 0:
            raise ValueError("통행 가능한 도로/임도가 없습니다. 화선 또는 통제선 범위를 확인하세요.")

        return graph, hazards

    def _resolve_point_feature(self, lng: float, lat: float) -> dict[str, Any]:
        return {
            "type": "Feature",
            "properties": {"id": "manual-target", "name": "수동 지정 목표", "kind": "manual"},
            "geometry": mapping(Point(lng, lat)),
        }

    def _feature_point(self, feature: dict[str, Any]) -> Point:
        point = shape(feature["geometry"])
        if point.geom_type != "Point":
            raise ValueError("목표 레이어는 Point여야 합니다.")
        return point

    def _candidate_targets(
        self,
        graph: nx.Graph,
        request: RouteRequest,
        start_anchor: NetworkAnchor,
    ) -> list[ResolvedTarget]:
        if request.goal_point:
            feature = self._resolve_point_feature(request.goal_point.lng, request.goal_point.lat)
            anchor = self._anchor_point(graph, request.goal_point.lng, request.goal_point.lat, "목표 지점")
            return [ResolvedTarget(layer_name="manual", feature_id="manual-target", feature=feature, anchor=anchor)]

        if request.goal_layer and request.goal_id:
            feature = self.layer_store.get_feature_by_id(request.goal_layer, request.goal_id)
            if not feature:
                raise ValueError("선택한 목표를 찾을 수 없습니다.")
            point = self._feature_point(feature)
            anchor = self._anchor_point(graph, point.x, point.y, "목표 지점")
            return [
                ResolvedTarget(
                    layer_name=request.goal_layer,
                    feature_id=str(feature.get("properties", {}).get("id")),
                    feature=feature,
                    anchor=anchor,
                )
            ]

        if not request.goal_layer:
            raise ValueError("goal_layer 또는 goal_point가 필요합니다.")

        payload = self.layer_store.get_layer(request.goal_layer)
        candidates: list[ResolvedTarget] = []
        for feature in payload.get("features", []):
            try:
                point = self._feature_point(feature)
                anchor = self._anchor_point(graph, point.x, point.y, "목표 지점")
                network_cost = nx.shortest_path_length(graph, start_anchor.node, anchor.node, weight="search_cost")
            except (ValueError, nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            total_cost = float(network_cost) + start_anchor.snap_eta_min + anchor.snap_eta_min
            candidates.append(
                ResolvedTarget(
                    layer_name=request.goal_layer,
                    feature_id=str(feature.get("properties", {}).get("id")),
                    feature=feature,
                    anchor=anchor,
                    search_cost=total_cost,
                )
            )

        if not candidates:
            raise ValueError("도달 가능한 목표가 없습니다.")

        candidates.sort(key=lambda item: item.search_cost or 0.0)
        for rank, item in enumerate(candidates, start=1):
            item.target_rank = rank
        return candidates[: max(1, settings.target_pool_size)]

    def _segment_summaries(self, graph: nx.Graph, path: list[tuple[float, float]]) -> list[dict[str, Any]]:
        summaries = []
        for left, right in zip(path[:-1], path[1:]):
            edge = graph[left][right]
            summaries.append(
                {
                    "id": edge.get("source_id") or edge["id"],
                    "segment_id": edge["id"],
                    "name": edge["name"],
                    "road_class": edge["road_class"],
                    "segment_type": edge["segment_type"],
                    "distance_m": round(float(edge["length_m"]), 1),
                    "travel_min": round(float(edge["base_travel_min"]), 1),
                    "hazard_overlap_m": round(float(edge["hazard_overlap_m"]), 1),
                    "night_ok": bool(edge["night_ok"]),
                }
            )
        return summaries

    def _diverse_paths(
        self,
        graph: nx.Graph,
        start_node: tuple[float, float],
        goal_node: tuple[float, float],
        max_candidates: int,
    ) -> list[list[tuple[float, float]]]:
        working_graph = graph.copy()
        paths: list[list[tuple[float, float]]] = []
        seen: set[tuple[tuple[float, float], ...]] = set()

        attempts = 0
        while len(paths) < max_candidates and attempts < max_candidates * 6:
            attempts += 1
            try:
                path = nx.shortest_path(working_graph, start_node, goal_node, weight="search_cost")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                break
            key = tuple(path)
            if key not in seen:
                paths.append(path)
                seen.add(key)
            for left, right in zip(path[:-1], path[1:]):
                working_graph[left][right]["search_cost"] = (
                    float(working_graph[left][right]["search_cost"]) * settings.route_overlap_penalty_multiplier
                    + settings.route_overlap_fixed_penalty_min
                )
        return paths

    def _severity(self, hazard_overlap_m: float, min_clearance_m: float, warnings: list[str]) -> str:
        if hazard_overlap_m > 0 or min_clearance_m < settings.wildfire_no_go_buffer_m:
            return "danger"
        if min_clearance_m < settings.wildfire_caution_buffer_m or warnings:
            return "caution"
        return "ok"

    def _reason(self, target: ResolvedTarget, same_target_alt: bool) -> str:
        if target.target_rank == 1 and not same_target_alt:
            return "가장 빠른 목표로 향하는 권장 경로"
        if same_target_alt:
            return "같은 목표로 우회한 대안 경로"
        return f"대체 목표 {target.target_rank}순위 경로"

    def _candidate_from_path(
        self,
        graph: nx.Graph,
        hazards: HazardContext,
        path: list[tuple[float, float]],
        target: ResolvedTarget,
        start_anchor: NetworkAnchor,
        route_id: str,
        same_target_alt: bool,
    ) -> dict[str, Any]:
        segments = self._segment_summaries(graph, path)
        coords = [start_anchor.point.coords[0], *path, target.anchor.point.coords[0]]
        deduped_coords: list[tuple[float, float]] = []
        for coord in coords:
            current = (float(coord[0]), float(coord[1]))
            if not deduped_coords or deduped_coords[-1] != current:
                deduped_coords.append(current)
        if len(deduped_coords) == 1:
            deduped_coords.append(deduped_coords[0])
        line = LineString(deduped_coords)

        network_distance_m = sum(item["distance_m"] for item in segments)
        network_eta_min = sum(item["travel_min"] for item in segments)
        network_hazard_overlap_m = sum(item["hazard_overlap_m"] for item in segments)
        network_min_clearance_m = min(
            (graph[left][right]["min_clearance_m"] for left, right in zip(path[:-1], path[1:])),
            default=999999.0,
        )

        connector_distance_m = start_anchor.snap_distance_m + target.anchor.snap_distance_m
        connector_eta_min = start_anchor.snap_eta_min + target.anchor.snap_eta_min
        connector_hazard_overlap_m = 0.0
        connector_min_clearance_m = 999999.0
        connector_geoms: list[BaseGeometry] = []
        if start_anchor.snap_distance_m > 0:
            connector_geoms.append(to_meters(LineString([start_anchor.point.coords[0], start_anchor.node])))
        if target.anchor.snap_distance_m > 0:
            connector_geoms.append(to_meters(LineString([target.anchor.node, target.anchor.point.coords[0]])))
        if connector_geoms and not hazards.caution_geom.is_empty:
            connector_hazard_overlap_m = sum(geom.intersection(hazards.caution_geom).length for geom in connector_geoms)
        if connector_geoms and not hazards.fire_geom.is_empty:
            connector_min_clearance_m = min(geom.distance(hazards.fire_geom) for geom in connector_geoms)

        hazard_overlap_m = network_hazard_overlap_m + connector_hazard_overlap_m
        min_clearance_m = min(network_min_clearance_m, connector_min_clearance_m)
        if hazards.fire_geom.is_empty:
            min_clearance_m = 999999.0

        total_distance_m = network_distance_m + connector_distance_m
        total_eta_min = network_eta_min + connector_eta_min

        warnings: list[str] = []
        if hazard_overlap_m > 0:
            warnings.append("화선 경계 버퍼를 일부 통과합니다.")
        if min_clearance_m < settings.wildfire_caution_buffer_m:
            warnings.append("화선과 매우 가깝습니다.")
        if any(not segment["night_ok"] for segment in segments):
            warnings.append("야간 비권장 구간이 포함됩니다.")
        if any(segment["segment_type"] == "trail" for segment in segments):
            warnings.append("산길/보행구간이 포함됩니다.")
        if start_anchor.snap_distance_m >= 60:
            warnings.append(f"시작점이 네트워크에서 {round(start_anchor.snap_distance_m)}m 떨어져 있습니다.")
        if target.anchor.snap_distance_m >= 60:
            warnings.append(f"목표 지점이 네트워크에서 {round(target.anchor.snap_distance_m)}m 떨어져 있습니다.")
        if target.target_rank > 1:
            warnings.append(f"대체 목표 {target.target_rank}순위입니다.")

        score = 100.0
        score -= total_eta_min * 1.9
        score -= hazard_overlap_m / 20.0
        score -= connector_distance_m / 70.0
        score -= max(0.0, settings.wildfire_caution_buffer_m - min_clearance_m) / 4.0
        if any(not segment["night_ok"] for segment in segments):
            score -= 6.0
        if any(segment["segment_type"] == "trail" for segment in segments):
            score -= 4.0
        if target.target_rank > 1:
            score -= min((target.target_rank - 1) * 2.5, 8.0)
        score = max(0, min(100, round(score)))

        severity = self._severity(hazard_overlap_m, min_clearance_m, warnings)

        return {
            "id": route_id,
            "target": {
                "layer_name": target.layer_name,
                "feature_id": target.feature_id,
                "name": target.feature.get("properties", {}).get("name"),
                "address": target.feature.get("properties", {}).get("address"),
                "geometry": target.feature.get("geometry"),
                "target_rank": target.target_rank,
            },
            "distance_m": round(float(total_distance_m), 1),
            "network_distance_m": round(float(network_distance_m), 1),
            "connector_distance_m": round(float(connector_distance_m), 1),
            "eta_min": round(float(total_eta_min), 1),
            "network_eta_min": round(float(network_eta_min), 1),
            "connector_eta_min": round(float(connector_eta_min), 1),
            "hazard_overlap_m": round(float(hazard_overlap_m), 1),
            "min_clearance_m": round(float(min_clearance_m), 1),
            "score": score,
            "severity": severity,
            "reason": self._reason(target, same_target_alt=same_target_alt),
            "warnings": warnings,
            "geometry": mapping(line),
            "segments": segments,
        }

    def route(self, request: RouteRequest) -> dict[str, Any]:
        graph, hazards = self._search_graph(
            night_mode=request.night_mode,
            blocked_segment_ids=request.blocked_segment_ids,
        )

        start_anchor = self._anchor_point(graph, request.start.lng, request.start.lat, "시작점")
        targets = self._candidate_targets(graph, request, start_anchor)

        routes: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[tuple[float, float], ...]]] = set()

        if len(targets) == 1:
            target = targets[0]
            for index, path in enumerate(
                self._diverse_paths(graph, start_anchor.node, target.anchor.node, request.max_candidates),
                start=1,
            ):
                signature = (target.feature_id, tuple(path))
                if signature in seen:
                    continue
                seen.add(signature)
                routes.append(
                    self._candidate_from_path(
                        graph=graph,
                        hazards=hazards,
                        path=path,
                        target=target,
                        start_anchor=start_anchor,
                        route_id=f"route-{index}",
                        same_target_alt=index > 1,
                    )
                )
        else:
            for target in targets:
                try:
                    path = nx.shortest_path(graph, start_anchor.node, target.anchor.node, weight="search_cost")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                signature = (target.feature_id, tuple(path))
                if signature in seen:
                    continue
                seen.add(signature)
                routes.append(
                    self._candidate_from_path(
                        graph=graph,
                        hazards=hazards,
                        path=path,
                        target=target,
                        start_anchor=start_anchor,
                        route_id=f"route-{len(routes) + 1}",
                        same_target_alt=False,
                    )
                )
                if len(routes) >= request.max_candidates:
                    break

            if len(routes) < request.max_candidates:
                for target in targets[:2]:
                    for path in self._diverse_paths(graph, start_anchor.node, target.anchor.node, 2):
                        signature = (target.feature_id, tuple(path))
                        if signature in seen:
                            continue
                        seen.add(signature)
                        routes.append(
                            self._candidate_from_path(
                                graph=graph,
                                hazards=hazards,
                                path=path,
                                target=target,
                                start_anchor=start_anchor,
                                route_id=f"route-{len(routes) + 1}",
                                same_target_alt=target.target_rank == 1,
                            )
                        )
                        if len(routes) >= request.max_candidates:
                            break
                    if len(routes) >= request.max_candidates:
                        break

            routes.sort(
                key=lambda item: (
                    -item["score"],
                    item["eta_min"],
                    item["hazard_overlap_m"],
                    item["connector_distance_m"],
                    item["target"]["target_rank"],
                )
            )
            for index, route in enumerate(routes, start=1):
                route["id"] = f"route-{index}"

        if not routes:
            raise ValueError("경로를 찾지 못했습니다.")

        return {
            "start": request.start.model_dump(),
            "resolved_target": routes[0]["target"],
            "night_mode": request.night_mode,
            "routes": routes[: request.max_candidates],
            "analysis": {
                "start_snap_distance_m": round(float(start_anchor.snap_distance_m), 1),
                "blocked_segment_ids": request.blocked_segment_ids,
                "targets_considered": len(targets),
            },
        }

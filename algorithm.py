"""
algorithm.py - Dynamic A* engine for the UNIB campus graph.

The engine follows the draft design: A* guided by a geometric heuristic,
scenario-aware edge weights, and runtime road conditions. UI edits are passed
through the conditions dictionary so the graph topology can change per query
without rewriting the base campus data.
"""

from __future__ import annotations

import heapq
import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple


CONDITION_MULTIPLIER: Dict[str, float] = {
    "NORMAL": 1.0,
    "BUSY": 2.0,
    "POTHOLE": 1.6,
    "NARROW": 1.8,
    "CLOSED": float("inf"),
    "CONSTRUCTION": 2.5,
    "VIP_ROUTE": 0.5,
}

SCENARIO_MODIFIERS: Dict[str, Dict[str, float]] = {
    "Normal": {},
}

SCENARIO_BLOCKED: Dict[str, Set[str]] = {
    "Normal": set(),
}

# Global cache untuk overlap detection (built once at startup)
_OVERLAP_CACHE: Dict[str, Set[str]] = {}


def _build_overlap_cache(all_edges: list) -> Dict[str, Set[str]]:
    """Build cache of overlapping edges untuk performa lebih baik."""
    cache = {}
    for edge in all_edges:
        overlapping = _find_overlapping_edges(edge["id"], all_edges)
        if overlapping:
            cache[edge["id"]] = overlapping
    return cache


def set_overlap_cache(all_edges: list):
    """Set global overlap cache (call once at app startup)."""
    global _OVERLAP_CACHE
    _OVERLAP_CACHE = _build_overlap_cache(all_edges)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return straight-line GPS distance in meters."""
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _edge_geometry_distance(p1: List[float], p2: List[float]) -> float:
    """Hitung jarak antara dua titik dengan haversine."""
    if len(p1) < 2 or len(p2) < 2:
        return float("inf")
    return haversine(p1[0], p1[1], p2[0], p2[1])


def _edges_overlap(edge1: dict, edge2: dict, threshold_meters: float = 5.0) -> bool:
    """
    Deteksi apakah dua jalan saling tindih berdasarkan geometry mereka.
    Dua jalan dianggap tindih jika:
    - Kedua endpoint dalam jarak threshold
    - Atau geometry paths berdekatan
    """
    from_1, to_1 = edge1.get("from"), edge1.get("to")
    from_2, to_2 = edge2.get("from"), edge2.get("to")
    
    # Jika from/to sama atau mirip, besar kemungkinan tindih
    if (from_1 == from_2 and to_1 == to_2) or (from_1 == to_2 and to_1 == from_2):
        return True
    
    geom1 = edge1.get("geometry", [])
    geom2 = edge2.get("geometry", [])
    
    if not geom1 or not geom2 or len(geom1) < 2 or len(geom2) < 2:
        return False
    
    # Bandingkan start dan end points
    start1, end1 = geom1[0], geom1[-1]
    start2, end2 = geom2[0], geom2[-1]
    
    # Hitung jarak antar endpoints
    dist_start_start = _edge_geometry_distance(start1, start2)
    dist_start_end = _edge_geometry_distance(start1, end2)
    dist_end_start = _edge_geometry_distance(end1, start2)
    dist_end_end = _edge_geometry_distance(end1, end2)
    
    # Jika dua endpoints utama sangat dekat dengan threshold
    if (dist_start_start < threshold_meters and dist_end_end < threshold_meters) or \
       (dist_start_end < threshold_meters and dist_end_start < threshold_meters):
        return True
    
    # Lakukan pengecekan pada intermediate points (sampling)
    if len(geom1) > 2 or len(geom2) > 2:
        # Ambil titik tengah setiap jalan dan hitung jarak
        mid_idx1 = len(geom1) // 2
        mid_idx2 = len(geom2) // 2
        mid1 = geom1[mid_idx1]
        mid2 = geom2[mid_idx2]
        
        # Jika tengahnya dekat dan endpoints dekat, kemungkinan tindih
        if _edge_geometry_distance(mid1, mid2) < threshold_meters * 1.5:
            # Cek lagi endpoints
            if dist_start_start < threshold_meters * 2 or dist_end_end < threshold_meters * 2 or \
               dist_start_end < threshold_meters * 2 or dist_end_start < threshold_meters * 2:
                return True
    
    return False


def _find_overlapping_edges(edge_id: str, all_edges: list) -> Set[str]:
    """Temukan semua edge yang saling tindih dengan edge_id tertentu."""
    overlapping = set()
    target_edge = None
    
    for e in all_edges:
        if e["id"] == edge_id:
            target_edge = e
            break
    
    if not target_edge:
        return overlapping
    
    for e in all_edges:
        if e["id"] != edge_id and _edges_overlap(target_edge, e):
            overlapping.add(e["id"])
    
    return overlapping


def _is_identical_edge(edge1: dict, edge2: dict, tolerance_m: float = 2.0) -> bool:
    """
    Check if two edges are identical (same route with similar endpoints).
    Handles both forward and reverse directions.
    """
    e1_from, e1_to = edge1.get("from"), edge1.get("to")
    e2_from, e2_to = edge2.get("from"), edge2.get("to")
    
    geom1 = edge1.get("geometry", [])
    geom2 = edge2.get("geometry", [])
    
    if not geom1 or not geom2 or len(geom1) < 2 or len(geom2) < 2:
        return False
    
    # Check if endpoints match (forward or reverse)
    start1, end1 = geom1[0], geom1[-1]
    start2, end2 = geom2[0], geom2[-1]
    
    # Forward match: start1==start2 and end1==end2
    forward = (_edge_geometry_distance(start1, start2) < tolerance_m and 
               _edge_geometry_distance(end1, end2) < tolerance_m)
    
    # Reverse match: start1==end2 and end1==start2
    reverse = (_edge_geometry_distance(start1, end2) < tolerance_m and 
               _edge_geometry_distance(end1, start2) < tolerance_m)
    
    return forward or reverse


def _remove_duplicate_edges(edges: list) -> list:
    """
    Remove identical duplicate edges, keeping the first occurrence.
    Returns deduplicated edge list.
    """
    seen_signatures = set()
    result = []
    
    for edge in edges:
        is_duplicate = False
        edge_id = edge.get("id", "")
        
        # Check against previously seen edges
        for existing in result:
            if _is_identical_edge(edge, existing, tolerance_m=3.0):
                is_duplicate = True
                break
        
        if not is_duplicate:
            result.append(edge)
            seen_signatures.add(edge_id)
    
    return result


def _split_long_edges(edges: list, nodes: dict, max_length_m: float = 120.0) -> Tuple[list, dict]:
    """
    Split edges longer than max_length_m into multiple segments with intermediate waypoints.
    Returns (new_edges_list, updated_nodes_dict) with auto-generated intermediate nodes.
    """
    result_edges = []
    result_nodes = dict(nodes)  # Copy existing nodes
    
    # Counter for auto-generated node IDs
    existing_ids = set(nodes.keys())
    node_counter = 0
    
    def generate_node_id():
        nonlocal node_counter
        while True:
            node_id = f"waypoint_{node_counter}"
            node_counter += 1
            if node_id not in existing_ids:
                return node_id
    
    for edge in edges:
        geometry = edge.get("geometry", [])
        if len(geometry) < 2:
            result_edges.append(edge)
            continue
        
        # Calculate total distance
        total_dist = 0.0
        for i in range(len(geometry) - 1):
            lat1, lon1 = geometry[i]
            lat2, lon2 = geometry[i + 1]
            dist = haversine(lat1, lon1, lat2, lon2)
            total_dist += dist
        
        # If edge is short enough, keep as-is
        if total_dist <= max_length_m:
            if "condition_id" not in edge:
                edge = dict(edge)
                edge["condition_id"] = edge["id"]
            result_edges.append(edge)
            continue
        
        # Split long edge into segments
        segments = []
        current_segment_points = [geometry[0]]
        current_dist = 0.0
        
        for i in range(len(geometry) - 1):
            lat1, lon1 = geometry[i]
            lat2, lon2 = geometry[i + 1]
            segment_dist = haversine(lat1, lon1, lat2, lon2)
            
            if current_dist + segment_dist > max_length_m and len(current_segment_points) > 1:
                # Finalize current segment
                current_segment_points.append(geometry[i])
                segments.append((current_segment_points, current_dist))
                
                # Start new segment
                current_segment_points = [geometry[i]]
                current_dist = 0.0
            
            current_segment_points.append(geometry[i + 1])
            current_dist += segment_dist
        
        # Add last segment
        if len(current_segment_points) > 1:
            segments.append((current_segment_points, current_dist))
        
        # Create edge for each segment
        current_from = edge["from"]
        cond_id = edge.get("condition_id", edge["id"])
        for idx, (seg_geom, seg_dist) in enumerate(segments):
            if idx == len(segments) - 1:
                # Last segment - use original destination
                seg_to = edge["to"]
            else:
                # Create waypoint
                seg_to = generate_node_id()
                # Average lat/lon of endpoint
                end_lat, end_lon = seg_geom[-1]
                result_nodes[seg_to] = {
                    "id": seg_to,
                    "name": f"Waypoint {node_counter}",
                    "type": "waypoint",
                    "lat": end_lat,
                    "lon": end_lon,
                }
            
            # Create edge for this segment
            new_edge = {
                "id": f"{edge['id']}_{idx}" if len(segments) > 1 else edge["id"],
                "from": current_from,
                "to": seg_to,
                "distance": seg_dist,
                "surface": edge.get("surface", "asphalt"),
                "bidirectional": edge.get("bidirectional", True),
                "geometry": seg_geom,
                "condition_id": cond_id,
            }
            result_edges.append(new_edge)
            current_from = seg_to
    
    return result_edges, result_nodes




def calc_cost(
    edge: dict,
    scenario: str,
    conditions: dict,
    time_factor: float,
    all_edges: Optional[list] = None,
    overlap_cache: Optional[Dict[str, Set[str]]] = None,
) -> float:
    """
    Dynamic edge cost.

    cost(e) = distance * k_surface * k_scenario * k_condition * time_factor
    CLOSED edges return infinity and are skipped by A*.
    
    Jika ada jalan yang saling tindih, gunakan kondisi yang paling ketat (highest multiplier).
    """
    surface_multiplier = {
        "ASPHALT": 1.0,
        "CONCRETE": 1.1,
        "DIRT": 1.4,
        "STAIRS": 1.8,
        "RAMP": 1.3,
    }

    edge_id = edge["id"]
    condition_id = edge.get("condition_id", edge_id)
    distance = float(edge.get("distance", 100))
    surface = edge.get("surface", "ASPHALT")

    blocked_edges = SCENARIO_BLOCKED.get(scenario, set())
    if edge_id in blocked_edges or condition_id in blocked_edges:
        return float("inf")

    k_surface = surface_multiplier.get(surface, 1.0)
    scenario_modifiers = SCENARIO_MODIFIERS.get(scenario, {})
    k_scenario = scenario_modifiers.get(edge_id, scenario_modifiers.get(condition_id, 1.0))
    k_condition = 1.0

    edge_conditions = conditions.get("edge_conditions", {})
    
    # Cari kondisi untuk edge ini
    condition = edge_conditions.get(edge_id, edge_conditions.get(condition_id, {}))
    status = (condition.get("status") or condition.get("type") or "NORMAL").upper()
    
    # CEK JALAN YANG TINDIH - jika salah satu ditutup, semua ditutup
    overlapping_edges = overlap_cache.get(edge_id, set()) if overlap_cache else set()
    if overlapping_edges:
        for ovr_edge_id in overlapping_edges:
            ovr_condition = edge_conditions.get(ovr_edge_id, {})
            ovr_status = (ovr_condition.get("status") or ovr_condition.get("type") or "NORMAL").upper()
            
            # Jika jalan overlap ditutup atau dalam scenario blocked, edge ini juga ditutup
            if ovr_status == "CLOSED" or ovr_edge_id in blocked_edges:
                return float("inf")
            
            # Gunakan multiplier tertinggi dari semua jalan overlap
            if ovr_status != "NORMAL":
                ovr_severity = float(
                    ovr_condition.get("severity", CONDITION_MULTIPLIER.get(ovr_status, 1.0))
                )
                edge_cond_severity = float(
                    condition.get("severity", CONDITION_MULTIPLIER.get(status, 1.0))
                )
                if ovr_severity > edge_cond_severity:
                    k_condition = ovr_severity

    if status == "CLOSED":
        return float("inf")
    if status != "NORMAL":
        k_condition = max(k_condition, float(
            condition.get("severity", CONDITION_MULTIPLIER.get(status, 1.0))
        ))

    return distance * k_surface * k_scenario * k_condition * time_factor


def find_path(
    nodes: dict,
    edges: list,
    start_id: str,
    goal_id: str,
    scenario: str = "Normal",
    time_factor: float = 1.0,
    conditions: Optional[dict] = None,
) -> dict:
    """
    Run Dynamic A*.

    nodes: {id: {id, name, type, lat, lon}}
    edges: [{id, from, to, distance, surface, bidirectional}]
    conditions:
        edge_conditions: {edge_id: {status, severity}}
        edge_directions: {edge_id: TWO_WAY|ONE_WAY_FORWARD|ONE_WAY_REVERSE}
    """
    conditions = conditions or {}
    t0 = time.perf_counter()

    if start_id not in nodes:
        return {"error": "NODE_NOT_FOUND", "detail": f"Node '{start_id}' tidak ada"}
    if goal_id not in nodes:
        return {"error": "NODE_NOT_FOUND", "detail": f"Node '{goal_id}' tidak ada"}
    if start_id == goal_id:
        return {
            "path": [start_id],
            "total_cost": 0,
            "total_dist_m": 0,
            "eta_minutes": 0,
            "iterations": 0,
            "execution_ms": 0,
            "visited": [start_id],
            "edges_used": [],
            "scenario": scenario,
        }

    # Use global overlap cache (built at startup, not per query)
    overlap_cache = _OVERLAP_CACHE

    edge_by_id = {edge["id"]: edge for edge in edges}
    adjacency = _build_adjacency(nodes, edges, scenario, time_factor, conditions, overlap_cache)
    heuristic_scale = _heuristic_scale(edges, scenario, time_factor, conditions, overlap_cache)

    infinity = float("inf")
    g_score: Dict[str, float] = {node_id: infinity for node_id in nodes}
    g_score[start_id] = 0.0

    came_from: Dict[str, Optional[str]] = {start_id: None}

    infinity = float("inf")
    g_score: Dict[str, float] = {node_id: infinity for node_id in nodes}
    g_score[start_id] = 0.0

    came_from: Dict[str, Optional[str]] = {start_id: None}
    came_edge: Dict[str, Optional[str]] = {start_id: None}
    visited: Set[str] = set()
    heap: List[Tuple[float, int, str]] = [
        (heuristic_scale * _h(nodes, start_id, goal_id), 0, start_id)
    ]
    counter = 1
    iterations = 0

    while heap:
        _, _, current = heapq.heappop(heap)
        if current in visited:
            continue

        visited.add(current)
        iterations += 1

        if current == goal_id:
            path = _reconstruct(came_from, goal_id)
            edges_used = [came_edge[n] for n in path[1:] if came_edge.get(n)]
            physical_distance = sum(
                float(edge_by_id[edge_id].get("distance", 0))
                for edge_id in edges_used
                if edge_id in edge_by_id
            )
            effective_cost = g_score[goal_id]
            eta = effective_cost / 80  # walking speed including penalties
            return {
                "path": path,
                "total_cost": round(effective_cost, 2),
                "total_dist_m": round(physical_distance, 1),
                "eta_minutes": round(eta, 1),
                "iterations": iterations,
                "execution_ms": round((time.perf_counter() - t0) * 1000, 3),
                "visited": list(visited),
                "edges_used": edges_used,
                "scenario": scenario,
            }

        for neighbor, edge_id, weight in adjacency.get(current, []):
            if neighbor in visited or weight == infinity:
                continue

            tentative_g = g_score[current] + weight
            if tentative_g < g_score.get(neighbor, infinity):
                g_score[neighbor] = tentative_g
                came_from[neighbor] = current
                came_edge[neighbor] = edge_id
                f_score = tentative_g + heuristic_scale * _h(nodes, neighbor, goal_id)
                heapq.heappush(heap, (f_score, counter, neighbor))
                counter += 1

    return {
        "error": "NO_PATH_FOUND",
        "detail": f"Tidak ada jalur dari '{start_id}' ke '{goal_id}'",
        "iterations": iterations,
        "execution_ms": round((time.perf_counter() - t0) * 1000, 3),
        "visited": list(visited),
    }


def event_routing(
    nodes: dict,
    edges: list,
    gate_ids: List[str],
    event_id: str,
    scenario: str = "Normal",
    time_factor: float = 1.0,
    conditions: Optional[dict] = None,
) -> List[dict]:
    """Find routes from all selected gates to one event location."""
    results = []
    for gate_id in gate_ids:
        result = find_path(nodes, edges, gate_id, event_id, scenario, time_factor, conditions)
        result["gate"] = gate_id
        result["gate_name"] = nodes.get(gate_id, {}).get("name", gate_id)
        results.append(result)

    results.sort(
        key=lambda result: (
            1 if "error" in result else 0,
            result.get("total_cost", float("inf")),
        )
    )
    return results


def _build_adjacency(
    nodes: dict,
    edges: list,
    scenario: str,
    time_factor: float,
    conditions: dict,
    overlap_cache: Optional[Dict[str, Set[str]]] = None,
) -> Dict[str, List[Tuple[str, str, float]]]:
    adjacency: Dict[str, List[Tuple[str, str, float]]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        from_node, to_node = edge["from"], edge["to"]
        if from_node not in nodes or to_node not in nodes:
            continue

        weight = calc_cost(edge, scenario, conditions, time_factor, edges, overlap_cache)
        direction = _direction_for(edge, conditions)

        if direction in ("TWO_WAY", "BIDIRECTIONAL"):
            adjacency[from_node].append((to_node, edge["id"], weight))
            adjacency[to_node].append((from_node, edge["id"], weight))
        elif direction == "ONE_WAY_FORWARD":
            adjacency[from_node].append((to_node, edge["id"], weight))
        elif direction == "ONE_WAY_REVERSE":
            adjacency[to_node].append((from_node, edge["id"], weight))

    return adjacency


def _direction_for(edge: dict, conditions: dict) -> str:
    condition_id = edge.get("condition_id", edge["id"])
    override = conditions.get("edge_directions", {}).get(
        edge["id"],
        conditions.get("edge_directions", {}).get(condition_id),
    )
    if override:
        return override
    return "TWO_WAY" if edge.get("bidirectional", True) else "ONE_WAY_FORWARD"


def _heuristic_scale(
    edges: list,
    scenario: str,
    time_factor: float,
    conditions: dict,
    overlap_cache: Optional[Dict[str, Set[str]]] = None,
) -> float:
    """
    Keep the heuristic admissible even when an edge receives a bonus multiplier.
    If every traversable edge costs at least its physical distance, the scale is 1.
    """
    factors = []
    for edge in edges:
        distance = float(edge.get("distance", 0))
        if distance <= 0:
            continue
        cost = calc_cost(edge, scenario, conditions, time_factor, edges, overlap_cache)
        if math.isfinite(cost):
            factors.append(cost / distance)
    return min(1.0, min(factors, default=1.0))


def _h(nodes: dict, node_a: str, node_b: str) -> float:
    a, b = nodes[node_a], nodes[node_b]
    return haversine(a["lat"], a["lon"], b["lat"], b["lon"])


def _reconstruct(came_from: dict, goal_id: str) -> List[str]:
    path = []
    node = goal_id
    while node is not None:
        path.append(node)
        node = came_from.get(node)
    path.reverse()
    return path


class Engine:
    """Small facade used by the UI."""

    def __init__(self, nodes: dict, edges: list):
        self._nodes = nodes
        self._edges = edges
        self._conditions: dict = {"edge_conditions": {}, "edge_directions": {}}

    @property
    def nodes(self) -> dict:
        return self._nodes

    @property
    def edges(self) -> list:
        return self._edges

    @property
    def conditions(self) -> dict:
        return self._conditions

    def find_path(
        self,
        start_id: str,
        goal_id: str,
        scenario_enum,
        time_factor: float = 1.0,
    ) -> dict:
        scenario = scenario_enum.value if hasattr(scenario_enum, "value") else str(scenario_enum)
        return find_path(
            self._nodes,
            self._edges,
            start_id,
            goal_id,
            scenario,
            time_factor,
            self._conditions,
        )

    def compare_all_scenarios(
        self,
        start_id: str,
        goal_id: str,
        time_factor: float = 1.0,
    ) -> dict:
        from campus_data import Scenario

        return {
            scenario.value: find_path(
                self._nodes,
                self._edges,
                start_id,
                goal_id,
                scenario.value,
                time_factor,
                self._conditions,
            )
            for scenario in Scenario
        }

    def event_routing(
        self,
        gate_ids: List[str],
        event_id: str,
        scenario_enum,
        time_factor: float = 1.0,
    ) -> List[dict]:
        scenario = scenario_enum.value if hasattr(scenario_enum, "value") else str(scenario_enum)
        return event_routing(
            self._nodes,
            self._edges,
            gate_ids,
            event_id,
            scenario,
            time_factor,
            self._conditions,
        )

    def set_conditions(self, conditions: dict):
        self._conditions = {
            "edge_conditions": conditions.get("edge_conditions", {}),
            "edge_directions": conditions.get("edge_directions", {}),
        }


def build_engine() -> Engine:
    """Convert dataclass campus data into dictionaries consumed by A*."""
    from campus_data import CAMPUS_EDGES, CAMPUS_NODES, SCENARIO_CONFIG

    nodes: dict = {}
    for node in CAMPUS_NODES:
        nodes[node.id] = {
            "id": node.id,
            "name": node.name,
            "type": node.node_type.value,
            "lat": node.lat,
            "lon": node.lon,
        }

    edges: list = []
    for edge in CAMPUS_EDGES:
        edges.append(
            {
                "id": edge.id,
                "from": edge.from_node,
                "to": edge.to_node,
                "distance": edge.distance,
                "surface": edge.surface.name,
                "bidirectional": edge.is_bidirectional,
                "geometry": edge.geometry,
            }
        )

    SCENARIO_MODIFIERS.clear()
    SCENARIO_BLOCKED.clear()
    for scenario_enum, config in SCENARIO_CONFIG.items():
        scenario = scenario_enum.value
        SCENARIO_MODIFIERS[scenario] = config.get("edge_modifiers", {})
        SCENARIO_BLOCKED[scenario] = set(config.get("blocked_edges", set()))

    # Remove identical duplicate edges
    edges = _remove_duplicate_edges(edges)
    
    # Split long edges into segments with waypoints (max 120m per segment)
    edges, nodes = _split_long_edges(edges, nodes, max_length_m=120.0)
    
    return Engine(nodes, edges)

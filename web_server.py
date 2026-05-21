"""
web_server.py – Versi Stabil Tanpa API Eksternal.
Menggunakan algorithm.py dan campus_data.py lokal untuk menjamin kecepatan dan fitur.
"""
import json
import math
import os
import sys
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from algorithm import build_engine, find_path, set_overlap_cache
from campus_data import CAMPUS_EDGES, CAMPUS_NODES, SCENARIO_CONFIG, Scenario

app = Flask(__name__, template_folder="templates", static_folder="static")
CUSTOM_GRAPH_FILE = os.path.join(BASE_DIR, "data", "custom_graph.json")
CONDITIONS_FILE = os.path.join(BASE_DIR, "data", "conditions.json")
CUSTOM_SCENARIOS_FILE = os.path.join(BASE_DIR, "data", "custom_scenarios.json")
BASE_EDGE_IDS = {edge.id for edge in CAMPUS_EDGES}
AUTO_CONNECT_MAX_METERS = 70.0
ROAD_SNAP_MAX_METERS = 26.0
EARTH_RADIUS_M = 6_371_000
ROAD_NODE_PRECISION = 5
_road_routing_cache = None

def _load_custom_scenarios():
    try:
        if os.path.exists(CUSTOM_SCENARIOS_FILE):
            with open(CUSTOM_SCENARIOS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return []

def _save_custom_scenarios(data):
    os.makedirs(os.path.dirname(CUSTOM_SCENARIOS_FILE), exist_ok=True)
    with open(CUSTOM_SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _load_custom_graph():
    try:
        if os.path.exists(CUSTOM_GRAPH_FILE):
            with open(CUSTOM_GRAPH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("nodes", [])
                data.setdefault("edges", [])
                data.setdefault("deleted_edges", [])
                data.setdefault("custom_distances", {})
                _normalize_custom_graph(data)
                return data
    except: pass
    return {"nodes": [], "edges": [], "deleted_edges": [], "custom_distances": {}}

def _save_custom_graph(data):
    os.makedirs(os.path.dirname(CUSTOM_GRAPH_FILE), exist_ok=True)
    with open(CUSTOM_GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _edge_signature(edge):
    geometry = edge.get("geometry") or []
    rounded_geometry = tuple(
        (round(float(point[0]), 7), round(float(point[1]), 7))
        for point in geometry
        if len(point) >= 2
    )
    return (edge.get("from"), edge.get("to"), rounded_geometry)

def _next_custom_edge_id(custom, used_ids=None):
    used = set(used_ids or set())
    used.update(BASE_EDGE_IDS)
    used.update(edge.get("id") for edge in custom.get("edges", []) if edge.get("id"))
    used.update(custom.get("deleted_edges", []))
    counter = 1
    while f"CE{counter}" in used:
        counter += 1
    return f"CE{counter}"

def _next_custom_node_id(custom):
    existing_ids = {n["id"] for n in custom.get("nodes", [])}
    counter = 1
    while f"C{counter}" in existing_ids:
        counter += 1
    return f"C{counter}"

def _normalize_custom_graph(data):
    changed = False
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    data.setdefault("deleted_edges", [])
    data.setdefault("custom_distances", {})

    deleted_edges = set(data.get("deleted_edges", []))
    used_ids = set(BASE_EDGE_IDS)
    normalized_edges = []
    normalized_deleted = set(eid for eid in deleted_edges if eid)
    deleted_signatures = set()

    for edge in data.get("edges", []):
        edge = dict(edge)
        old_id = str(edge.get("id") or "").strip()
        if not old_id:
            old_id = _next_custom_edge_id(data, used_ids)
            edge["id"] = old_id
            changed = True

        signature = _edge_signature(edge)
        must_rename = old_id in used_ids
        if must_rename:
            new_id = _next_custom_edge_id(data, used_ids)
            edge["id"] = new_id
            changed = True
            if old_id in data["custom_distances"] and new_id not in data["custom_distances"]:
                data["custom_distances"][new_id] = data["custom_distances"][old_id]
        else:
            new_id = old_id

        used_ids.add(new_id)

        if old_id in deleted_edges:
            if not must_rename:
                normalized_deleted.add(new_id)
                deleted_signatures.add(signature)
            elif signature in deleted_signatures:
                normalized_deleted.add(new_id)
        elif new_id in deleted_edges:
            normalized_deleted.add(new_id)

        normalized_edges.append(edge)

    if normalized_edges != data.get("edges", []):
        data["edges"] = normalized_edges
        changed = True

    if sorted(normalized_deleted) != sorted(data.get("deleted_edges", [])):
        data["deleted_edges"] = sorted(normalized_deleted, key=_edge_sort_key)
        changed = True

    if changed and os.path.exists(CUSTOM_GRAPH_FILE):
        _save_custom_graph(data)

def _edge_sort_key(edge_id):
    prefix = "".join(ch for ch in edge_id if not ch.isdigit())
    digits = "".join(ch for ch in edge_id if ch.isdigit())
    return (prefix, int(digits or 0), edge_id)

def _apply_custom_distances(edges, custom_distances):
    for edge in edges:
        if edge["id"] in custom_distances:
            edge["distance"] = custom_distances[edge["id"]]

def _invalidate_route_cache():
    global _road_routing_cache
    _road_routing_cache = None

def _xy(lat, lon, ref_lat=-3.759):
    return (
        math.radians(lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat)),
        math.radians(lat) * EARTH_RADIUS_M,
    )

def _latlon_from_xy(x, y, ref_lat=-3.759):
    return (
        math.degrees(y / EARTH_RADIUS_M),
        math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(ref_lat)))),
    )

def _project_to_segment(point, start, end):
    px, py = _xy(point[0], point[1])
    ax, ay = _xy(start[0], start[1])
    bx, by = _xy(end[0], end[1])
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    x, y = ax + (dx * t), ay + (dy * t)
    lat, lon = _latlon_from_xy(x, y)
    return [lat, lon], math.hypot(px - x, py - y), t

def _nearest_point_on_geometry(point, geometry):
    if not geometry:
        return None
    best = None
    for idx in range(len(geometry) - 1):
        projected, distance, t = _project_to_segment(point, geometry[idx], geometry[idx + 1])
        if best is None or distance < best["distance"]:
            best = {"point": projected, "distance": distance, "segment_index": idx, "t": t}
    if best is None:
        only = [float(geometry[0][0]), float(geometry[0][1])]
        best = {"point": only, "distance": _point_distance(point, only), "segment_index": 0, "t": 0.0}
    return best

def _point_distance(a, b):
    from algorithm import haversine
    return haversine(float(a[0]), float(a[1]), float(b[0]), float(b[1]))

def _path_distance(geometry):
    if len(geometry) < 2:
        return 0.0
    return round(sum(_point_distance(geometry[i], geometry[i + 1]) for i in range(len(geometry) - 1)), 1)

def _clean_geometry(geometry):
    clean = []
    for point in geometry:
        normalized = [float(point[0]), float(point[1])]
        if not clean or normalized != clean[-1]:
            clean.append(normalized)
    return clean

def _geometry_for_edge(edge, nodes):
    geometry = edge.get("geometry") or []
    if geometry:
        return _clean_geometry(geometry)
    from_node = nodes.get(edge.get("from"))
    to_node = nodes.get(edge.get("to"))
    if from_node and to_node:
        return [
            [float(from_node["lat"]), float(from_node["lon"])],
            [float(to_node["lat"]), float(to_node["lon"])],
        ]
    return []

def _road_key(point):
    return (
        round(float(point[0]), ROAD_NODE_PRECISION),
        round(float(point[1]), ROAD_NODE_PRECISION),
    )

def _road_node_id(point):
    lat, lon = _road_key(point)
    return f"RN_{lat:.{ROAD_NODE_PRECISION}f}_{lon:.{ROAD_NODE_PRECISION}f}".replace("-", "M").replace(".", "P")

def _segment_distance(edge, segment_start, segment_end, full_geometry):
    physical_segment = _point_distance(segment_start, segment_end)
    physical_total = _path_distance(full_geometry)
    if physical_total <= 0:
        return round(physical_segment, 1)
    scale = float(edge.get("distance") or physical_total) / physical_total
    return round(physical_segment * scale, 1)

def _scaled_segment_distance(edge, segment_start, segment_end):
    return round(_point_distance(segment_start, segment_end) * float(edge.get("_distance_scale", 1.0)), 1)

def _route_edge(edge_id, from_node, to_node, distance, geometry, source_edge_id, source="road", surface="ASPHALT", bidirectional=True):
    return {
        "id": edge_id,
        "from": from_node,
        "to": to_node,
        "distance": round(float(distance), 1),
        "surface": surface,
        "bidirectional": bidirectional,
        "geometry": _clean_geometry(geometry),
        "condition_id": source_edge_id,
        "source": source,
    }

def _find_nearest_access_edge(point, edges):
    best = None
    for edge in edges:
        geometry = edge.get("geometry") or []
        if len(geometry) < 2:
            continue
        nearest = _nearest_point_on_geometry(point, geometry)
        if not nearest:
            continue
        if best is None or nearest["distance"] < best["nearest"]["distance"]:
            best = {"edge": edge, "nearest": nearest}
    return best

def _find_nearest_access_segment(point, segments):
    px, py = _xy(point[0], point[1])
    best = None
    for segment in segments:
        length_sq = segment["length_sq"]
        if length_sq == 0:
            t = 0.0
        else:
            t = max(
                0.0,
                min(
                    1.0,
                    ((px - segment["ax"]) * segment["dx"] + (py - segment["ay"]) * segment["dy"]) / length_sq,
                ),
            )
        x = segment["ax"] + (segment["dx"] * t)
        y = segment["ay"] + (segment["dy"] * t)
        distance_sq = (px - x) ** 2 + (py - y) ** 2
        if best is None or distance_sq < best["distance_sq"]:
            lat, lon = _latlon_from_xy(x, y)
            best = {
                "edge": segment["edge"],
                "nearest": {
                    "point": [lat, lon],
                    "distance": math.sqrt(distance_sq),
                    "segment_index": segment["segment_index"],
                    "t": t,
                },
                "distance_sq": distance_sq,
            }
    if not best:
        return None
    best.pop("distance_sq", None)
    return best

def _get_road_routing_base(display_engine):
    global _road_routing_cache
    engine_token = id(display_engine)
    if _road_routing_cache and _road_routing_cache.get("engine_token") == engine_token:
        return _road_routing_cache

    route_nodes = {}
    route_edges = []
    road_node_by_key = {}
    access_segments = []
    display_edges = [
        dict(edge, geometry=_geometry_for_edge(edge, display_engine.nodes))
        for edge in display_engine.edges
    ]
    display_edges = [edge for edge in display_edges if len(edge.get("geometry", [])) >= 2]

    def add_road_node(point):
        key = _road_key(point)
        if key in road_node_by_key:
            return road_node_by_key[key]
        node_id = _road_node_id(point)
        road_node_by_key[key] = node_id
        route_nodes[node_id] = {
            "id": node_id,
            "name": "Titik jalan",
            "type": "Road",
            "lat": key[0],
            "lon": key[1],
        }
        return node_id

    for edge in display_edges:
        geometry = edge["geometry"]
        surface = edge.get("surface", "ASPHALT")
        physical_total = _path_distance(geometry)
        edge["_distance_scale"] = (
            float(edge.get("distance") or physical_total) / physical_total
            if physical_total > 0 else 1.0
        )
        for index in range(len(geometry) - 1):
            start_point, end_point = geometry[index], geometry[index + 1]
            from_road = add_road_node(start_point)
            to_road = add_road_node(end_point)
            ax, ay = _xy(start_point[0], start_point[1])
            bx, by = _xy(end_point[0], end_point[1])
            dx, dy = bx - ax, by - ay
            access_segments.append({
                "edge": edge,
                "segment_index": index,
                "ax": ax,
                "ay": ay,
                "bx": bx,
                "by": by,
                "dx": dx,
                "dy": dy,
                "length_sq": dx * dx + dy * dy,
            })
            route_edges.append(
                _route_edge(
                    f"RSEG_{edge['id']}_{index}",
                    from_road,
                    to_road,
                    _scaled_segment_distance(edge, start_point, end_point),
                    [start_point, end_point],
                    edge.get("condition_id", edge["id"]),
                    surface=surface,
                    bidirectional=edge.get("bidirectional", True),
                )
            )

    _road_routing_cache = {
        "engine_token": engine_token,
        "nodes": route_nodes,
        "edges": route_edges,
        "road_node_by_key": road_node_by_key,
        "display_edges": display_edges,
        "access_segments": access_segments,
        "access_cache": {},
    }
    return _road_routing_cache

def _build_road_routing_graph(display_engine, start_id, end_id):
    base = _get_road_routing_base(display_engine)
    route_nodes = dict(base["nodes"])
    route_edges = list(base["edges"])
    road_node_by_key = dict(base["road_node_by_key"])
    access_segments = base["access_segments"]

    def add_road_node(point):
        key = _road_key(point)
        if key in road_node_by_key:
            return road_node_by_key[key]
        node_id = _road_node_id(point)
        road_node_by_key[key] = node_id
        route_nodes[node_id] = {
            "id": node_id,
            "name": "Titik jalan",
            "type": "Road",
            "lat": key[0],
            "lon": key[1],
        }
        return node_id

    def add_access(node_id, role):
        node = display_engine.nodes.get(node_id)
        if not node:
            return None
        point = [float(node["lat"]), float(node["lon"])]
        route_nodes[node_id] = dict(node)
        access_cache = base["access_cache"]
        if node_id in access_cache:
            nearest = access_cache[node_id]
        else:
            nearest = _find_nearest_access_segment(point, access_segments)
            access_cache[node_id] = nearest
        if not nearest:
            return None

        edge = nearest["edge"]
        geometry = edge["geometry"]
        projected = nearest["nearest"]["point"]
        segment_index = nearest["nearest"]["segment_index"]
        segment_index = max(0, min(segment_index, len(geometry) - 2))
        road_node = add_road_node(projected)
        surface = edge.get("surface", "ASPHALT")
        source_edge_id = edge.get("condition_id", edge["id"])

        route_edges.append(
            _route_edge(
                f"ACCESS_{role}_{node_id}",
                node_id,
                road_node,
                _point_distance(point, projected),
                [point, projected],
                f"ACCESS_{role}_{node_id}",
                source="access",
                surface="ASPHALT",
            )
        )

        for suffix, endpoint in (
            ("A", geometry[segment_index]),
            ("B", geometry[segment_index + 1]),
        ):
            endpoint_node = add_road_node(endpoint)
            distance = _scaled_segment_distance(edge, projected, endpoint)
            if distance <= 0:
                continue
            route_edges.append(
                _route_edge(
                    f"RACCESS_{role}_{source_edge_id}_{suffix}",
                    road_node,
                    endpoint_node,
                    distance,
                    [projected, endpoint],
                    source_edge_id,
                    source="road_access",
                    surface=surface,
                    bidirectional=edge.get("bidirectional", True),
                )
            )
        return road_node

    start_access = add_access(start_id, "START")
    end_access = add_access(end_id, "END")
    if not start_access or not end_access:
        return None
    return {"nodes": route_nodes, "edges": route_edges}

def _display_path(start_id, end_id, nodes):
    path = []
    for node_id in (start_id, end_id):
        if node_id in nodes and node_id not in path:
            path.append(node_id)
    return path

def _split_geometry_at(geometry, segment_index, projected_point):
    geom = _clean_geometry(geometry)
    projected = [float(projected_point[0]), float(projected_point[1])]
    segment_index = max(0, min(segment_index, max(0, len(geom) - 2)))
    first = _clean_geometry(geom[: segment_index + 1] + [projected])
    second = _clean_geometry([projected] + geom[segment_index + 1 :])
    return first, second

def _connected_components(nodes, edges):
    adjacency = defaultdict(set)
    for edge in edges:
        if edge.get("from") in nodes and edge.get("to") in nodes:
            adjacency[edge["from"]].add(edge["to"])
            adjacency[edge["to"]].add(edge["from"])

    seen = set()
    components = []
    for node_id in nodes:
        if node_id in seen:
            continue
        queue = deque([node_id])
        seen.add(node_id)
        component = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    components.sort(key=len, reverse=True)
    return components

def _auto_edge(edge_id, from_node, to_node, geometry, surface="ASPHALT", condition_id=None):
    geometry = _clean_geometry(geometry)
    res = {
        "id": edge_id,
        "from": from_node,
        "to": to_node,
        "distance": _path_distance(geometry),
        "surface": surface,
        "bidirectional": True,
        "geometry": geometry,
        "source": "auto",
    }
    if condition_id:
        res["condition_id"] = condition_id
    return res

def _augment_with_auto_road_links(engine, deleted_edges):
    components = _connected_components(engine.nodes, engine.edges)
    if len(components) <= 1:
        return

    main_component = set(components[0])
    existing_ids = {edge["id"] for edge in engine.edges}
    main_edges = [
        edge for edge in engine.edges
        if edge.get("from") in main_component
        and edge.get("to") in main_component
        and edge.get("geometry")
        and not str(edge.get("id", "")).startswith("AUTO_")
    ]

    for component in components[1:]:
        best = None
        for node_id in component:
            node = engine.nodes[node_id]
            point = [node["lat"], node["lon"]]
            for edge in main_edges:
                nearest = _nearest_point_on_geometry(point, edge.get("geometry", []))
                if not nearest:
                    continue
                if best is None or nearest["distance"] < best["nearest"]["distance"]:
                    best = {"node_id": node_id, "edge": edge, "nearest": nearest}

        if not best or best["nearest"]["distance"] > AUTO_CONNECT_MAX_METERS:
            continue

        node_id = best["node_id"]
        target_edge = best["edge"]
        nearest = best["nearest"]
        safe_edge_id = target_edge["id"].replace(" ", "_")
        junction_id = f"RJ_{node_id}_{safe_edge_id}"
        split_a_id = f"AUTO_SPLIT_{node_id}_{safe_edge_id}_A"
        split_b_id = f"AUTO_SPLIT_{node_id}_{safe_edge_id}_B"
        link_id = f"AUTO_LINK_{node_id}_{safe_edge_id}"

        if link_id in deleted_edges:
            continue

        point = nearest["point"]
        engine.nodes[junction_id] = {
            "id": junction_id,
            "name": "Simpang jalan otomatis",
            "type": "Waypoint",
            "lat": point[0],
            "lon": point[1],
            "source": "auto",
        }

        first_geom, second_geom = _split_geometry_at(
            target_edge["geometry"],
            nearest["segment_index"],
            point,
        )
        surface = target_edge.get("surface", "ASPHALT")
        target_cond_id = target_edge.get("condition_id", target_edge["id"])
        for generated in (
            _auto_edge(split_a_id, target_edge["from"], junction_id, first_geom, surface, condition_id=target_cond_id),
            _auto_edge(split_b_id, junction_id, target_edge["to"], second_geom, surface, condition_id=target_cond_id),
            _auto_edge(
                link_id,
                node_id,
                junction_id,
                [[engine.nodes[node_id]["lat"], engine.nodes[node_id]["lon"]], point],
                surface,
            ),
        ):
            if generated["id"] not in existing_ids and generated["id"] not in deleted_edges:
                engine._edges.append(generated)
                existing_ids.add(generated["id"])

def _find_nearest_graph_edge(lat, lon, edges):
    point = [float(lat), float(lon)]
    best = None
    for edge in edges:
        edge_id = str(edge.get("id", ""))
        if edge_id.startswith("AUTO_") or not edge.get("geometry"):
            continue
        nearest = _nearest_point_on_geometry(point, edge["geometry"])
        if not nearest:
            continue
        if best is None or nearest["distance"] < best["nearest"]["distance"]:
            best = {"edge": edge, "nearest": nearest}
    return best

def _split_edge_into_custom_segments(custom, edge, waypoint_node, nearest):
    edge_id = edge["id"]
    if edge_id.startswith("AUTO_"):
        return False

    first_geom, second_geom = _split_geometry_at(
        edge.get("geometry", []),
        nearest["segment_index"],
        [waypoint_node["lat"], waypoint_node["lon"]],
    )
    if len(first_geom) < 2 or len(second_geom) < 2:
        return False

    custom.setdefault("deleted_edges", [])
    custom.setdefault("edges", [])
    custom.setdefault("custom_distances", {})
    used_ids = {custom_edge.get("id") for custom_edge in custom.get("edges", [])}
    used_ids.update(custom.get("deleted_edges", []))

    source_edge = None
    remaining_custom_edges = []
    for custom_edge in custom.get("edges", []):
        if custom_edge.get("id") == edge_id:
            source_edge = custom_edge
        else:
            remaining_custom_edges.append(custom_edge)

    if source_edge is None and edge_id in BASE_EDGE_IDS:
        if edge_id not in custom["deleted_edges"]:
            custom["deleted_edges"].append(edge_id)
    elif source_edge is not None:
        custom["edges"] = remaining_custom_edges
    else:
        return False

    surface = edge.get("surface", "ASPHALT")
    edge_a_id = _next_custom_edge_id(custom, used_ids)
    used_ids.add(edge_a_id)
    edge_b_id = _next_custom_edge_id(custom, used_ids)
    cond_id = edge.get("condition_id", edge_id)
    custom["edges"].extend([
        {
            "id": edge_a_id,
            "from": edge["from"],
            "to": waypoint_node["id"],
            "distance": _path_distance(first_geom),
            "surface": surface,
            "bidirectional": True,
            "geometry": first_geom,
            "condition_id": cond_id,
        },
        {
            "id": edge_b_id,
            "from": waypoint_node["id"],
            "to": edge["to"],
            "distance": _path_distance(second_geom),
            "surface": surface,
            "bidirectional": True,
            "geometry": second_geom,
            "condition_id": cond_id,
        },
    ])
    custom["custom_distances"].pop(edge_id, None)
    return True

def _build_combined_engine():
    custom = _load_custom_graph()

    engine = build_engine()

    for n in custom.get("nodes", []):
        engine.nodes[n["id"]] = n

    deleted_edges = set(custom.get("deleted_edges", []))
    engine._edges = [e for e in engine.edges if e["id"] not in deleted_edges]

    for e in custom.get("edges", []):
        if e["id"] not in deleted_edges:
            engine._edges.append(e)

    # Apply saved direction overrides (bidirectional / one-way)
    edge_directions = custom.get("edge_directions", {})
    for edge in engine._edges:
        eid = edge.get("id", "")
        if eid in edge_directions:
            d = edge_directions[eid]
            edge["bidirectional"] = d.get("bidirectional", True)
            if d.get("from"):
                edge["from"] = d["from"]
            if d.get("to"):
                edge["to"] = d["to"]

    custom_distances = custom.get("custom_distances", {})
    _apply_custom_distances(engine._edges, custom_distances)
    _augment_with_auto_road_links(engine, deleted_edges)
    _apply_custom_distances(engine._edges, custom_distances)

    # Register custom scenarios
    from algorithm import SCENARIO_MODIFIERS, SCENARIO_BLOCKED
    for cs in _load_custom_scenarios():
        SCENARIO_MODIFIERS[cs["id"]] = cs.get("edge_modifiers", {})
        SCENARIO_BLOCKED[cs["id"]] = set(cs.get("blocked_edges", []))

    return engine

# Build engine saat startup
engine = _build_combined_engine()
# Initialize overlap cache
set_overlap_cache(engine.edges)


def _load_conditions():
    try:
        if os.path.exists(CONDITIONS_FILE):
            with open(CONDITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {"edge_conditions": {}, "edge_directions": {}}

def _save_conditions(data):
    os.makedirs(os.path.dirname(CONDITIONS_FILE), exist_ok=True)
    with open(CONDITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/graph")
def api_graph():
    global engine
    engine = _build_combined_engine()
    set_overlap_cache(engine.edges)
    _invalidate_route_cache()
    _get_road_routing_base(engine)
    nodes = list(engine.nodes.values())
    edges = [dict(edge) for edge in engine.edges]

    scenarios = []
    for sc, cfg in SCENARIO_CONFIG.items():
        blocked = cfg.get("blocked_edges", set())
        scenarios.append({
            "id": sc.value,
            "description": cfg.get("description", ""),
            "color": cfg.get("color", "#14b8a6"),
            "edge_modifiers": cfg.get("edge_modifiers", {}),
            "blocked_edges": list(blocked) if isinstance(blocked, set) else blocked,
        })
    # Append custom scenarios
    for cs in _load_custom_scenarios():
        scenarios.append(cs)
    return jsonify({"nodes": nodes, "edges": edges, "scenarios": scenarios})

@app.route("/api/route", methods=["POST"])
def api_route():
    global engine
    body = request.get_json(force=True)
    start_id = body.get("start")
    end_id = body.get("end")
    scenario_name = body.get("scenario", "Normal")
    time_fac = float(body.get("time_factor", 1.0))

    # Update conditions di engine sebelum hitung
    conds = _load_conditions()
    engine.set_conditions(conds)

    scenario_arg = scenario_name
    for s in Scenario:
        if s.value == scenario_name:
            scenario_arg = s
            break

    scenario_value = scenario_arg.value if hasattr(scenario_arg, "value") else str(scenario_arg)
    routing_graph = _build_road_routing_graph(engine, start_id, end_id)
    if not routing_graph:
        return jsonify({
            "error": "ROAD_GRAPH_NOT_FOUND",
            "detail": "Titik awal atau tujuan belum tersambung ke jaringan jalan.",
        }), 400

    result = find_path(
        routing_graph["nodes"],
        routing_graph["edges"],
        start_id,
        end_id,
        scenario_value,
        time_fac,
        conds,
    )
    
    # Tambahkan geometry untuk visualisasi
    if "path" in result:
        edge_map = {e["id"]: e for e in routing_graph["edges"]}
        result["edges_geometry"] = [
            {"id": eid, "geometry": edge_map[eid].get("geometry", [])}
            for eid in result.get("edges_used", [])
            if eid in edge_map
        ]
        result["road_path"] = result["path"]
        result["path"] = _display_path(start_id, end_id, engine.nodes)

    return jsonify(result)

@app.route("/api/conditions", methods=["GET", "POST"])
def api_conditions():
    if request.method == "POST":
        body = request.get_json(force=True)
        # Handle individual edge update or full reset
        if "edge_id" in body:
            conds = _load_conditions()
            eid = body["edge_id"]
            status = body.get("status", "NORMAL").upper()
            if status == "NORMAL":
                conds["edge_conditions"].pop(eid, None)
            else:
                conds["edge_conditions"][eid] = {"status": status, "severity": body.get("severity", 1.0)}
            _save_conditions(conds)
            return jsonify({"ok": True, "conditions": conds})
        else:
            _save_conditions(body)
            return jsonify({"ok": True})
    return jsonify(_load_conditions())

@app.route("/api/brush_area", methods=["POST"])
def api_brush_area():
    """
    Stateless brush: detect all edges whose geometry intersects the bounding box,
    then update their conditions. NO graph splits, NO permanent modifications.
    Conditions can always be reset to NORMAL to restore original state.
    """
    body = request.get_json(force=True)
    bounds = body.get("bounds", {})
    min_lat = bounds.get("_southWest", {}).get("lat", -90)
    max_lat = bounds.get("_northEast", {}).get("lat",  90)
    min_lon = bounds.get("_southWest", {}).get("lng", -180)
    max_lon = bounds.get("_northEast", {}).get("lng",  180)
    status   = body.get("status", "NORMAL").upper()
    severity = float(body.get("severity", 1.0))

    def point_in_box(lat, lon):
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

    def segment_crosses_box(p1, p2):
        """Quick check: does a line segment p1→p2 cross the bounding box?"""
        # Check if either endpoint is inside
        if point_in_box(p1[0], p1[1]) or point_in_box(p2[0], p2[1]):
            return True
        # Simple axis-aligned bounding box overlap check
        seg_min_lat = min(p1[0], p2[0]); seg_max_lat = max(p1[0], p2[0])
        seg_min_lon = min(p1[1], p2[1]); seg_max_lon = max(p1[1], p2[1])
        return not (seg_max_lat < min_lat or seg_min_lat > max_lat or
                    seg_max_lon < min_lon or seg_min_lon > max_lon)

    conds = _load_conditions()
    edges_to_update = []

    for edge in engine.edges:
        if edge["id"].startswith("AUTO_"):
            continue
        geom = edge.get("geometry", [])

        if geom and len(geom) >= 2:
            # Check each segment of the polyline
            hit = False
            for i in range(len(geom) - 1):
                if segment_crosses_box(geom[i], geom[i+1]):
                    hit = True
                    break
            if not hit:
                continue
        else:
            # Fallback: check node positions
            fn = engine.nodes.get(edge["from"])
            tn = engine.nodes.get(edge["to"])
            if not fn or not tn:
                continue
            if not segment_crosses_box(
                [fn["lat"], fn["lon"]], [tn["lat"], tn["lon"]]
            ):
                continue

        edges_to_update.append(edge["id"])

    # Apply conditions (purely metadata, graph structure unchanged)
    for eid in edges_to_update:
        if status == "NORMAL":
            conds.setdefault("edge_conditions", {}).pop(eid, None)
        else:
            conds.setdefault("edge_conditions", {})[eid] = {
                "status": status, "severity": severity
            }

    _save_conditions(conds)
    return jsonify({"ok": True, "updated": len(edges_to_update)})

@app.route("/api/conditions/reset", methods=["POST"])
def api_reset():
    data = {"edge_conditions": {}, "edge_directions": {}}
    _save_conditions(data)
    return jsonify({"ok": True})

@app.route("/api/edges/<edge_id>/overlaps", methods=["GET"])
def api_edge_overlaps(edge_id):
    """Temukan semua jalan yang saling tindih dengan edge_id tertentu."""
    from algorithm import _find_overlapping_edges
    
    edges = [dict(edge) for edge in engine.edges]
    overlapping_ids = _find_overlapping_edges(edge_id, edges)
    
    overlapping_edges = []
    for e in edges:
        if e["id"] in overlapping_ids:
            overlapping_edges.append({
                "id": e["id"],
                "from": e.get("from"),
                "to": e.get("to"),
                "from_name": engine.nodes.get(e.get("from"), {}).get("name", ""),
                "to_name": engine.nodes.get(e.get("to"), {}).get("name", ""),
                "distance": e.get("distance"),
            })
    
    return jsonify({
        "edge_id": edge_id,
        "overlapping_count": len(overlapping_edges),
        "overlapping_edges": overlapping_edges
    })

@app.route("/api/nodes", methods=["POST"])
def api_nodes():
    body = request.get_json(force=True)
    custom = _load_custom_graph()

    lat = float(body.get("lat"))
    lon = float(body.get("lon"))

    new_node = {
        "id": _next_custom_node_id(custom),
        "name": body.get("name", "Gedung Kustom"),
        "type": body.get("type", "Gedung"),
        "lat": lat,
        "lon": lon,
    }
    custom.setdefault("nodes", []).append(new_node)

    # --- Auto-connect new building to the nearest road segment ---
    # Build a temporary engine to access all current edges/geometries
    global engine
    engine = _build_combined_engine()
    nearest = _find_nearest_graph_edge(lat, lon, engine.edges)
    connected = False
    split_edge = None
    if nearest and nearest["nearest"]["distance"] <= AUTO_CONNECT_MAX_METERS:
        split_edge = nearest["edge"]["id"]
        # Create a waypoint junction at the projected point on the road
        jct_node = {
            "id": _next_custom_node_id(custom),
            "name": f"WP_{new_node['id']}_JCT",
            "type": "Waypoint",
            "lat": nearest["nearest"]["point"][0],
            "lon": nearest["nearest"]["point"][1],
        }
        # Split the road at that junction
        split_ok = _split_edge_into_custom_segments(
            custom, nearest["edge"], jct_node, nearest["nearest"]
        )
        if split_ok:
            custom["nodes"].append(jct_node)
            # Add a connecting edge: new building → junction
            used_ids = {e.get("id") for e in custom.get("edges", [])}
            used_ids.update(custom.get("deleted_edges", []))
            conn_id = _next_custom_edge_id(custom, used_ids)
            from algorithm import haversine

            start = (lat, lon)
            end = (jct_node["lat"], jct_node["lon"])
            
            # OSRM geometry logic to connect building to the nearest path
            import json
            import urllib.request
            url = f"https://router.project-osrm.org/route/v1/foot/{start[1]},{start[0]};{end[1]},{end[0]}?overview=full&geometries=geojson"
            geom = [[start[0], start[1]], [end[0], end[1]]]
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'UNIB-Navigator-Bot'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    if data['code'] == 'Ok':
                        coords = data['routes'][0]['geometry']['coordinates']
                        geom = [[start[0], start[1]]] + [[round(c[1], 5), round(c[0], 5)] for c in coords] + [[end[0], end[1]]]
            except Exception as e:
                print(f"OSRM fallback to straight line: {e}")
                
            conn_dist = 0.0
            for i in range(len(geom)-1):
                conn_dist += haversine(geom[i][0], geom[i][1], geom[i+1][0], geom[i+1][1])
            conn_dist = round(conn_dist, 1)

            custom["edges"].append({
                "id": conn_id,
                "from": new_node["id"],
                "to": jct_node["id"],
                "distance": conn_dist,
                "surface": nearest["edge"].get("surface", "ASPHALT"),
                "bidirectional": True,
                "geometry": geom,
            })
            connected = True

    _save_custom_graph(custom)
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({
        "ok": True,
        "id": new_node["id"],
        "node": new_node,
        "connected": connected,
        "split_edge": split_edge,
    })

@app.route("/api/road-points", methods=["POST"])
def api_road_points():
    body = request.get_json(force=True)
    lat = float(body.get("lat"))
    lon = float(body.get("lon"))
    custom = _load_custom_graph()

    new_node = {
        "id": _next_custom_node_id(custom),
        "name": body.get("name") or f"WP_{len(custom.get('nodes', [])) + 1}",
        "type": "Waypoint",
        "lat": lat,
        "lon": lon,
    }

    global engine
    nearest = _find_nearest_graph_edge(lat, lon, engine.edges)
    snapped = False
    split_edge = None
    if nearest and nearest["nearest"]["distance"] <= ROAD_SNAP_MAX_METERS:
        new_node["lat"], new_node["lon"] = nearest["nearest"]["point"]
        split_edge = nearest["edge"]["id"]
        snapped = _split_edge_into_custom_segments(custom, nearest["edge"], new_node, nearest["nearest"])

    custom.setdefault("nodes", []).append(new_node)
    _save_custom_graph(custom)

    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({
        "ok": True,
        "id": new_node["id"],
        "node": new_node,
        "snapped": snapped,
        "split_edge": split_edge,
    })

@app.route("/api/nodes/<node_id>", methods=["DELETE"])
def api_delete_node(node_id):
    custom = _load_custom_graph()
    
    # Only allow deleting custom nodes
    nodes = custom.get("nodes", [])
    found_node = next((n for n in nodes if n["id"] == node_id), None)
    if not found_node:
        return jsonify({"error": "Hanya node kustom yang bisa dihapus"}), 400
    
    edges = custom.get("edges", [])
    is_waypoint = found_node.get("type") == "Waypoint"
    
    if is_waypoint:
        # Find edges connected to this waypoint
        connected = [e for e in edges if e["from"] == node_id or e["to"] == node_id]
        other_edges = [e for e in edges if e["from"] != node_id and e["to"] != node_id]
        
        if len(connected) == 2:
            # Merge two edges through waypoint: A→WP + WP→B = A→B
            e1, e2 = connected
            # Determine direction: ensure e1 ends at WP and e2 starts at WP
            if e1["to"] == node_id and e2["from"] == node_id:
                from_node, to_node = e1["from"], e2["to"]
                geom = e1.get("geometry", []) + e2.get("geometry", [])
            elif e2["to"] == node_id and e1["from"] == node_id:
                from_node, to_node = e2["from"], e1["to"]
                geom = e2.get("geometry", []) + e1.get("geometry", [])
            elif e1["from"] == node_id and e2["from"] == node_id:
                # Both start at WP — reverse e1
                from_node, to_node = e1["to"], e2["to"]
                geom = list(reversed(e1.get("geometry", []))) + e2.get("geometry", [])
            else:
                # Both end at WP — reverse e2
                from_node, to_node = e1["from"], e2["from"]
                geom = e1.get("geometry", []) + list(reversed(e2.get("geometry", [])))
            
            # Remove duplicate midpoints
            clean_geom = [geom[0]]
            for pt in geom[1:]:
                if pt != clean_geom[-1]:
                    clean_geom.append(pt)
            
            merged_edge = {
                "id": e1["id"],  # reuse first edge ID
                "from": from_node,
                "to": to_node,
                "distance": round(e1.get("distance", 0) + e2.get("distance", 0), 1),
                "surface": e1.get("surface", "ASPHALT"),
                "bidirectional": True,
                "geometry": clean_geom
            }
            other_edges.append(merged_edge)
            custom["edges"] = other_edges
        elif len(connected) == 1:
            # Only one edge — just keep it, reconnect if possible
            custom["edges"] = other_edges  # remove the single edge
        else:
            # More than 2 edges — can't merge, just remove all
            custom["edges"] = other_edges
    else:
        # Non-waypoint (building): remove edges connected to it
        custom["edges"] = [e for e in edges 
                           if e["from"] != node_id and e["to"] != node_id]
    
    # Remove the node
    custom["nodes"] = [n for n in nodes if n["id"] != node_id]
    
    _save_custom_graph(custom)
    
    global engine
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({"ok": True})

@app.route("/api/edges", methods=["POST"])
def api_edges():
    import math
    import json
    import urllib.request
    
    body = request.get_json(force=True)
    custom = _load_custom_graph()
    
    # get coordinates from engine to calculate distance
    global engine
    try:
        n1 = engine.nodes[body["from"]]
        n2 = engine.nodes[body["to"]]
        
        start = (n1["lat"], n1["lon"])
        end = (n2["lat"], n2["lon"])
        
        # OSRM geometry logic (with 5s timeout to avoid hanging)
        url = f"https://router.project-osrm.org/route/v1/foot/{start[1]},{start[0]};{end[1]},{end[0]}?overview=full&geometries=geojson"
        geom = [[start[0], start[1]], [end[0], end[1]]]
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'UNIB-Navigator-Bot'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                if data['code'] == 'Ok':
                    coords = data['routes'][0]['geometry']['coordinates']
                    geom = [[start[0], start[1]]] + [[round(c[1], 5), round(c[0], 5)] for c in coords] + [[end[0], end[1]]]
        except Exception as e:
            print(f"OSRM fallback to straight line: {e}")
            
        # Calculate path distance
        from algorithm import haversine
        distance = 0.0
        for i in range(len(geom)-1):
            distance += haversine(geom[i][0], geom[i][1], geom[i+1][0], geom[i+1][1])
        distance = round(distance, 1)
        
    except KeyError:
        return jsonify({"error": "Node not found"}), 400

    new_edge = {
        "id": _next_custom_edge_id(custom),
        "from": body["from"],
        "to": body["to"],
        "distance": body.get("distance") or distance,
        "surface": "ASPHALT",
        "bidirectional": body.get("bidirectional", True),
        "geometry": geom
    }
    
    custom.setdefault("edges", []).append(new_edge)
    _save_custom_graph(custom)
    
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({"ok": True, "edge": new_edge})

@app.route("/api/edges/<edge_id>/distance", methods=["PUT"])
def api_update_edge_distance(edge_id):
    body = request.get_json(force=True)
    new_distance = float(body.get("distance", 0))
    if new_distance <= 0:
        return jsonify({"error": "Jarak harus lebih dari 0"}), 400

    custom = _load_custom_graph()
    custom.setdefault("custom_distances", {})[edge_id] = round(new_distance, 1)
    _save_custom_graph(custom)

    global engine
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({"ok": True, "edge_id": edge_id, "distance": round(new_distance, 1)})


@app.route("/api/edges/<edge_id>/direction", methods=["PUT"])
def api_update_edge_direction(edge_id):
    """Set one-way / two-way direction for any edge (base or custom).

    Persists to:
      1. custom_graph.json  – edge_directions dict + bidirectional flag on custom edges
      2. conditions.json    – edge_directions string value consumed by algorithm._direction_for()
    """
    global engine  # declare at top to avoid SyntaxError
    body = request.get_json(force=True)
    bidirectional = bool(body.get("bidirectional", True))
    from_node = body.get("from")  # optional, determines direction for one-way
    to_node   = body.get("to")    # optional

    # ── 1. custom_graph.json ──────────────────────────────────────────────
    custom = _load_custom_graph()
    custom.setdefault("edge_directions", {})
    custom["edge_directions"][edge_id] = {
        "bidirectional": bidirectional,
        "from": from_node,
        "to":   to_node,
    }
    for e in custom.get("edges", []):
        if e["id"] == edge_id:
            e["bidirectional"] = bidirectional
            if from_node:
                e["from"] = from_node
            if to_node:
                e["to"] = to_node
    _save_custom_graph(custom)

    # ── 2. conditions.json (string format read by _direction_for) ─────────
    conds = _load_conditions()
    conds.setdefault("edge_directions", {})
    if bidirectional:
        # Remove override → algorithm falls back to default (TWO_WAY)
        conds["edge_directions"].pop(edge_id, None)
    else:
        # Find the natural from-node of this edge in the engine
        natural_from = next(
            (e.get("from") for e in engine.edges if e.get("id") == edge_id),
            None,
        )
        if from_node and natural_from and from_node != natural_from:
            conds["edge_directions"][edge_id] = "ONE_WAY_REVERSE"
        else:
            conds["edge_directions"][edge_id] = "ONE_WAY_FORWARD"
    _save_conditions(conds)

    engine = _build_combined_engine()
    _invalidate_route_cache()
    direction_label = "Dua Arah" if bidirectional else "Satu Arah"
    return jsonify({"ok": True, "edge_id": edge_id, "bidirectional": bidirectional, "direction": direction_label})



@app.route("/api/nodes/<node_id>/location", methods=["PUT"])
def api_update_node_location(node_id):
    body = request.get_json(force=True)
    lat = float(body.get("lat"))
    lon = float(body.get("lon"))

    custom = _load_custom_graph()
    global engine
    
    found = False
    for n in custom.setdefault("nodes", []):
        if n["id"] == node_id:
            n["lat"] = lat
            n["lon"] = lon
            found = True
            break
            
    if not found:
        if node_id in engine.nodes:
            base_n = engine.nodes[node_id].copy()
            base_n["lat"] = lat
            base_n["lon"] = lon
            custom["nodes"].append(base_n)
        else:
            return jsonify({"error": "Node tidak ditemukan"}), 404

    from algorithm import haversine
    def update_edge_endpoints(edges_list):
        for e in edges_list:
            if e.get("geometry") and len(e["geometry"]) >= 2:
                if e["from"] == node_id:
                    e["geometry"][0] = [lat, lon]
                if e["to"] == node_id:
                    e["geometry"][-1] = [lat, lon]
                dist = 0.0
                geom = e["geometry"]
                for i in range(len(geom)-1):
                    dist += haversine(geom[i][0], geom[i][1], geom[i+1][0], geom[i+1][1])
                e["distance"] = round(dist, 1)

    update_edge_endpoints(custom.setdefault("edges", []))
    
    base_edges_to_override = []
    existing_custom_ids = {ce["id"] for ce in custom["edges"]}
    for e in engine.edges:
        if e["id"] not in existing_custom_ids:
            if e["from"] == node_id or e["to"] == node_id:
                e_copy = dict(e)
                if e_copy.get("geometry"):
                    e_copy["geometry"] = [[p[0], p[1]] for p in e_copy["geometry"]]
                base_edges_to_override.append(e_copy)
                
    update_edge_endpoints(base_edges_to_override)
    custom["edges"].extend(base_edges_to_override)

    _save_custom_graph(custom)
    
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({"ok": True})

@app.route("/api/edges/<edge_id>", methods=["DELETE"])
def api_delete_edge(edge_id):
    custom = _load_custom_graph()
    custom.setdefault("deleted_edges", []).append(edge_id)
    _save_custom_graph(custom)
    
    global engine
    engine = _build_combined_engine()
    _invalidate_route_cache()
    return jsonify({"ok": True})

@app.route("/api/scenarios", methods=["GET", "POST", "DELETE"])
def api_scenarios():
    if request.method == "POST":
        body = request.get_json(force=True)
        name = body.get("name", "").strip()
        if not name:
            return jsonify({"error": "Nama skenario wajib diisi"}), 400
        custom = _load_custom_scenarios()
        # Check duplicate
        existing_ids = [s.value for s in Scenario] + [c["id"] for c in custom]
        if name in existing_ids:
            return jsonify({"error": f"Skenario '{name}' sudah ada"}), 400
        new_sc = {
            "id": name,
            "description": body.get("description", ""),
            "color": body.get("color", "#14b8a6"),
            "edge_modifiers": body.get("edge_modifiers", {}),
            "blocked_edges": body.get("blocked_edges", []),
        }
        custom.append(new_sc)
        _save_custom_scenarios(custom)
        # Register in algorithm engine
        from algorithm import SCENARIO_MODIFIERS, SCENARIO_BLOCKED
        SCENARIO_MODIFIERS[name] = new_sc["edge_modifiers"]
        SCENARIO_BLOCKED[name] = set(new_sc["blocked_edges"])
        return jsonify({"ok": True, "scenario": new_sc})
    elif request.method == "DELETE":
        body = request.get_json(force=True)
        sid = body.get("id", "")
        custom = _load_custom_scenarios()
        custom = [c for c in custom if c["id"] != sid]
        _save_custom_scenarios(custom)
        
        global engine
        engine = _build_combined_engine()
        set_overlap_cache(engine.edges)
        _invalidate_route_cache()
        
        return jsonify({"ok": True})
    return jsonify(_load_custom_scenarios())

if __name__ == "__main__":
    print("SERVER READY: Menggunakan Algoritma A* Lokal (UNIB)")
    app.run(debug=True, port=5000)

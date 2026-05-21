"""
campus_data.py - UNIB 100% Precision Road Geometry.
Koordinat Polylines diambil langsung dari dataset ELOKGALO untuk akurasi sempurna.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple


class NodeType(Enum):
    ENTRY    = "Gerbang"
    BUILDING = "Gedung"
    FACILITY = "Fasilitas"
    PARKING  = "Parkir"
    OPEN     = "Area terbuka"


class SurfaceType(Enum):
    ASPHALT  = ("Aspal",  1.0)
    CONCRETE = ("Beton",  1.1)
    DIRT     = ("Tanah",  1.4)
    STAIRS   = ("Tangga", 1.8)
    RAMP     = ("Ramp",   1.3)
    def __init__(self, label: str, multiplier: float):
        self.label = label
        self.multiplier = multiplier


class Scenario(Enum):
    NORMAL      = "Normal"


@dataclass
class CampusNode:
    id: str; name: str; node_type: NodeType
    lat: float; lon: float
    x: float = 0; y: float = 0


@dataclass
class CampusEdge:
    id: str; from_node: str; to_node: str; distance: float
    surface: SurfaceType = field(default=SurfaceType.ASPHALT)
    is_accessible: bool = True
    is_bidirectional: bool = True
    geometry: List[Tuple[float, float]] = field(default_factory=list)
    @property
    def base_weight(self) -> float:
        return self.distance * self.surface.multiplier


# ---------------------------------------------------------------------------
# Node – Seluruh 29 Landmark Kampus
# ---------------------------------------------------------------------------
CAMPUS_NODES: List[CampusNode] = []

NODE_BY_ID: Dict[str, CampusNode] = {n.id: n for n in CAMPUS_NODES}

def _seg(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0]-a[0]), math.radians(b[1]-a[1])
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(h), math.sqrt(1-h))

def _pdist(pts):
    return round(sum(_seg(pts[i], pts[i+1]) for i in range(len(pts)-1)), 1)

def edge(eid, fn, tn, surface=SurfaceType.ASPHALT, bidirectional=True, via=None):
    # Geometri diambil dari koordinat nyata OSM
    start = (NODE_BY_ID[fn].lat, NODE_BY_ID[fn].lon)
    end = (NODE_BY_ID[tn].lat, NODE_BY_ID[tn].lon)
    full_geom = [start] + (via or []) + [end]
    return CampusEdge(eid, fn, tn, _pdist(full_geom), surface, True, bidirectional, full_geom)

CAMPUS_EDGES: List[CampusEdge] = []

SCENARIO_CONFIG: Dict[Scenario, dict] = {
    Scenario.NORMAL: {
        "description": "Kondisi kampus hari biasa.",
        "color": "#0f766e", "edge_modifiers": {}, "blocked_edges": set(),
    },
}

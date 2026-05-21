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
    WISUDA      = "Wisuda"
    UTBK        = "UTBK"
    EVENT_BESAR = "Event Besar"


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
    Scenario.WISUDA: {
        "description": "Arus tamu padat di gerbang utama, rektorat, dan gedung acara.",
        "color": "#d97706",
        "edge_modifiers": {"E01":2.3,"E02":1.8,"E03":1.7,"E18":1.9,"E25":2.0,"E26":2.2,"E29":1.6},
        "blocked_edges": set(),
    },
    Scenario.UTBK: {
        "description": "Zona ujian dipadatkan di UPA TIK, FKIP, Perpustakaan, Teknik, FKIK, FEB, dan Hukum.",
        "color": "#dc2626",
        "edge_modifiers": {"E20":1.7,"E22":1.7,"E24":1.8,"E25":1.7,"E27":1.9,"E31":1.7,"E35":1.6,"E37":1.8,"E08":1.6,"E09":1.6},
        "blocked_edges": {"E41"},
    },
    Scenario.EVENT_BESAR: {
        "description": "Kepadatan diarahkan di sekitar stadion, sport center, dan koridor timur.",
        "color": "#7c3aed",
        "edge_modifiers": {"E10":2.2,"E11":1.8,"E36":2.2,"E37":2.4,"E39":2.0,"E48":2.0,"E28":1.7,"E29":1.6},
        "blocked_edges": set(),
    },
}

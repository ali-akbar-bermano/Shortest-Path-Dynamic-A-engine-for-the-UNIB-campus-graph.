// ── State ──────────────────────────────────────────────────
let graph = null,
  mapMode = null,
  addEdgeFrom = null,
  routeLayer = null;
let edgeLayers = {},
  nodeLayers = {},
  conditions = {},
  nodeById = {},
  edgeById = {};
let isDrawing = false,
  drawEdgeId = null,
  drawPoints = [],
  drawPolyline = null;
let drawTempLine = null,
  drawDots = [],
  drawSnapLine = null;
let pickRouteFrom = null; // for pick_route mode

// ── Map ────────────────────────────────────────────────────
const map = L.map("map", { doubleClickZoom: false }).setView(
  [-3.759, 102.2725],
  16,
);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap",
  maxZoom: 19,
}).addTo(map);

map.on("click", async (e) => {
  if (isDrawing) {
    addDrawPoint(e.latlng);
    return;
  }
  // Add node (building)
  if (mapMode === "add_node") {
    const name = prompt("Masukkan nama gedung/titik baru:");
    if (!name) return;
    let type = prompt("Masukkan jenis (Gedung / Gerbang / Fasilitas / Parkir):", "Gedung");
    if (!type) type = "Gedung";
    
    setStepIndicator("⏳ Menambahkan titik…");
    try {
      const r = await fetch("/api/nodes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          lat: e.latlng.lat,
          lon: e.latlng.lng,
          type: type,
        }),
      });
      const d = await r.json();
      if (d.ok) {
        if (d.connected) {
          showAlert(
            `${type} "${name}" (${d.id}) ditambahkan & tersambung ke jalan ${d.split_edge}! 🎉`,
            "success",
          );
        } else {
          showAlert(
            `${type} "${name}" (${d.id}) ditambahkan. Tidak ada jalan terdekat — tambahkan jalan manual dengan 🔗 Tambah Jalan.`,
            "info",
          );
        }
        await loadGraph();
        setStepIndicator("🏢 Klik peta untuk tambah titik lain");
      } else {
        showAlert(d.error || "Gagal menambah titik", "error");
        setStepIndicator("🏢 Klik peta untuk tambah Gedung");
      }
    } catch (err) {
      showAlert("Gagal menambah titik: " + err.message, "error");
      setStepIndicator("🏢 Klik peta untuk tambah Gedung");
    }
    return;
  }
  // Add edge from map click — auto-create waypoint
  if (mapMode === "add_edge") {
    const lat = e.latlng.lat,
      lon = e.latlng.lng;
    // Check if near an existing node (snap threshold ~30m)
    let nearNode = null,
      nearDist = Infinity;
    if (graph && graph.nodes) {
      for (const n of graph.nodes) {
        try {
          const d = map
            .latLngToLayerPoint([n.lat, n.lon])
            .distanceTo(map.latLngToLayerPoint(e.latlng));
          if (d < 25 && d < nearDist) {
            nearDist = d;
            nearNode = n;
          }
        } catch (err) {
          console.warn("Error checking nearby node:", err);
        }
      }
    }
    if (nearNode) {
      handleAddEdgeNode(nearNode.id, nearNode.name);
    } else {
      setStepIndicator("⏳ Membuat titik jalan…");
      try {
        const d = await createRoadPoint(lat, lon);
        if (d.ok) {
          await loadGraph();
          handleAddEdgeNode(d.id, d.node?.name || d.id);
          const snapMsg = d.snapped ? ` (tersambung ke ${d.split_edge})` : "";
          showAlert(
            `✅ Titik jalan pertama dibuat${snapMsg}. Klik lokasi kedua untuk membuat jalan.`,
            "success",
          );
        } else {
          showAlert(d.error || "Gagal membuat titik jalan", "error");
          setStepIndicator("📍 Klik untuk buat titik jalan pertama");
        }
      } catch (err) {
        console.error("Error creating road point:", err);
        showAlert("Gagal membuat titik jalan: " + err.message, "error");
        setStepIndicator("📍 Klik untuk buat titik jalan pertama");
      }
    }
    return;
  }
});
map.on("dblclick", (e) => {
  if (isDrawing) {
    L.DomEvent.stopPropagation(e);
    finishDraw();
  }
});
map.on("contextmenu", (e) => {
  // Right-click cancels chain mode
  if (mapMode === "add_edge" && addEdgeFrom) {
    L.DomEvent.preventDefault(e);
    addEdgeFrom = null;
    setStepIndicator("📍 Klik titik pertama (gedung, ruas jalan, atau peta)");
    showAlert("Chain dibatalkan", "info");
  }
});
map.on("mousemove", (e) => {
  if (!isDrawing || drawPoints.length === 0) return;
  if (drawTempLine) map.removeLayer(drawTempLine);
  drawTempLine = L.polyline([drawPoints[drawPoints.length - 1], e.latlng], {
    color: "#f59e0b",
    weight: 2.5,
    dashArray: "8,6",
    opacity: 0.8,
  }).addTo(map);
});

// ── Clock ──────────────────────────────────────────────────
function updateClock() {
  document.getElementById("clock").textContent = new Date().toLocaleTimeString(
    "id-ID",
    { hour: "2-digit", minute: "2-digit", second: "2-digit" },
  );
}
updateClock();
setInterval(updateClock, 1000);

// ── Tabs ───────────────────────────────────────────────────
function showTab(name, btn) {
  document
    .querySelectorAll(".tab-panel")
    .forEach((p) => p.classList.remove("active"));
  document
    .querySelectorAll(".tab-btn")
    .forEach((b) => b.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
  btn.classList.add("active");
  if (name === "jalan") refreshCondTable();
  if (name === "skenario") renderScenarioCards();
}

// ── Alert ──────────────────────────────────────────────────
function showAlert(msg, type = "info") {
  const el = document.getElementById("alert");
  el.className = "alert-" + type;
  el.textContent = msg;
  el.style.display = "block";
  if (type !== "error") setTimeout(() => (el.style.display = "none"), 4000);
}

// ── Icons ──────────────────────────────────────────────────
function nodeIcon(type, isStart, isEnd) {
  const lowerType = (type || "").toLowerCase().trim();
  // Hide waypoint markers entirely (case-insensitive type check)
  if (lowerType === "waypoint") {
    return L.divIcon({ html: "", className: "", iconSize: [0, 0] });
  }
  let bg = "#1e40af",
    emoji = "🏢";
  if (lowerType === "gerbang") {
    bg = "#d97706";
    emoji = "🚪";
  } else if (lowerType === "fasilitas") {
    bg = "#0d9488";
    emoji = "⚙";
  } else if (lowerType === "area terbuka" || lowerType === "area") {
    bg = "#059669";
    emoji = "🌿";
  } else if (lowerType === "parkir") {
    bg = "#6366f1";
    emoji = "P";
  }
  if (isStart) {
    bg = "#f59e0b";
    emoji = "🟢";
  }
  if (isEnd) {
    bg = "#ef4444";
    emoji = "🔴";
  }
  return L.divIcon({
    html: `<div style="background:${bg};width:28px;height:28px;border-radius:50%;
      display:flex;align-items:center;justify-content:center;font-size:13px;
      border:2px solid rgba(255,255,255,.4);box-shadow:0 2px 8px rgba(0,0,0,.4)">${emoji}</div>`,
    className: "",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function edgeColor(id) {
  const ec = (conditions.edge_conditions || {})[id];
  if (ec) {
    const s = (ec.status || ec.type || "NORMAL").toUpperCase();
    if (s === "CLOSED") return "#ef4444";
    if (s === "BUSY") return "#f59e0b";
    if (s === "POTHOLE") return "#f97316";
    if (s === "CUSTOM") return "#60a5fa";
    if (s === "CONSTRUCTION") return "#a855f7";
  }
  // Check scenario modifiers
  const selSc = document.getElementById("sel-scenario");
  if (selSc && graph) {
    const sc = graph.scenarios.find((s) => s.id === selSc.value);
    if (sc) {
      if ((sc.blocked_edges || []).includes(id)) return "#ef4444";
      if (sc.edge_modifiers && sc.edge_modifiers[id])
        return sc.color || "#d97706";
    }
  }
  return "#38bdf8";
}

// ── Load Graph ─────────────────────────────────────────────
async function loadGraph() {
  try {
    const [gR, cR] = await Promise.all([
      fetch("/api/graph"),
      fetch("/api/conditions"),
    ]);
    graph = await gR.json();
    conditions = await cR.json();

    // Ensure graph has required properties
    if (!graph) graph = { nodes: [], edges: [], scenarios: [] };
    if (!graph.nodes) graph.nodes = [];
    if (!graph.edges) graph.edges = [];
    if (!graph.scenarios) graph.scenarios = [];

    nodeById = {};
    edgeById = {};
    graph.nodes.forEach((n) => (nodeById[n.id] = n));
    graph.edges.forEach((e) => (edgeById[e.id] = e));
    populateSelects();
    populateScenarios();
    drawEdges();
    drawNodes();
    renderScenarioCards();
  } catch (e) {
    console.error("Load failed:", e);
    // Initialize empty graph on error
    graph = { nodes: [], edges: [], scenarios: [] };
  }
}

function populateSelects() {
  const s = document.getElementById("sel-start"),
    e = document.getElementById("sel-end");
  if (!s || !e) return;

  const prevStart = s.value || "G1",
    prevEnd = e.value || "RK";
  const buildings = (graph?.nodes || []).filter(
    (n) => n.type !== "Waypoint" && (!n.name || !n.name.startsWith("WP_")),
  );
  [s, e].forEach((sel) => {
    sel.innerHTML = "";
    buildings.forEach((n) => {
      const o = document.createElement("option");
      o.value = n.id;
      o.textContent = `${n.id} – ${n.name}`;
      sel.appendChild(o);
    });
  });
  const buildingIds = new Set(buildings.map((n) => n.id));
  s.value = buildingIds.has(prevStart)
    ? prevStart
    : buildingIds.has("G1")
      ? "G1"
      : buildings[0]?.id || "";
  e.value = buildingIds.has(prevEnd)
    ? prevEnd
    : buildingIds.has("RK")
      ? "RK"
      : buildings[1]?.id || buildings[0]?.id || "";
}

function populateScenarios() {
  const sel = document.getElementById("sel-scenario");
  if (!sel) return;

  const prevScenario = sel.value || "Normal";
  sel.innerHTML = "";
  (graph?.scenarios || []).forEach((sc) => {
    const o = document.createElement("option");
    o.value = sc.id;
    o.textContent = sc.id;
    sel.appendChild(o);
  });

  const info = document.getElementById("scenario-info");
  if (!info || !graph || !graph.scenarios) return;

  if ((graph.scenarios || []).some((sc) => sc.id === prevScenario)) {
    sel.value = prevScenario;
  }

  const sc = graph.scenarios.find((s) => s.id === sel.value);
  const descEl = info.querySelector(".si-desc");
  if (descEl) descEl.textContent = sc ? sc.description : "—";

  // Show affected edges
  const affDiv = document.getElementById("si-affected");
  const affList = document.getElementById("si-affected-list");
  if (!affDiv || !affList) return;
  const mods = sc?.edge_modifiers || {};
  const blocked = sc?.blocked_edges || [];
  if (Object.keys(mods).length === 0 && blocked.length === 0) {
    affDiv.style.display = "none";
    return;
  }
  affDiv.style.display = "block";
  let html = "";
  for (const [eid, mul] of Object.entries(mods)) {
    const e = edgeById[eid];
    const label = e
      ? `${eid} (${nodeById[e.from]?.name?.substring(0, 12) || e.from} → ${nodeById[e.to]?.name?.substring(0, 12) || e.to})`
      : eid;
    html += `<span class="si-tag si-tag-mod">×${mul} ${label}</span> `;
  }
  for (const eid of blocked) {
    html += `<span class="si-tag si-tag-block">🚫 ${eid}</span> `;
  }
  affList.innerHTML = html;
  // Redraw edges with scenario colors
  if (graph) drawEdges();
}

function drawEdges() {
  Object.values(edgeLayers).forEach((l) => map.removeLayer(l));
  edgeLayers = {};

  if (!graph || !graph.edges || graph.edges.length === 0) return;

  const selSc = document.getElementById("sel-scenario");
  const sc = (graph.scenarios || []).find(
    (s) => s.id === (selSc ? selSc.value : "Normal"),
  );
  const mods = sc?.edge_modifiers || {};
  const blocked = sc?.blocked_edges || [];
  graph.edges.forEach((e) => {
    const ll = e.geometry.map((p) => [p[0], p[1]]);
    const c = edgeColor(e.id);
    const isBlocked = blocked.includes(e.id);
    const isMod = !!mods[e.id];
    // Unified styling: all roads same thickness and opacity, except when blocked
    const w = isBlocked ? 5 : isMod ? 4.5 : 3.5;
    const op = isBlocked ? 0.35 : isMod ? 0.75 : 0.6;
    const dash = isBlocked ? "8,6" : null;
    const opts = { color: c, weight: w, opacity: op, smoothFactor: 0.5 };
    if (dash) opts.dashArray = dash;
    const line = L.polyline(ll, opts).addTo(map);
    // Invisible wider click target for easier clicking
    const hitLine = L.polyline(ll, {
      color: "#000",
      weight: 18,
      opacity: 0.001,
      interactive: true,
      smoothFactor: 0.5,
    }).addTo(map);
    const ttMod = isMod ? ` ⚠×${mods[e.id]}` : "";
    const ttBlock = isBlocked ? " 🚫DITUTUP" : "";
    const handleClick = (ev) => onEdgeClick(e, ev);
    line.on("click", handleClick);
    hitLine.on("click", handleClick);
    hitLine.on("mouseover", () => {
      if (!mapMode || mapMode === "edit_road")
        line.setStyle({ weight: 8, opacity: 1 });
    });
    hitLine.on("mouseout", () => line.setStyle({ weight: w, opacity: op }));
    line.on("mouseover", () => {
      if (!mapMode || mapMode === "edit_road")
        line.setStyle({ weight: 8, opacity: 1 });
    });
    line.on("mouseout", () => line.setStyle({ weight: w, opacity: op }));
    hitLine.bindTooltip(`${e.id}: ${e.from} → ${e.to}${ttMod}${ttBlock}`, {
      sticky: true,
    });
    line.bindTooltip(`${e.id}: ${e.from} → ${e.to}${ttMod}${ttBlock}`, {
      sticky: true,
    });
    edgeLayers[e.id] = line;
    edgeLayers[e.id + "_hit"] = hitLine;
  });
}

function drawNodes() {
  Object.values(nodeLayers).forEach((l) => map.removeLayer(l));
  nodeLayers = {};

  if (!graph || !graph.nodes || graph.nodes.length === 0) return;

  const start = document.getElementById("sel-start").value;
  const end = document.getElementById("sel-end").value;
  graph.nodes.forEach((n) => {
    // Skip waypoint nodes — they're only for routing, not displayed on map
    // Case-insensitive check + WP_ name prefix check
    const ntype = (n.type || "").toLowerCase();
    if (ntype === "waypoint" || (n.name && n.name.startsWith("WP_"))) return;
    const m = L.marker([n.lat, n.lon], {
      icon: nodeIcon(n.type, n.id === start, n.id === end),
    }).addTo(map);
    const isCustom = n.id.startsWith("C");
    const deleteBtn = isCustom
      ? `<button onclick="deleteNode('${n.id}')" style="margin-top:6px;padding:3px 10px;background:#ef4444;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px">🗑 Hapus</button>`
      : "";
    const moveBtn = `<button onclick="enableNodeDrag('${n.id}')" style="margin-top:6px;padding:3px 10px;background:#38bdf8;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;margin-right:4px">📍 Pindah Lokasi</button>`;
    m.bindPopup(
      `<b style="color:#5eead4">${n.id}</b><br>${n.name}<br><small style="color:#7fb4ab">${n.type}</small><br>${moveBtn}${deleteBtn}`,
    );
    m.on("click", async () => {
      // ── Pick Route mode ──
      if (mapMode === "pick_route") {
        if (!pickRouteFrom) {
          pickRouteFrom = n.id;
          document.getElementById("sel-start").value = n.id;
          drawNodes();
          setStepIndicator(`🟢 ${n.name} — klik gedung tujuan`);
          showAlert(`Awal: ${n.name}. Klik gedung tujuan.`, "info");
        } else {
          if (pickRouteFrom !== n.id) {
            document.getElementById("sel-end").value = n.id;
            drawNodes();
            setStepIndicator("⏳ Menghitung rute…");
            document
              .querySelectorAll(".tab-panel")
              .forEach((p) => p.classList.remove("active"));
            document
              .querySelectorAll(".tab-btn")
              .forEach((b) => b.classList.remove("active"));
            document.getElementById("tab-route").classList.add("active");
            document.querySelectorAll(".tab-btn")[0].classList.add("active");
            await findRoute();
            setStepIndicator("✅ Rute ditemukan — klik gedung untuk rute baru");
          }
          pickRouteFrom = null;
        }
        return;
      }
      // ── Add Edge mode ──
      if (mapMode === "add_edge") {
        handleAddEdgeNode(n.id, n.name);
        return;
      }
    });
    nodeLayers[n.id] = m;
  });
}

function enableNodeDrag(id) {
  const m = nodeLayers[id];
  if (!m) return;
  m.closePopup();
  m.dragging.enable();
  showAlert(
    "Geser ikon gedung/titik ke lokasi baru, lalu lepaskan untuk menyimpan.",
    "info",
  );
  m.once("dragend", async function (e) {
    m.dragging.disable();
    const pos = m.getLatLng();
    if (confirm("Simpan lokasi baru untuk titik ini?")) {
      setStepIndicator("⏳ Menyimpan lokasi baru...");
      try {
        const r = await fetch(`/api/nodes/${id}/location`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat: pos.lat, lon: pos.lng }),
        });
        const d = await r.json();
        if (d.ok) {
          showAlert(`Lokasi titik ${id} berhasil diperbarui.`, "success");
        } else {
          showAlert(d.error || "Gagal mengubah lokasi", "error");
        }
      } catch (err) {
        showAlert("Error: " + err.message, "error");
      }
    }
    // Refresh to update edge geometries properly
    await loadGraph();
    setStepIndicator("");
  });
}

// Handle adding edge from a node (building or waypoint)
// Chain mode: after creating A→B, auto-continue from B
// State untuk modal tambah jalan
let _addEdgePendingTo = null;
let _addEdgePendingToName = null;

async function handleAddEdgeNode(nodeId, nodeName) {
  if (!addEdgeFrom) {
    addEdgeFrom = nodeId;
    setStepIndicator(`✅ ${nodeName} dipilih — klik titik tujuan`);
    showAlert(
      `Titik awal: <strong>${nodeName}</strong>. Sekarang klik titik tujuan.`,
      "info",
    );
    // Highlight selected node visually
    if (nodeLayers[nodeId]) nodeLayers[nodeId].setZIndexOffset(1000);
  } else {
    if (addEdgeFrom === nodeId) {
      showAlert("Titik awal dan tujuan tidak boleh sama!", "error");
      return;
    }
    // Show confirmation modal
    const fromName = String(nodeById[addEdgeFrom]?.name || addEdgeFrom || "").substring(
      0,
      30,
    );
    const toName = String(nodeName || nodeId || "").substring(0, 30);
    _addEdgePendingTo = nodeId;
    _addEdgePendingToName = nodeName;
    document.getElementById("add-edge-modal-desc").innerHTML =
      `<strong style="color:#e2e8f0">${fromName}</strong> → <strong style="color:#e2e8f0">${toName}</strong>`;
    document.getElementById("add-edge-distance-input").value = "";
    document.getElementById("add-edge-dir-input").value = "true";
    const modal = document.getElementById("add-edge-modal");
    modal.style.display = "flex";
  }
}

function cancelAddEdgeModal() {
  document.getElementById("add-edge-modal").style.display = "none";
  _addEdgePendingTo = null;
  _addEdgePendingToName = null;
  // Keep addEdgeFrom so user can try a different target
  setStepIndicator(
    `📍 Klik titik tujuan dari ${nodeById[addEdgeFrom]?.name || addEdgeFrom}`,
  );
}

async function confirmAddEdge() {
  if (!addEdgeFrom || !_addEdgePendingTo) return;
  const distInput = document.getElementById("add-edge-distance-input").value;
  const bidirectional =
    document.getElementById("add-edge-dir-input").value === "true";
  document.getElementById("add-edge-modal").style.display = "none";

  const fromNode = addEdgeFrom;
  const toNode = _addEdgePendingTo;
  const toName = _addEdgePendingToName;
  _addEdgePendingTo = null;
  _addEdgePendingToName = null;

  setStepIndicator("⏳ Membuat jalan…");
  try {
    const body = { from: fromNode, to: toNode, bidirectional };
    if (distInput && parseFloat(distInput) > 0) {
      body.distance = parseFloat(distInput);
    }
    const r = await fetch("/api/edges", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      showAlert(
        `✅ Jalan ditambahkan! (${d.edge?.distance ?? "?"} m). Klik titik berikutnya untuk lanjut, atau klik kanan untuk batal.`,
        "success",
      );
      await loadGraph();
      // Chain: continue from last node
      addEdgeFrom = toNode;
      setStepIndicator(
        `🔗 ${toName} — klik titik berikutnya (atau klik kanan untuk batal)`,
      );
    } else {
      showAlert(d.error || "Gagal menambah jalan", "error");
      addEdgeFrom = null;
      setStepIndicator("📍 Klik titik pertama");
    }
  } catch (err) {
    showAlert("Gagal menambah jalan: " + err.message, "error");
    addEdgeFrom = null;
    setStepIndicator("📍 Klik titik pertama");
  }
}

function setStepIndicator(t) {
  const el = document.getElementById("edge-step-indicator");
  if (el) el.querySelector(".esi-text").textContent = t;
}

async function createRoadPoint(lat, lon) {
  // Auto-create waypoint at map click location
  try {
    const r = await fetch("/api/road-points", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat,
        lon,
        name: null, // auto-generate name
      }),
    });
    const d = await r.json();
    if (d.ok) {
      return {
        ok: true,
        id: d.id,
        node: d.node,
        snapped: d.snapped,
        split_edge: d.split_edge,
      };
    } else {
      return { ok: false, error: d.error || "Gagal membuat titik jalan" };
    }
  } catch (err) {
    return { ok: false, error: "Gagal membuat titik jalan: " + err.message };
  }
}

// ── Map Mode ───────────────────────────────────────────────
function setMapMode(mode) {
  if (isDrawing) cancelDraw();
  closeEdgeEditor();
  mapMode = mapMode === mode ? null : mode;
  const ind = document.getElementById("edge-step-indicator");
  document
    .querySelectorAll(".map-tool-btn")
    .forEach((b) => b.classList.remove("active"));

  if (mapMode === "add_edge") {
    const btn = document.getElementById("btn-add-edge");
    if (btn) btn.classList.add("active");
    ind && ind.classList.add("visible");
    addEdgeFrom = null;
    setStepIndicator("📍 Klik untuk buat titik jalan pertama");
    showAlert(
      "Mode Tambah Jalan: Klik peta untuk buat titik jalan, atau klik gedung yang sudah ada.",
      "info",
    );
  } else if (mapMode === "edit_road") {
    const btn = document.getElementById("btn-edit-road");
    if (btn) btn.classList.add("active");
    ind && ind.classList.add("visible");
    setStepIndicator("✏ Klik garis jalan mana saja untuk edit bobot & arah");
    showAlert("Klik ruas jalan di peta atau baris pada tabel Jalan", "info");
  } else if (mapMode === "add_node") {
    const btn = document.getElementById("btn-add-node");
    if (btn) btn.classList.add("active");
    ind && ind.classList.add("visible");
    setStepIndicator("🏢 Klik peta untuk tambah Gedung");
    showAlert("Klik lokasi gedung baru di peta", "info");
  } else {
    // Mode dinonaktifkan
    ind && ind.classList.remove("visible");
    addEdgeFrom = null;
  }
  map.getContainer().style.cursor = mapMode ? "crosshair" : "";
}

// ── Edge Editor Panel ─────────────────────────────────────
let editingEdgeId = null;
function onEdgeClick(edge, ev) {
  // In add_edge mode, forward click to the add-edge handler instead of blocking
  if (mapMode === "add_edge") {
    let lat, lon;
    if (ev && ev.latlng) {
      lat = ev.latlng.lat;
      lon = ev.latlng.lng;
    } else {
      const geom = edge.geometry || [];
      if (geom.length > 0) {
        const midIdx = Math.floor(geom.length / 2);
        lat = geom[midIdx][0];
        lon = geom[midIdx][1];
      } else {
        return;
      }
    }
    
    // Check if near an existing node (snap threshold ~25px)
    let nearNode = null, nearDist = Infinity;
    if (graph && graph.nodes) {
      for (const n of graph.nodes) {
        try {
          const d = map.latLngToLayerPoint([n.lat, n.lon])
            .distanceTo(map.latLngToLayerPoint([lat, lon]));
          if (d < 25 && d < nearDist) { nearDist = d; nearNode = n; }
        } catch (err) { /* skip */ }
      }
    }
    if (nearNode) {
      handleAddEdgeNode(nearNode.id, nearNode.name);
    } else {
      // Create a road point at this location
      (async () => {
        setStepIndicator("⏳ Membuat titik jalan…");
        try {
          const d = await createRoadPoint(lat, lon);
          if (d.ok) {
            await loadGraph();
            handleAddEdgeNode(d.id, d.node?.name || d.id);
            const snapMsg = d.snapped ? ` (tersambung ke ${d.split_edge})` : "";
            showAlert(`✅ Titik jalan dibuat${snapMsg}. Klik lokasi berikutnya.`, "success");
          } else {
            showAlert(d.error || "Gagal membuat titik jalan", "error");
            setStepIndicator("📍 Klik titik pertama");
          }
        } catch (err) {
          console.error("Error creating road point:", err);
          showAlert("Gagal membuat titik jalan: " + err.message, "error");
          setStepIndicator("📍 Klik titik pertama");
        }
      })();
    }
    return;
  }
  // Block only when actively placing nodes or picking route
  if (mapMode === "add_node" || mapMode === "pick_route") return;
  openEdgeEditor(edge);
}

async function openEdgeEditor(edge) {
  editingEdgeId = edge.id;
  const ec = (conditions.edge_conditions || {})[edge.id];
  const cur = ec ? ec.status || ec.type || "NORMAL" : "NORMAL";
  const sev = ec?.severity ?? 1.0;
  const isBidir = edge.bidirectional !== false; // default true
  const fromName = (nodeById[edge.from]?.name || edge.from).substring(0, 22);
  const toName = (nodeById[edge.to]?.name || edge.to).substring(0, 22);
  const panel = document.getElementById("edge-editor");

  // Fetch overlapping edges
  let overlappingEdges = [];
  try {
    const r = await fetch(`/api/edges/${edge.id}/overlaps`);
    const data = await r.json();
    overlappingEdges = data.overlapping_edges || [];
  } catch (e) {
    console.error("Failed to fetch overlaps:", e);
  }

  // Build node options for direction selectors (only non-waypoint nodes)
  const buildingNodes = graph.nodes.filter((n) => n.type !== "Waypoint");
  const optFrom = buildingNodes
    .map(
      (n) =>
        `<option value="${n.id}" ${n.id === edge.from ? "selected" : ""}>${n.id} – ${n.name.substring(0, 18)}</option>`,
    )
    .join("");
  const optTo = buildingNodes
    .map(
      (n) =>
        `<option value="${n.id}" ${n.id === edge.to ? "selected" : ""}>${n.id} – ${n.name.substring(0, 18)}</option>`,
    )
    .join("");

  const statusColor =
    {
      NORMAL: "#22c55e",
      BUSY: "#f59e0b",
      POTHOLE: "#f97316",
      CLOSED: "#ef4444",
      CUSTOM: "#a78bfa",
    }[cur] || "#22c55e";

  // Build overlapping edges section
  let overlappingHtml = "";
  if (overlappingEdges.length > 0) {
    overlappingHtml = `
      <div class="ee-section" style="background:#1e1b4b;border:1.5px solid #818cf8;border-radius:8px;padding:12px">
        <div style="font-size:11px;color:#a5b4fc;font-weight:700;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px">⚠️ JALAN SALING TINDIH (${overlappingEdges.length})</div>
        <div style="font-size:10px;color:#d8b4fe;margin-bottom:8px">Jalan-jalan di bawah menempati lokasi yang sama. Ubah kondisi pada semua sekaligus:</div>
        <div style="display:grid;gap:4px;margin-bottom:8px">
          ${overlappingEdges
            .map(
              (e) => `
            <div style="padding:6px;background:#0f172a;border-radius:5px;font-size:10px;color:#cbd5e1">
              <b style="color:#a5b4fc">${e.id}</b> • ${e.from_name} → ${e.to_name} (${e.distance}m)
            </div>
          `,
            )
            .join("")}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
          <button onclick="setConditionToAll('${edge.id}','CLOSED','${overlappingEdges.map((e) => e.id).join(",")}')"
            style="padding:7px;background:#ef4444;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:11px">🚫 Tutup Semua</button>
          <button onclick="setConditionToAll('${edge.id}','NORMAL','${overlappingEdges.map((e) => e.id).join(",")}')"
            style="padding:7px;background:#22c55e;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:11px">✅ Normal Semua</button>
        </div>
      </div>
    `;
  }

  panel.innerHTML = `
    <div class="ee-header">
      <div style="overflow:hidden">
        <div style="font-size:10px;color:#64748b;margin-bottom:1px">🛣 Ruas &nbsp;<b style="color:#94a3b8">${edge.id}</b></div>
        <div style="font-size:12px;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${fromName} → ${toName}</div>
      </div>
      <button class="ee-close" onclick="closeEdgeEditor()">✕</button>
    </div>

    <div style="display:flex;border-bottom:1px solid #1e293b;background:#0f172a">
      <div style="flex:1;text-align:center;padding:8px 4px">
        <div style="font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.5px">Jarak</div>
        <div style="font-size:16px;font-weight:700;color:#38bdf8">${edge.distance}<span style="font-size:10px"> m</span></div>
      </div>
      <div style="flex:1;text-align:center;padding:8px 4px;border-left:1px solid #1e293b;border-right:1px solid #1e293b">
        <div style="font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.5px">Status</div>
        <div style="font-size:11px;font-weight:700;color:${statusColor};margin-top:2px">${cur}</div>
      </div>
      <div style="flex:1;text-align:center;padding:8px 4px">
        <div style="font-size:9px;color:#475569;text-transform:uppercase;letter-spacing:.5px">Bobot</div>
        <div style="font-size:16px;font-weight:700;color:#a78bfa">×${parseFloat(sev).toFixed(1)}</div>
      </div>
    </div>

    ${overlappingHtml}

    <div class="ee-section">
      <label style="margin-bottom:8px">Kondisi Jalan</label>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <button class="cond-btn cb-normal ${cur === "NORMAL" ? "active" : ""}" onclick="setCondition('${edge.id}','NORMAL')" style="padding:9px 4px;border-radius:8px;font-size:12px">✅ Normal</button>
        <button class="cond-btn cb-busy ${cur === "BUSY" ? "active" : ""}" onclick="setCondition('${edge.id}','BUSY')" style="padding:9px 4px;border-radius:8px;font-size:12px">🚦 Sibuk</button>
        <button class="cond-btn cb-pothole ${cur === "POTHOLE" ? "active" : ""}" onclick="setCondition('${edge.id}','POTHOLE')" style="padding:9px 4px;border-radius:8px;font-size:12px">⚠️ Berlubang</button>
        <button class="cond-btn cb-closed ${cur === "CLOSED" ? "active" : ""}" onclick="setCondition('${edge.id}','CLOSED')" style="padding:9px 4px;border-radius:8px;font-size:12px">🚫 Ditutup</button>
      </div>
    </div>

    <div class="ee-section">
      <label style="margin-bottom:6px">⚖️ Bobot Kustom &nbsp;<b style="color:#a78bfa">×<span id="ee-wval">${parseFloat(sev).toFixed(1)}</span></b></label>
      <input type="range" id="ee-weight" min="0.5" max="5" step="0.1" value="${sev}"
        style="width:100%;accent-color:#7c3aed;margin-bottom:8px"
        oninput="document.getElementById('ee-wval').textContent=parseFloat(this.value).toFixed(1)">
      <div style="display:flex;gap:6px">
        <input type="number" id="ee-weight-num" min="0.1" max="99" step="0.1" value="${sev}" placeholder="Ketik nilai..."
          style="flex:1;padding:6px 8px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#f8fafc;font-size:12px">
        <button onclick="setCondition('${edge.id}','CUSTOM',document.getElementById('ee-weight-num').value||document.getElementById('ee-weight').value)"
          style="padding:6px 14px;background:#7c3aed;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:12px">Simpan</button>
      </div>
    </div>

    <div class="ee-section">
      <label style="margin-bottom:6px">📏 Panjang Jalan</label>
      <div style="display:flex;gap:6px;align-items:center">
        <input type="number" id="ee-dist" value="${edge.distance}" step="0.1" min="0.1"
          style="flex:1;padding:6px 8px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#f8fafc;font-size:12px">
        <span style="color:#64748b;font-size:11px">m</span>
        <button onclick="updateEdgeDist('${edge.id}',document.getElementById('ee-dist').value)"
          style="padding:6px 14px;background:#d97706;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:12px">Simpan</button>
      </div>
      <button class="draw-road-btn" style="margin-top:8px;width:100%" onclick="startDraw('${edge.id}')">✏️ Gambar Ulang di Peta</button>
    </div>

    <div class="ee-section">
      <label style="margin-bottom:6px">🔀 Arah Jalur</label>
      <div style="display:flex;gap:6px">
        <button id="ee-dir-bi" class="cond-btn ${isBidir ? "cb-normal active" : "cb-normal"}" onclick="setEdgeDir('${edge.id}',true)" style="flex:1;padding:8px;border-radius:8px">⇄ Dua Arah</button>
        <button id="ee-dir-one" class="cond-btn ${!isBidir ? "cb-busy active" : "cb-busy"}" onclick="setEdgeDir('${edge.id}',false)" style="flex:1;padding:8px;border-radius:8px">→ Satu Arah</button>
      </div>
      <div id="ee-dir-detail" style="margin-top:8px;padding:10px;background:#0f172a;border-radius:8px;${isBidir ? "display:none" : ""}">
        <div style="font-size:10px;color:#64748b;margin-bottom:6px">Arah perjalanan:</div>
        <select id="ee-dir-from" style="width:100%;padding:6px;background:#1e293b;border:1px solid #334155;border-radius:5px;color:#f8fafc;font-size:11px;margin-bottom:4px">${optFrom}</select>
        <div style="text-align:center;color:#14b8a6;font-size:18px">↓</div>
        <select id="ee-dir-to" style="width:100%;padding:6px;background:#1e293b;border:1px solid #334155;border-radius:5px;color:#f8fafc;font-size:11px;margin-top:4px">${optTo}</select>
        <button onclick="updateEdgeDirection('${edge.id}',false,document.getElementById('ee-dir-from').value,document.getElementById('ee-dir-to').value)"
          style="width:100%;margin-top:8px;padding:7px;background:#d97706;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:12px">💾 Simpan Arah</button>
      </div>
    </div>

    <div class="ee-section">
      <button class="cond-btn cb-closed" style="width:100%;padding:10px;border-radius:8px;font-size:13px" onclick="deleteEdge('${edge.id}')">🗑️ Hapus Jalan Ini</button>
    </div>
  `;
  panel.classList.add("visible");
  if (edgeLayers[edge.id])
    edgeLayers[edge.id].setStyle({ weight: 8, opacity: 1 });
}

function setEdgeDir(edgeId, bidir) {
  const detail = document.getElementById("ee-dir-detail");
  const btnBi = document.getElementById("ee-dir-bi");
  const btnOne = document.getElementById("ee-dir-one");
  if (bidir) {
    detail.style.display = "none";
    btnBi.className = "cond-btn cb-normal active";
    btnOne.className = "cond-btn cb-busy";
    // Immediately save bidirectional
    updateEdgeDirection(edgeId, true, null, null);
  } else {
    detail.style.display = "block";
    btnBi.className = "cond-btn cb-normal";
    btnOne.className = "cond-btn cb-busy active";
  }
}

async function updateEdgeDirection(edgeId, bidir, fromNode, toNode) {
  try {
    const body = { bidirectional: bidir };
    if (fromNode) body.from = fromNode;
    if (toNode) body.to = toNode;
    const r = await fetch(`/api/edges/${edgeId}/direction`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      const label = bidir ? "Dua Arah" : "Satu Arah";
      showAlert(
        `${edgeId} → ${label}${fromNode ? ` (${fromNode}→${toNode})` : ""}`,
        "success",
      );
      await loadGraph();
      // Reopen editor with refreshed data
      const refreshed = edgeById[edgeId];
      if (refreshed && editingEdgeId === edgeId) openEdgeEditor(refreshed);
    } else showAlert(d.error || "Gagal mengubah arah", "error");
  } catch (e) {
    showAlert("Error: " + e.message, "error");
  }
}

function closeEdgeEditor() {
  const panel = document.getElementById("edge-editor");
  if (panel) panel.classList.remove("visible");
  // Reset edge highlight
  if (editingEdgeId && edgeLayers[editingEdgeId]) {
    const sc = graph?.scenarios?.find(
      (s) => s.id === document.getElementById("sel-scenario")?.value,
    );
    const mods = sc?.edge_modifiers || {};
    const blocked = sc?.blocked_edges || [];
    const isMod = !!mods[editingEdgeId];
    const isBlocked = blocked.includes(editingEdgeId);
    const w = isBlocked ? 5 : isMod ? 4.5 : 3.5;
    const op = isBlocked ? 0.35 : isMod ? 0.75 : 0.55;
    edgeLayers[editingEdgeId].setStyle({ weight: w, opacity: op });
  }
  editingEdgeId = null;
}

// ── Conditions API ─────────────────────────────────────────
async function setCondition(id, status, cs = null) {
  let sev = { NORMAL: 1, BUSY: 2, POTHOLE: 1.6, CLOSED: 999 }[status] || 1;
  if (status === "CUSTOM" && cs !== null) sev = parseFloat(cs);
  const r = await fetch("/api/conditions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edge_id: id, status, severity: sev }),
  });
  const d = await r.json();
  conditions = d.conditions;
  if (edgeLayers[id]) edgeLayers[id].setStyle({ color: edgeColor(id) });
  showAlert(`${id} → ${status} (×${sev})`, "success");
  refreshCondTable();
  // Refresh editor if open
  if (editingEdgeId === id && edgeById[id]) openEdgeEditor(edgeById[id]);
}

async function setConditionToAll(mainEdgeId, status, overlappingEdgeIds) {
  const ids = [
    mainEdgeId,
    ...overlappingEdgeIds.split(",").filter((id) => id && id !== mainEdgeId),
  ];
  let sev = { NORMAL: 1, BUSY: 2, POTHOLE: 1.6, CLOSED: 999 }[status] || 1;

  let successCount = 0;
  for (const edgeId of ids) {
    try {
      const r = await fetch("/api/conditions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edge_id: edgeId, status, severity: sev }),
      });
      const d = await r.json();
      if (d.ok) {
        successCount++;
        if (edgeLayers[edgeId])
          edgeLayers[edgeId].setStyle({ color: edgeColor(edgeId) });
      }
    } catch (e) {
      console.error(`Failed to update ${edgeId}:`, e);
    }
  }

  conditions = {};
  const cR = await fetch("/api/conditions");
  conditions = await cR.json();

  showAlert(
    `✅ ${successCount}/${ids.length} jalan diperbarui ke ${status}`,
    "success",
  );
  refreshCondTable();
  drawEdges();

  // Refresh editor if open
  if (editingEdgeId && edgeById[editingEdgeId])
    openEdgeEditor(edgeById[editingEdgeId]);
}

async function updateEdgeDist(id, nd) {
  const d = parseFloat(nd);
  if (isNaN(d) || d <= 0) {
    showAlert("Jarak harus positif", "error");
    return;
  }
  try {
    const r = await fetch(`/api/edges/${id}/distance`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ distance: d }),
    });
    const res = await r.json();
    if (res.ok) {
      if (edgeById[id]) edgeById[id].distance = d;
      const eg = graph.edges.find((e) => e.id === id);
      if (eg) eg.distance = d;
      showAlert(`${id} → ${d}m`, "success");
      refreshCondTable();
      if (editingEdgeId === id && edgeById[id]) openEdgeEditor(edgeById[id]);
    } else showAlert(res.error || "Gagal", "error");
  } catch (e) {
    showAlert("Error: " + e.message, "error");
  }
}

async function deleteEdge(id) {
  if (!confirm(`Hapus jalan ${id}?`)) return;
  closeEdgeEditor();
  try {
    const r = await fetch(`/api/edges/${id}`, { method: "DELETE" });
    const d = await r.json();
    if (d.ok) {
      showAlert(`${id} dihapus!`, "success");
      loadGraph();
    }
  } catch {
    showAlert("Gagal menghapus", "error");
  }
}

async function deleteNode(id) {
  const node = graph.nodes.find((n) => n.id === id);
  const isWP = node && node.type === "Waypoint";
  const msg = isWP
    ? `Hapus waypoint ${id}? Jalan akan digabungkan otomatis.`
    : `Hapus gedung ${node?.name || id}? Semua jalan terhubung juga akan dihapus.`;
  if (!confirm(msg)) return;
  map.closePopup();
  try {
    const r = await fetch(`/api/nodes/${id}`, { method: "DELETE" });
    const d = await r.json();
    if (d.ok) {
      showAlert(
        isWP ? `Waypoint ${id} dihapus, jalan digabung!` : `${id} dihapus!`,
        "success",
      );
      loadGraph();
    } else showAlert(d.error || "Gagal menghapus", "error");
  } catch {
    showAlert("Gagal menghapus node", "error");
  }
}

// ── Draw Road ──────────────────────────────────────────────
function _calcDD() {
  let t = 0;
  for (let i = 1; i < drawPoints.length; i++)
    t += drawPoints[i - 1].distanceTo(drawPoints[i]);
  return Math.round(t * 10) / 10;
}

function _updateDP() {
  const d = _calcDD(),
    n = drawPoints.length;
  document.getElementById("drp-dist-val").textContent = d.toFixed(1);
  const sub = document.getElementById("drp-subtitle");
  sub.textContent =
    n === 0
      ? "Klik pada peta mengikuti jalur jalan"
      : n === 1
        ? "1 titik — lanjutkan klik"
        : `${n} titik — double-klik atau ✓ Terapkan`;
  const ab = document.getElementById("drp-apply");
  ab.disabled = n < 2;
  ab.style.opacity = n < 2 ? ".4" : "1";
  const ub = document.getElementById("drp-undo");
  ub.disabled = n === 0;
  ub.style.opacity = n === 0 ? ".4" : "1";
}

function startDraw(id) {
  closeEdgeEditor();
  drawPoints = [];
  drawEdgeId = id;
  isDrawing = true;
  [drawPolyline, drawTempLine, drawSnapLine].forEach((l) => {
    if (l) map.removeLayer(l);
  });
  drawPolyline = drawTempLine = drawSnapLine = null;
  drawDots.forEach((d) => map.removeLayer(d));
  drawDots = [];
  if (edgeLayers[id]) {
    drawSnapLine = L.polyline(edgeLayers[id].getLatLngs(), {
      color: "#f59e0b",
      weight: 6,
      opacity: 0.35,
      dashArray: "12,8",
    }).addTo(map);
    map.fitBounds(edgeLayers[id].getBounds(), {
      padding: [60, 60],
      maxZoom: 18,
    });
  }
  document.getElementById("draw-road-panel").classList.add("visible");
  document.getElementById("drp-edge-id").textContent = id;
  map.getContainer().classList.add("map-drawing-cursor");
  _updateDP();
}

function addDrawPoint(ll) {
  drawPoints.push(ll);
  const dot = L.circleMarker(ll, {
    radius: 6,
    color: "#f59e0b",
    fillColor: "#fbbf24",
    fillOpacity: 1,
    weight: 2.5,
  }).addTo(map);
  dot.bindTooltip(`${drawPoints.length}`, {
    permanent: true,
    direction: "top",
    className: "measure-label",
    offset: [0, -10],
  });
  drawDots.push(dot);
  if (drawPolyline) map.removeLayer(drawPolyline);
  if (drawPoints.length > 1)
    drawPolyline = L.polyline(drawPoints, {
      color: "#f59e0b",
      weight: 4,
      opacity: 0.95,
    }).addTo(map);
  _updateDP();
}

function undoDraw() {
  if (!drawPoints.length) return;
  drawPoints.pop();
  const d = drawDots.pop();
  if (d) map.removeLayer(d);
  if (drawPolyline) {
    map.removeLayer(drawPolyline);
    drawPolyline = null;
  }
  if (drawTempLine) {
    map.removeLayer(drawTempLine);
    drawTempLine = null;
  }
  if (drawPoints.length > 1)
    drawPolyline = L.polyline(drawPoints, {
      color: "#f59e0b",
      weight: 4,
      opacity: 0.95,
    }).addTo(map);
  _updateDP();
}

function finishDraw() {
  if (drawPoints.length < 2) {
    showAlert("Min 2 titik", "error");
    return;
  }
  const d = _calcDD(),
    tid = drawEdgeId;
  cancelDraw(true);
  if (tid && confirm(`Jarak: ${d}m\nTerapkan ke ${tid}?`))
    updateEdgeDist(tid, d);
  else showAlert(`Jarak: ${d}m (tidak diterapkan)`, "info");
}

function cancelDraw(keepId = false) {
  isDrawing = false;
  if (!keepId) drawEdgeId = null;
  [drawPolyline, drawTempLine, drawSnapLine].forEach((l) => {
    if (l) map.removeLayer(l);
  });
  drawPolyline = drawTempLine = drawSnapLine = null;
  drawDots.forEach((d) => map.removeLayer(d));
  drawDots = [];
  drawPoints = [];
  document.getElementById("draw-road-panel").classList.remove("visible");
  map.getContainer().classList.remove("map-drawing-cursor");
  map.getContainer().style.cursor = mapMode ? "crosshair" : "";
}

// ── Find Route (with loading + animation) ──────────────────
async function findRoute() {
  const start = document.getElementById("sel-start").value;
  const end = document.getElementById("sel-end").value;
  const scen = document.getElementById("sel-scenario").value;
  const tfac = parseFloat(document.getElementById("time-factor").value) || 1.0;
  const btn = document.getElementById("btn-find-route");
  btn.classList.add("loading");
  btn.textContent = "⏳ Menghitung…";
  drawNodes();
  try {
    const r = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start, end, scenario: scen, time_factor: tfac }),
    });
    const data = await r.json();
    if (data.error) {
      showAlert(data.detail || data.error, "error");
      return;
    }
    if (routeLayer) {
      routeLayer.forEach((l) => map.removeLayer(l));
      routeLayer = null;
    }

    const layers = [];
    const allGeom = data.edges_geometry || [];
    const routeLines = allGeom
      .map((eg) => (eg.geometry || []).map((p) => [p[0], p[1]]))
      .filter((ll) => ll.length > 1);
    if (routeLines.length) {
      const glow = L.polyline(routeLines, {
        color: "#fbbf24",
        weight: 12,
        opacity: 0.2,
        smoothFactor: 0.1,
      }).addTo(map);
      const line = L.polyline(routeLines, {
        color: "#f59e0b",
        weight: 6,
        opacity: 1,
        smoothFactor: 0.1,
      }).addTo(map);
      layers.push(glow, line);
    }
    routeLayer = layers;

    const routePts = routeLines.flat();
    if (routePts.length > 1) {
      map.fitBounds(L.latLngBounds(routePts), { padding: [50, 50] });
    } else if (data.path && data.path.length > 1) {
      const pts = data.path
        .filter((id) => nodeById[id])
        .map((id) => [nodeById[id].lat, nodeById[id].lon]);
      map.fitBounds(L.latLngBounds(pts), { padding: [50, 50] });
    }
    document.getElementById("m-dist").textContent = data.total_dist_m ?? "-";
    document.getElementById("m-eta").textContent = data.eta_minutes ?? "-";
    document.getElementById("m-iter").textContent = data.iterations ?? "-";
    document.getElementById("m-ms").textContent = data.execution_ms ?? "-";

    const ul = document.getElementById("path-list");
    ul.innerHTML = "";
    (data.path || []).forEach((id, i) => {
      const li = document.createElement("li");
      li.textContent = `${id} – ${nodeById[id]?.name || id}`;
      if (i === 0) li.classList.add("start");
      if (i === data.path.length - 1) li.classList.add("end");
      ul.appendChild(li);
    });
    document.getElementById("metrics").style.display = "block";
    showAlert(
      `Rute: ${data.total_dist_m}m, ETA ${data.eta_minutes} menit`,
      "success",
    );
  } catch (e) {
    showAlert("Error: " + e.message, "error");
  } finally {
    btn.classList.remove("loading");
    btn.textContent = "🔍 Cari Rute Tercepat";
  }
}

function clearRoute() {
  if (routeLayer) {
    routeLayer.forEach((l) => map.removeLayer(l));
    routeLayer = null;
  }
  document.getElementById("metrics").style.display = "none";
  document.getElementById("alert").style.display = "none";
}
// ── Auto-Generate OSM Roads ──────────────────────────────
async function generateOSMRoads() {
  if (
    !confirm(
      "Fitur ini akan mengunduh semua jalan kaki/kendaraan dari OpenStreetMap di area kampus UNIB dan menjadikannya jalur di aplikasi.\n\nLanjutkan?",
    )
  )
    return;
  const btn = document.getElementById("btn-osm-sync");
  const oldText = btn.textContent;
  btn.textContent = "⏳ Mengunduh...";
  btn.style.pointerEvents = "none";
  btn.style.opacity = "0.7";

  try {
    const r = await fetch("/api/osm-sync", { method: "POST" });
    const d = await r.json();
    if (d.ok) {
      showAlert(
        `Berhasil! ${d.nodes_added} titik (waypoint) dan ${d.edges_added} jalan baru ditambahkan dari OSM.`,
        "success",
      );
      await loadGraph();
    } else {
      showAlert(d.error || "Gagal mengunduh OSM", "error");
    }
  } catch (e) {
    showAlert("Error: " + e.message, "error");
  } finally {
    btn.textContent = oldText;
    btn.style.pointerEvents = "auto";
    btn.style.opacity = "1";
  }
}

async function clearOSMRoads() {
  if (
    !confirm(
      "Apakah Anda yakin ingin menghapus semua titik dan jalan yang di-generate dari OSM?",
    )
  )
    return;
  const btn = document.getElementById("btn-osm-clear");
  const oldText = btn.textContent;
  btn.textContent = "⏳ Menghapus...";
  try {
    const r = await fetch("/api/osm-sync", { method: "DELETE" });
    const d = await r.json();
    if (d.ok) {
      showAlert("Semua jalan dari OSM telah dihapus!", "success");
      await loadGraph();
    }
  } catch (e) {
    showAlert("Error: " + e.message, "error");
  } finally {
    btn.textContent = oldText;
  }
}

// ── Compare Scenarios ──────────────────────────────────────
async function compareScenarios() {
  const start = document.getElementById("sel-start").value;
  const end = document.getElementById("sel-end").value;
  const tfac = parseFloat(document.getElementById("time-factor").value) || 1.0;
  const overlay = document.getElementById("compare-overlay");
  const grid = document.getElementById("compare-grid");
  grid.innerHTML =
    '<div style="text-align:center;padding:24px;color:#7fb4ab">⏳ Menghitung semua skenario…</div>';
  overlay.classList.add("visible");

  try {
    const entries = await Promise.all(
      graph.scenarios.map(async (sc) => {
        const r = await fetch("/api/route", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start,
            end,
            scenario: sc.id,
            time_factor: tfac,
          }),
        });
        return [sc.id, await r.json()];
      }),
    );
    const results = Object.fromEntries(entries);
    // Find best
    let bestId = null,
      bestCost = Infinity;
    for (const [k, v] of Object.entries(results)) {
      if (!v.error && v.total_cost < bestCost) {
        bestCost = v.total_cost;
        bestId = k;
      }
    }
    grid.innerHTML = "";
    for (const sc of graph.scenarios) {
      const r = results[sc.id];
      const isBest = sc.id === bestId;
      const card = document.createElement("div");
      card.className = "compare-card" + (isBest ? " best" : "");
      if (r.error) {
        card.innerHTML = `<div class="cc-name" style="color:${sc.color || "#5eead4"}">${sc.id}</div><div class="cc-error">❌ ${r.detail || r.error}</div>`;
      } else {
        card.innerHTML = `<div class="cc-name" style="color:${sc.color || "#5eead4"}">${sc.id} ${isBest ? '<span class="cc-badge cc-best">TERBAIK</span>' : ""}</div>
          <div class="cc-metrics">
            <div class="cc-metric"><div class="cc-val">${r.total_dist_m}</div><div class="cc-lbl">Jarak (m)</div></div>
            <div class="cc-metric"><div class="cc-val">${r.eta_minutes}</div><div class="cc-lbl">ETA (min)</div></div>
            <div class="cc-metric"><div class="cc-val">${r.iterations}</div><div class="cc-lbl">Iterasi</div></div>
            <div class="cc-metric"><div class="cc-val">${r.execution_ms}</div><div class="cc-lbl">Waktu (ms)</div></div>
          </div>`;
      }
      grid.appendChild(card);
    }
  } catch (e) {
    grid.innerHTML = `<div class="cc-error">Error: ${e.message}</div>`;
  }
}

function closeCompare() {
  document.getElementById("compare-overlay").classList.remove("visible");
}

// ── Conditions Table (clickable rows) ──────────────────────
async function refreshCondTable() {
  const r = await fetch("/api/conditions");
  conditions = await r.json();
  const ec = conditions.edge_conditions || {};
  const tbody = document.getElementById("cond-tbody");
  tbody.innerHTML = "";
  graph.edges.forEach((e) => {
    const c = ec[e.id];
    const st = c ? (c.status || c.type || "NORMAL").toUpperCase() : "NORMAL";
    const statusColor =
      {
        NORMAL: "#22c55e",
        BUSY: "#f59e0b",
        POTHOLE: "#f97316",
        CLOSED: "#ef4444",
        CUSTOM: "#a78bfa",
      }[st] || "#22c55e";
    const cls =
      {
        NORMAL: "normal",
        BUSY: "busy",
        POTHOLE: "pothole",
        CLOSED: "closed",
        CUSTOM: "custom",
      }[st] || "normal";
    const from = nodeById[e.from]?.name?.substring(0, 18) || e.from;
    const to = nodeById[e.to]?.name?.substring(0, 18) || e.to;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><b>${e.id}</b></td><td style="font-size:10px">${from}<br>→ ${to}</td><td>${e.distance}m</td><td><span class="badge badge-${cls}">${st}</span></td>`;
    tr.addEventListener("click", () => {
      if (edgeLayers[e.id]) {
        map.fitBounds(edgeLayers[e.id].getBounds(), {
          padding: [80, 80],
          maxZoom: 18,
        });
        edgeLayers[e.id].setStyle({ weight: 9, opacity: 1 });
        setTimeout(
          () => edgeLayers[e.id].setStyle({ weight: 3.5, opacity: 0.55 }),
          1500,
        );
      }
      openEdgeEditor(e);
    });
    tbody.appendChild(tr);
  });
  if (graph) drawEdges();
}

async function resetConditions() {
  if (!confirm("Reset semua kondisi?")) return;
  await fetch("/api/conditions/reset", { method: "POST" });
  conditions = { edge_conditions: {}, edge_directions: {} };
  drawEdges();
  refreshCondTable();
  showAlert("Semua kondisi direset", "success");
}

// ── Scenario Management ────────────────────────────────────
function renderScenarioCards() {
  const container = document.getElementById("scenario-cards");
  if (!container || !graph) return;
  container.innerHTML = "";
  graph.scenarios.forEach((sc) => {
    const mods = sc.edge_modifiers || {};
    const blocked = sc.blocked_edges || [];
    const isBuiltin = ["Normal", "Wisuda", "UTBK", "Event Besar"].includes(
      sc.id,
    );
    let tags = "";
    for (const [eid, mul] of Object.entries(mods))
      tags += `<span class="sc-tag">×${mul} ${eid}</span>`;
    for (const eid of blocked)
      tags += `<span class="sc-tag sc-tag-blocked">🚫 ${eid}</span>`;
    if (!tags)
      tags =
        '<span style="font-size:10px;color:#7fb4ab">Tidak ada modifikasi</span>';
    const deleteBtn = isBuiltin
      ? ""
      : `<button class="sc-card-delete" onclick="deleteScenario('${sc.id}')" title="Hapus">✕</button>`;
    container.innerHTML += `<div class="sc-card" style="border-left-color:${sc.color}">
      <div class="sc-card-header"><span class="sc-card-name" style="color:${sc.color}">${sc.id}</span>${deleteBtn}</div>
      <div class="sc-card-desc">${sc.description || "—"}</div>
      <div class="sc-card-tags">${tags}</div>
    </div>`;
  });
}

async function addScenario() {
  const name = document.getElementById("new-sc-name").value.trim();
  const desc = document.getElementById("new-sc-desc").value.trim();
  const color =
    document.querySelector('input[name="sc-color"]:checked')?.value ||
    "#14b8a6";
  const modsRaw = document.getElementById("new-sc-mods").value.trim();
  const blockedRaw = document.getElementById("new-sc-blocked").value.trim();
  if (!name) {
    showAlert("Nama skenario wajib diisi", "error");
    return;
  }
  // Parse modifiers: "E01:2.0, E02:1.5"
  const edge_modifiers = {};
  if (modsRaw)
    modsRaw.split(",").forEach((p) => {
      const [k, v] = p.trim().split(":");
      if (k && v) edge_modifiers[k.trim()] = parseFloat(v);
    });
  const blocked_edges = blockedRaw
    ? blockedRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : [];
  try {
    const r = await fetch("/api/scenarios", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description: desc,
        color,
        edge_modifiers,
        blocked_edges,
      }),
    });
    const d = await r.json();
    if (d.error) {
      showAlert(d.error, "error");
      return;
    }
    showAlert(`Skenario "${name}" ditambahkan!`, "success");
    // Clear form
    document.getElementById("new-sc-name").value = "";
    document.getElementById("new-sc-desc").value = "";
    document.getElementById("new-sc-mods").value = "";
    document.getElementById("new-sc-blocked").value = "";
    loadGraph();
  } catch (e) {
    showAlert("Gagal: " + e.message, "error");
  }
}

async function deleteScenario(id) {
  if (!confirm(`Hapus skenario "${id}"?`)) return;
  try {
    const r = await fetch("/api/scenarios", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const d = await r.json();
    if (d.ok) {
      showAlert(`Skenario "${id}" dihapus`, "success");
      loadGraph();
    }
  } catch (e) {
    showAlert("Gagal: " + e.message, "error");
  }
}

// ── Events ─────────────────────────────────────────────────
document.getElementById("sel-start").addEventListener("change", drawNodes);
document.getElementById("sel-end").addEventListener("change", drawNodes);
document.getElementById("sel-scenario").addEventListener("change", () => {
  populateScenarios();
});

document.addEventListener("keydown", (e) => {
  if (
    e.key === "Enter" &&
    !isDrawing &&
    document.getElementById("tab-route").classList.contains("active")
  )
    findRoute();
});

// ── Init ───────────────────────────────────────────────────
loadGraph();
setTimeout(() => map.invalidateSize(), 200);
window.addEventListener("resize", () => map.invalidateSize());

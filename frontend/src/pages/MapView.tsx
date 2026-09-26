import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type GeoJSONSource, type LngLatBoundsLike, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { usePoll } from "../api";
import { missionStatus, readable } from "../plain";

/* The situation map.
 *
 * A live picture of the affected area for people who run a disaster, not for engineers: real streets or
 * satellite imagery, 3D terrain (Ratnapura is hilly, and slope matters for floods and landslides), live
 * rain radar, and everything ATLAS knows on top. Click anything to follow it; take a guided tour; fly
 * along an evacuation route. All sources are keyless; if the internet is down the ATLAS layers still draw.
 */

interface Area { id: string; name: string; lat: number; lon: number; population: number | null; vulnerable: number; level: string | null; incident_id: string | null }
interface Team { id: string; name: string; kind: string; lat: number; lon: number; busy: boolean }
interface Shelter { id: string; name?: string; lat: number; lon: number; capacity: number; occupancy?: number | null; expected?: number; elevation_m?: number | null }
interface Gauge { node: string; type: string; value: number; unit: string; threshold: number | null; lat: number; lon: number }
interface Route { mission_id: string; title: string; status: string; points: [number, number][]; team?: string; people?: number; destination?: string | null; progress?: number }
interface Edge { id: string; from: [number, number]; to: [number, number]; belief_open: number | null; confidence: number | null; closed_by_decision: boolean }
interface MapData {
  site: string;
  bounds: [[number, number], [number, number]];
  areas?: Area[];
  teams?: Team[];
  edges: Edge[];
  routes: Route[];
  shelters: Shelter[];
  gauges: Gauge[];
  observed_edges: number;
  likely_closed: number;
}
interface Alert { id: string; status: string; area: string; event: string; severity: string; texts: Record<string, { headline: string; instruction: string }> }

type Selected =
  | { kind: "area"; item: Area }
  | { kind: "shelter"; item: Shelter }
  | { kind: "team"; item: Team }
  | { kind: "station"; item: Station }
  | { kind: "route"; item: Route }
  | { kind: "road"; item: Edge };

interface Station { key: string; lat: number; lon: number; gauges: Gauge[] }

const LEVEL_COLOUR: Record<string, string> = { normal: "#16a34a", watch: "#eab308", warning: "#f97316", severe: "#dc2626" };
const SENSOR_NAME: Record<string, string> = { water_level: "River level", rainfall: "Rainfall", soil_moisture: "Soil wetness", tilt: "Ground movement" };

const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
const TERRAIN = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

function metres(a: [number, number], b: [number, number]) {
  const R = 6371000, r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
const distText = (m: number) => (m < 950 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`);
const bearing = (a: [number, number], b: [number, number]) => {
  const r = Math.PI / 180;
  const y = Math.sin((b[1] - a[1]) * r) * Math.cos(b[0] * r);
  const x = Math.cos(a[0] * r) * Math.sin(b[0] * r) - Math.sin(a[0] * r) * Math.cos(b[0] * r) * Math.cos((b[1] - a[1]) * r);
  return (Math.atan2(y, x) / r + 360) % 360;
};
function circle(lat: number, lon: number, radius: number, steps = 48): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI;
    out.push([lon + ((radius / 111320) * Math.cos(t)) / Math.cos((lat * Math.PI) / 180), lat + (radius / 110540) * Math.sin(t)]);
  }
  return out;
}
function roadState(e: Edge) {
  if (e.closed_by_decision) return { text: "Closed by decision", colour: "#111827" };
  if (e.belief_open === null) return { text: "No reports yet", colour: "#94a3b8" };
  if (e.belief_open >= 0.7) return { text: "Probably open", colour: "#16a34a" };
  if (e.belief_open >= 0.3) return { text: "Uncertain", colour: "#d97706" };
  return { text: "Probably closed", colour: "#dc2626" };
}
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

// ------------------------------------------------------------------------------------------ view link

interface View { c: [number, number]; z: number; p: number; b: number; sat: boolean; terrain: boolean; radar: boolean }
function readView(): Partial<View> {
  const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  const c = q.get("c")?.split(",").map(Number);
  return {
    ...(c && c.length === 2 && c.every(Number.isFinite) ? { c: [c[0], c[1]] as [number, number] } : {}),
    ...(q.get("z") ? { z: Number(q.get("z")) } : {}),
    ...(q.get("p") ? { p: Number(q.get("p")) } : {}),
    ...(q.get("b") ? { b: Number(q.get("b")) } : {}),
    ...(q.get("l") ? { sat: q.get("l")!.includes("sat"), terrain: q.get("l")!.includes("3d"), radar: q.get("l")!.includes("radar") } : {}),
  };
}

// ------------------------------------------------------------------------------------------ the page

export function MapPage() {
  const { data, error } = usePoll<MapData>("/dev/map", 4000);
  const alerts = usePoll<Alert[]>("/alerts", 5000);
  const approvals = usePoll<unknown[]>("/approvals", 5000);
  const holder = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const markers = useRef<Map<string, maplibregl.Marker>>(new Map());
  const initial = useRef(readView());
  const [ready, setReady] = useState(false);
  const [sat, setSat] = useState(initial.current.sat ?? false);
  const [terrain, setTerrain] = useState(initial.current.terrain ?? true);
  const [radar, setRadar] = useState(initial.current.radar ?? false);
  const [radarNote, setRadarNote] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [centre, setCentre] = useState<[number, number] | null>(null);
  const [caption, setCaption] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [copied, setCopied] = useState(false);
  const [showLegend, setShowLegend] = useState(true);
  const stop = useRef(false);
  const fitted = useRef(false);

  const stations = useMemo<Station[]>(() => {
    const out = new Map<string, Station>();
    for (const g of data?.gauges ?? []) {
      const key = `${g.lat.toFixed(4)},${g.lon.toFixed(4)}`;
      if (!out.has(key)) out.set(key, { key, lat: g.lat, lon: g.lon, gauges: [] });
      out.get(key)!.gauges.push(g);
    }
    return [...out.values()];
  }, [data]);

  // ---- create the map once
  useEffect(() => {
    if (!holder.current || mapRef.current) return;
    const v = initial.current;
    const map = new maplibregl.Map({
      container: holder.current,
      style: {
        version: 8,
        sources: {
          streets: { type: "raster", tiles: [OSM], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" },
          satellite: { type: "raster", tiles: [ESRI], tileSize: 256, maxzoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
          labels: { type: "raster", tiles: [ESRI_LABELS], tileSize: 256, maxzoom: 19 },
          dem: { type: "raster-dem", tiles: [TERRAIN], tileSize: 256, maxzoom: 15, encoding: "terrarium", attribution: "Terrain: Mapzen / AWS Open Data" },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#e7ebf0" } },
          { id: "streets", type: "raster", source: "streets", layout: { visibility: v.sat ? "none" : "visible" } },
          { id: "satellite", type: "raster", source: "satellite", layout: { visibility: v.sat ? "visible" : "none" } },
          { id: "labels", type: "raster", source: "labels", layout: { visibility: v.sat ? "visible" : "none" } },
          { id: "hillshade", type: "hillshade", source: "dem", paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#334155" } },
        ],
      },
      center: v.c ? [v.c[1], v.c[0]] : [80.3992, 6.6828],
      zoom: v.z ?? 14,
      pitch: v.p ?? 50,
      bearing: v.b ?? -15,
      maxPitch: 75,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.once("style.load", () => {  // not "load": that waits for tiles, which may never come offline
      if (initial.current.terrain ?? true) map.setTerrain({ source: "dem", exaggeration: 1.6 });
      map.addSource("area", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": ["get", "colour"], "fill-opacity": 0.16 } });
      map.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": ["get", "colour"], "line-width": 2.5, "line-dasharray": [2, 1.5] } });
      map.addSource("roads", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "roads", type: "line", source: "roads",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ["get", "colour"], "line-width": ["case", ["get", "known"], 4, 2], "line-opacity": ["get", "opacity"] },
      });
      map.addSource("routes", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "routes-casing", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.9 } });
      map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#2563eb", "line-width": 5, "line-dasharray": [0, 2, 2] } });
      // wide, invisible copies so a thin line is easy to click or tap
      map.addLayer({ id: "roads-hit", type: "line", source: "roads", paint: { "line-color": "#000", "line-width": 16, "line-opacity": 0 } }, "routes-casing");
      map.addLayer({ id: "routes-hit", type: "line", source: "routes", paint: { "line-color": "#000", "line-width": 22, "line-opacity": 0 } });
      map.addLayer({ id: "routes-selected", type: "line", source: "routes", filter: ["==", ["get", "id"], ""], layout: { "line-cap": "round" }, paint: { "line-color": "#f59e0b", "line-width": 7 } });
      map.on("click", "roads-hit", (e) => {
        if (map.queryRenderedFeatures(e.point, { layers: ["routes-hit"] }).length) return; // a route wins over the road under it
        const id = e.features?.[0]?.properties?.id as string | undefined;
        const edge = dataRef.current?.edges.find((x) => x.id === id);
        if (edge) setSelected({ kind: "road", item: edge });
      });
      map.on("click", "routes-hit", (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined;
        const route = dataRef.current?.routes.find((x) => x.mission_id === id);
        if (route) setSelected({ kind: "route", item: route });
      });
      for (const layer of ["roads-hit", "routes-hit"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
      setReady(true);
    });
    const onMove = () => { const c = map.getCenter(); setCentre([c.lat, c.lng]); };
    map.on("moveend", onMove);
    map.on("dragstart", () => { stop.current = true; });
    // "flowing" dashes on mission routes, like traffic moving toward the shelter
    let step = 0, raf = 0, last = 0;
    const dash = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
    const animate = (t: number) => {
      if (t - last > 90 && map.getLayer("routes")) {
        step = (step + 1) % dash.length; last = t;
        map.setPaintProperty("routes", "line-dasharray", dash[step]);
      }
      raf = requestAnimationFrame(animate);
    };
    raf = requestAnimationFrame(animate);
    mapRef.current = map;
    return () => { cancelAnimationFrame(raf); map.remove(); mapRef.current = null; };
  }, []);

  const dataRef = useRef<MapData | undefined>(undefined);
  dataRef.current = data ?? undefined;

  // ---- layers from data
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !data) return;
    (map.getSource("roads") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: data.edges.map((e) => {
        const s = roadState(e);
        return {
          type: "Feature", properties: { id: e.id, colour: s.colour, known: e.belief_open !== null || e.closed_by_decision, opacity: e.belief_open === null && !e.closed_by_decision ? 0.45 : 0.45 + 0.55 * (e.confidence ?? 0.6) },
          geometry: { type: "LineString", coordinates: [[e.from[1], e.from[0]], [e.to[1], e.to[0]]] },
        };
      }),
    });
    (map.getSource("routes") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: data.routes.map((r) => ({ type: "Feature", properties: { id: r.mission_id }, geometry: { type: "LineString", coordinates: r.points.map((p) => [p[1], p[0]]) } })),
    });
    (map.getSource("area") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: (data.areas ?? []).map((a) => ({
        type: "Feature", properties: { id: a.id, colour: a.level ? LEVEL_COLOUR[a.level] ?? "#f97316" : "#64748b" },
        geometry: { type: "Polygon", coordinates: [circle(a.lat, a.lon, 650)] },
      })),
    });
    if (!fitted.current && !initial.current.c) {
      const [[la0, lo0], [la1, lo1]] = data.bounds;
      map.fitBounds([[lo0, la0], [lo1, la1]] as LngLatBoundsLike, { padding: 60, pitch: 50, bearing: -15, duration: 0 });
      fitted.current = true;
    }
    // markers: areas, shelters, teams, sensor stations
    const want = new Set<string>();
    const stack = new Map<string, number>();
    const place = (key: string, lat: number, lon: number, html: string, cls: string, onClick: () => void) => {
      want.add(key);
      const spot = `${lat.toFixed(4)},${lon.toFixed(4)}`;
      const n = stack.get(spot) ?? 0;
      stack.set(spot, n + 1);
      const offset: [number, number] = n === 0 ? [0, 0] : [Math.cos(n * 1.1) * (18 + n * 5), Math.sin(n * 1.1) * (18 + n * 5)];
      let m = markers.current.get(key);
      if (!m) {
        const el = document.createElement("button");
        el.className = `mk ${cls}`;
        el.addEventListener("click", (ev) => { ev.stopPropagation(); onClickRef.current.get(key)?.(); });
        m = new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([lon, lat]).addTo(map);
        markers.current.set(key, m);
      }
      m.setLngLat([lon, lat]).setOffset(offset);
      const el = m.getElement();
      el.className = `mk ${cls}`;
      if (el.innerHTML !== html) el.innerHTML = html;
      onClickRef.current.set(key, onClick);
    };
    for (const a of data.areas ?? []) {
      place(`area:${a.id}`, a.lat, a.lon, `<span class="mk-i">⚠</span><span class="mk-t">${a.name}${a.level ? ` · ${a.level}` : ""}</span>`, `area ${a.level ?? "none"}`, () => setSelected({ kind: "area", item: a }));
    }
    for (const s of data.shelters) {
      const full = s.capacity ? ((s.occupancy ?? 0) + (s.expected ?? 0)) / s.capacity : 0;
      place(`shelter:${s.id}`, s.lat, s.lon, `<span class="mk-i">⌂</span><span class="mk-t">${s.name ?? s.id}${s.expected ? ` · ${s.expected} coming` : ""}</span>`, `shelter ${full >= 0.9 ? "full" : ""}`, () => setSelected({ kind: "shelter", item: s }));
    }
    for (const t of data.teams ?? []) {
      place(`team:${t.id}`, t.lat, t.lon, `<span class="mk-i">${t.kind === "vehicle" ? "🚐" : "⛑"}</span>`, `team ${t.busy ? "busy" : ""}`, () => setSelected({ kind: "team", item: t }));
    }
    for (const st of stations) {
      const over = st.gauges.some((g) => g.threshold != null && g.value >= g.threshold);
      const river = st.gauges.find((g) => g.type === "water_level");
      const first = st.gauges[0];
      const label = river ? `River ${river.value.toFixed(0)} ${river.unit}` : st.gauges.length === 1 ? `${SENSOR_NAME[first.type] ?? first.type} ${first.value.toFixed(0)} ${first.unit}` : `${st.gauges.length} sensors`;
      place(`station:${st.key}`, st.lat, st.lon, `<span class="mk-i">〰</span><span class="mk-t">${label}</span>`, `station ${over ? "over" : ""}`, () => setSelected({ kind: "station", item: st }));
    }
    for (const [key, m] of markers.current) if (!want.has(key)) { m.remove(); markers.current.delete(key); }
  }, [data, ready, stations]);

  const onClickRef = useRef<Map<string, () => void>>(new Map());

  // ---- basemap, terrain, radar
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setLayoutProperty("streets", "visibility", sat ? "none" : "visible");
    map.setLayoutProperty("satellite", "visibility", sat ? "visible" : "none");
    map.setLayoutProperty("labels", "visibility", sat ? "visible" : "none");
  }, [sat, ready]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setTerrain(terrain ? { source: "dem", exaggeration: 1.6 } : null);
    map.easeTo({ pitch: terrain ? 55 : 0, duration: 800 });
  }, [terrain, ready]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (!radar) {
      if (map.getLayer("radar")) map.removeLayer("radar");
      if (map.getSource("radar")) map.removeSource("radar");
      setRadarNote(null);
      return;
    }
    let cancelled = false;
    fetch("https://api.rainviewer.com/public/weather-maps.json")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        const frame = d.radar?.past?.[d.radar.past.length - 1];
        if (!frame) throw new Error("no radar frame");
        if (!map.getSource("radar")) {
          map.addSource("radar", { type: "raster", tiles: [`${d.host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`], tileSize: 256, maxzoom: 7, attribution: "Radar: RainViewer" });
          map.addLayer({ id: "radar", type: "raster", source: "radar", paint: { "raster-opacity": 0.6 } }, "area-fill");
        }
        setRadarNote(`Live rain radar, ${new Date(frame.time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      })
      .catch(() => setRadarNote("Rain radar needs an internet connection."));
    return () => { cancelled = true; };
  }, [radar, ready]);

  // ---- camera helpers
  const flyTo = useCallback((lat: number, lon: number, zoom = 16.2, pitch = 60, brg?: number) => {
    const map = mapRef.current;
    if (!map) return Promise.resolve();
    return new Promise<void>((done) => {
      map.once("moveend", () => done());
      map.flyTo({ center: [lon, lat], zoom, pitch: terrain ? pitch : 0, bearing: brg ?? map.getBearing(), duration: 2200, essential: true });
    });
  }, [terrain]);

  const where = (s: Selected): [number, number] => {
    switch (s.kind) {
      case "route": return s.item.points[Math.floor(s.item.points.length / 2)];
      case "road": return [(s.item.from[0] + s.item.to[0]) / 2, (s.item.from[1] + s.item.to[1]) / 2];
      default: return [s.item.lat, s.item.lon];
    }
  };

  // follow the selected thing
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setFilter("routes-selected", ["==", ["get", "id"], selected?.kind === "route" ? selected.item.mission_id : ""]);
    if (!selected) return;
    if (selected.kind === "route") {
      const b = new maplibregl.LngLatBounds();
      selected.item.points.forEach((p) => b.extend([p[1], p[0]]));
      map.fitBounds(b, { padding: { top: 80, bottom: 260, left: 60, right: 340 }, pitch: terrain ? 50 : 0, duration: 1600 });
    } else {
      const [lat, lon] = where(selected);
      void flyTo(lat, lon, selected.kind === "area" ? 15.2 : 16.4);
    }
  }, [selected, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  const flyAlong = async (route: Route) => {
    const map = mapRef.current;
    if (!map || route.points.length < 2) return;
    stop.current = false;
    setCaption(`Flying the route to ${route.destination ?? "the destination"}…`);
    await flyTo(route.points[0][0], route.points[0][1], 17, 65, bearing(route.points[0], route.points[1]));
    const legs = route.points.slice(1).map((p, i) => metres(route.points[i], p));
    const total = legs.reduce((a, b) => a + b, 0);
    const ms = Math.min(16000, Math.max(6000, total * 12));
    const t0 = performance.now();
    await new Promise<void>((done) => {
      const frame = (t: number) => {
        if (stop.current) return done();
        const want = Math.min(1, (t - t0) / ms) * total;
        let acc = 0, i = 0;
        while (i < legs.length - 1 && acc + legs[i] < want) acc += legs[i++];
        const f = legs[i] ? (want - acc) / legs[i] : 1;
        const a = route.points[i], b = route.points[i + 1];
        const lat = a[0] + (b[0] - a[0]) * f, lon = a[1] + (b[1] - a[1]) * f;
        const target = bearing(a, b), cur = map.getBearing();
        const turn = ((((target - cur) % 360) + 540) % 360) - 180;
        map.jumpTo({ center: [lon, lat], bearing: cur + turn * 0.08, pitch: terrain ? 65 : 0, zoom: 17 });
        if (want >= total) return done();
        requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    });
    setCaption(stop.current ? null : `Arrived: ${route.destination ?? "destination"}.`);
    window.setTimeout(() => setCaption(null), 3500);
  };

  const tour = async () => {
    if (!data) return;
    stop.current = false;
    setSelected(null);
    const steps: { text: string; go: () => Promise<void> }[] = [];
    const [[la0, lo0], [la1, lo1]] = data.bounds;
    steps.push({ text: `This is ${data.site}. Coloured lines are roads: green probably open, orange uncertain, red probably closed.`, go: () => flyTo((la0 + la1) / 2, (lo0 + lo1) / 2, 14.2, 45, -20) });
    for (const a of (data.areas ?? []).filter((x) => x.level)) {
      steps.push({ text: `${a.name}: alert level ${a.level}. ${a.population?.toLocaleString() ?? "?"} people live here, about ${a.vulnerable.toLocaleString()} of them vulnerable.`, go: () => flyTo(a.lat, a.lon, 15.6, 60, 30) });
    }
    const river = stations.find((s) => s.gauges.some((g) => g.type === "water_level"));
    if (river) {
      const g = river.gauges.find((x) => x.type === "water_level")!;
      steps.push({ text: `The river gauge reads ${g.value.toFixed(1)} ${g.unit}${g.threshold != null ? `; the danger line is ${g.threshold} ${g.unit}` : ""}.`, go: () => flyTo(river.lat, river.lon, 16.8, 62, -40) });
    }
    if (data.routes.length) {
      const people = data.routes.reduce((n, r) => n + (r.people ?? 0), 0);
      steps.push({
        text: `${data.routes.length} missions are under way${people ? `, moving ${people} people` : ""}. Blue lines are their routes.`,
        go: async () => {
          const b = new maplibregl.LngLatBounds();
          data.routes.forEach((r) => r.points.forEach((p) => b.extend([p[1], p[0]])));
          mapRef.current?.fitBounds(b, { padding: 120, pitch: terrain ? 55 : 0, bearing: 20, duration: 2200 });
          await new Promise((r) => setTimeout(r, 2300));
        },
      });
    }
    for (const s of data.shelters.filter((x) => (x.expected ?? 0) > 0)) {
      steps.push({ text: `${s.name ?? s.id}: room for ${s.capacity}, ${s.expected} people on their way${s.elevation_m != null ? `, ${Math.round(s.elevation_m)} m above sea level` : ""}.`, go: () => flyTo(s.lat, s.lon, 17, 65, 60) });
    }
    for (const [i, st] of steps.entries()) {
      if (stop.current) break;
      setCaption(`${i + 1}/${steps.length} · ${st.text}`);
      await st.go();
      await new Promise((r) => setTimeout(r, 3800));
    }
    setCaption(null);
  };

  const reset = () => {
    stop.current = true;
    setSelected(null);
    setCaption(null);
    const map = mapRef.current;
    if (!map || !data) return;
    const [[la0, lo0], [la1, lo1]] = data.bounds;
    map.fitBounds([[lo0, la0], [lo1, la1]] as LngLatBoundsLike, { padding: 60, pitch: terrain ? 50 : 0, bearing: -15, duration: 1400 });
  };

  const copyLink = () => {
    const map = mapRef.current;
    if (!map) return;
    const c = map.getCenter();
    const layers = [sat && "sat", terrain && "3d", radar && "radar"].filter(Boolean).join(",");
    const q = `c=${c.lat.toFixed(5)},${c.lng.toFixed(5)}&z=${map.getZoom().toFixed(1)}&p=${Math.round(map.getPitch())}&b=${Math.round(map.getBearing())}&l=${layers}`;
    const url = `${window.location.origin}${window.location.pathname}#/map?${q}`;
    window.history.replaceState(null, "", `#/map?${q}`);
    navigator.clipboard?.writeText(url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); }, () => undefined);
  };

  // ---- search and nearby
  const everything = useMemo(() => {
    if (!data) return [] as { label: string; sub: string; icon: string; pos: [number, number]; sel: Selected }[];
    return [
      ...(data.areas ?? []).map((a) => ({ label: a.name, sub: a.level ? `Alert: ${a.level}` : "Area", icon: "⚠", pos: [a.lat, a.lon] as [number, number], sel: { kind: "area", item: a } as Selected })),
      ...data.shelters.map((s) => ({ label: s.name ?? s.id, sub: `Shelter · room for ${s.capacity}`, icon: "⌂", pos: [s.lat, s.lon] as [number, number], sel: { kind: "shelter", item: s } as Selected })),
      ...(data.teams ?? []).map((t) => ({ label: t.name, sub: t.busy ? "On a mission" : "Available", icon: t.kind === "vehicle" ? "🚐" : "⛑", pos: [t.lat, t.lon] as [number, number], sel: { kind: "team", item: t } as Selected })),
      ...stations.map((s) => ({ label: s.gauges.map((g) => SENSOR_NAME[g.type] ?? g.type).join(", "), sub: "Sensor station", icon: "〰", pos: [s.lat, s.lon] as [number, number], sel: { kind: "station", item: s } as Selected })),
      ...data.routes.map((r) => ({ label: `${r.team ?? "Team"} → ${r.destination ?? "standby"}`, sub: r.people ? `Moving ${r.people} people` : "Mission", icon: "➜", pos: r.points[Math.floor(r.points.length / 2)], sel: { kind: "route", item: r } as Selected })),
    ];
  }, [data, stations]);

  const nearby = useMemo(() => {
    if (!centre) return [];
    return everything.map((x) => ({ ...x, d: metres(centre, x.pos) })).sort((a, b) => a.d - b.d).slice(0, 8);
  }, [everything, centre]);

  const doSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const q = search.trim();
    const ll = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (ll) { void flyTo(Number(ll[1]), Number(ll[2]), 16); return; }
    const hit = everything.find((x) => x.label.toLowerCase().includes(q.toLowerCase()) || x.sub.toLowerCase().includes(q.toLowerCase()));
    if (hit) setSelected(hit.sel);
  };

  // ---- the situation read-out
  const river = data?.gauges.find((g) => g.type === "water_level");
  const activeArea = data?.areas?.find((a) => a.level);
  const moving = data?.routes.reduce((n, r) => n + (r.people ?? 0), 0) ?? 0;
  const liveAlert = alerts.data?.find((a) => a.status === "active");

  if (error) return <div className="page"><div className="err">{error}</div></div>;
  return (
    <div className="page flush">
      <div className="mapwrap">
        <div ref={holder} className="map" />

        <div className="hud">
          <div className="hud-title">Situation now · {data?.site ?? "…"}</div>
          <div className="hud-row">
            <span>Alert level</span>
            <b style={{ color: activeArea?.level ? LEVEL_COLOUR[activeArea.level] : undefined }}>{activeArea?.level ? activeArea.level.toUpperCase() : "Normal"}</b>
          </div>
          {river && (
            <div className="hud-row">
              <span>River level</span>
              <b style={{ color: river.threshold != null && river.value >= river.threshold ? "#dc2626" : undefined }}>
                {river.value.toFixed(1)} {river.unit}{river.threshold != null && <small> / danger {river.threshold}</small>}
              </b>
            </div>
          )}
          <div className="hud-row"><span>Roads probably closed</span><b>{data?.likely_closed ?? "—"}</b></div>
          <div className="hud-row"><span>People being moved</span><b>{moving}</b></div>
          <div className="hud-row"><span>Missions under way</span><b>{data?.routes.length ?? "—"}</b></div>
          {liveAlert && <div className="hud-alert">Public alert out: {liveAlert.texts?.en?.headline ?? `${liveAlert.event} · ${liveAlert.area}`}</div>}
          {(approvals.data?.length ?? 0) > 0 && <a className="hud-decide" href="#/decisions">{approvals.data!.length} decision{approvals.data!.length > 1 ? "s" : ""} waiting for you →</a>}
        </div>

        <div className="mapbar">
          <form onSubmit={doSearch}><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find a shelter, team, place or 6.68, 80.40" aria-label="Search the map" /></form>
          <div className="seg">
            <button className={!sat ? "on" : ""} onClick={() => setSat(false)}>Map</button>
            <button className={sat ? "on" : ""} onClick={() => setSat(true)}>Satellite</button>
          </div>
          <button className={`tb ${terrain ? "on" : ""}`} onClick={() => setTerrain(!terrain)} title="Show hills and valleys in 3D">3D hills</button>
          <button className={`tb ${radar ? "on" : ""}`} onClick={() => setRadar(!radar)} title="Live rain radar">Rain radar</button>
          <button className="tb" onClick={() => mapRef.current?.easeTo({ bearing: 0, pitch: terrain ? 45 : 0, duration: 800 })} title="Put north at the top">North up</button>
          <button className="tb" onClick={reset} title="Back to the whole area">Reset</button>
          <button className="tb primary" onClick={() => void tour()} title="A guided fly-through of the situation">▶ Tour</button>
          <button className="tb" onClick={copyLink} title="Copy a link to this exact view">{copied ? "Link copied" : "Share view"}</button>
        </div>

        {radarNote && <div className="map-note">{radarNote}</div>}

        <div className="nearby">
          <div className="hud-title">Near the centre of the map</div>
          {nearby.map((n, i) => (
            <button key={i} className="near" onClick={() => setSelected(n.sel)}>
              <span className="near-i">{n.icon}</span>
              <span className="near-t"><b>{n.label}</b><small>{n.sub}</small></span>
              <span className="near-d">{distText(n.d)}</span>
            </button>
          ))}
        </div>

        {selected && <Details sel={selected} close={() => { stop.current = true; setSelected(null); }} fly={flyAlong} />}

        {caption && (
          <div className="caption">
            <span>{caption}</span>
            <button className="tb" onClick={() => { stop.current = true; setCaption(null); }}>Stop</button>
          </div>
        )}

        {showLegend ? (
          <div className="legend-card">
            <div className="row" style={{ justifyContent: "space-between" }}><b>What the colours mean</b><a onClick={() => setShowLegend(false)}>hide</a></div>
            <div><i className="sw" style={{ background: "#16a34a" }} /> Road probably open</div>
            <div><i className="sw" style={{ background: "#d97706" }} /> Road uncertain</div>
            <div><i className="sw" style={{ background: "#dc2626" }} /> Road probably closed</div>
            <div><i className="sw" style={{ background: "#94a3b8" }} /> No reports about this road yet</div>
            <div><i className="sw" style={{ background: "#2563eb" }} /> Evacuation route (moving)</div>
            <div className="muted sm">Fainter means less sure. ⌂ shelter · 🚐 ⛑ team · 〰 sensor · ⚠ affected area</div>
          </div>
        ) : (
          <button className="legend-open tb" onClick={() => setShowLegend(true)}>Legend</button>
        )}
      </div>
    </div>
  );
}

function Details({ sel, close, fly }: { sel: Selected; close: () => void; fly: (r: Route) => void }) {
  let title = "", body: React.ReactNode = null;
  if (sel.kind === "area") {
    const a = sel.item;
    title = a.name;
    body = (
      <>
        <p>{a.level ? <>Alert level <b style={{ color: LEVEL_COLOUR[a.level] }}>{a.level}</b>. ATLAS opened an incident here.</> : "No incident here right now."}</p>
        <p>{a.population?.toLocaleString() ?? "?"} people live here; about {a.vulnerable.toLocaleString()} are vulnerable (elderly, children, informal housing).</p>
      </>
    );
  } else if (sel.kind === "shelter") {
    const s = sel.item;
    title = s.name ?? s.id;
    const free = s.capacity - (s.occupancy ?? 0) - (s.expected ?? 0);
    body = (
      <>
        <p>Room for <b>{s.capacity}</b> people. <b>{s.expected ?? 0}</b> are on their way now.</p>
        <p>{s.occupancy != null ? <>Recorded inside: <b>{s.occupancy}</b>.</> : "Nobody has reported how many are inside yet."} Space left after arrivals: <b>{free}</b>.</p>
        {s.elevation_m != null && <p className="muted">{Math.round(s.elevation_m)} m above sea level.</p>}
      </>
    );
  } else if (sel.kind === "team") {
    const t = sel.item;
    title = t.name;
    body = <p>{t.kind === "vehicle" ? "Vehicle" : "Rescue team"}. <b>{t.busy ? "On a mission now." : "Available."}</b> Shown at its base.</p>;
  } else if (sel.kind === "station") {
    title = "Sensor station";
    body = (
      <table className="t">
        <tbody>
          {sel.item.gauges.map((g) => (
            <tr key={g.node + g.type}>
              <td>{SENSOR_NAME[g.type] ?? g.type}</td>
              <td><b>{g.value.toFixed(1)} {g.unit}</b></td>
              <td>{g.threshold != null && (g.value >= g.threshold ? <span className="chip bad">above danger line ({g.threshold})</span> : <span className="chip ok">below danger line ({g.threshold})</span>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  } else if (sel.kind === "route") {
    const r = sel.item;
    title = r.people ? `Evacuating ${r.people} people` : r.title;
    body = (
      <>
        <p><b>{r.team ?? "A team"}</b> {r.destination ? <>to <b>{r.destination}</b></> : "on standby"}. Status: <b>{missionStatus(r.status)}</b>{r.progress ? `, ${r.progress}% done` : ""}.</p>
        <p className="muted">{readable(r.title)}</p>
        <button className="btn primary" onClick={() => fly(r)}>Fly along this route</button>
      </>
    );
  } else {
    const e = sel.item, s = roadState(e);
    title = "Road";
    body = e.belief_open === null && !e.closed_by_decision
      ? <p>No sensor or field report has mentioned this road yet, so ATLAS assumes it is usable, with low confidence.</p>
      : <p><b style={{ color: s.colour }}>{s.text}</b>: {pct(e.belief_open)} likely to be passable. How sure: {pct(e.confidence)}{e.closed_by_decision ? ". A person closed it." : "."}</p>;
  }
  return (
    <div className="details">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b style={{ fontSize: 16 }}>{title}</b>
        <button className="tb" onClick={close} aria-label="Close">✕</button>
      </div>
      {body}
    </div>
  );
}

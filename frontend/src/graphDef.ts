// The agent graph layout. Positions are fixed so the picture is the same every time and people learn it:
// data flows left to right (sense, plan, control, act), with A3 in the middle.
export type Layer = "sense" | "plan" | "control" | "act" | "world";

export interface NodeDef {
  id: string;
  title: string;
  sub: string;
  layer: Layer;
  x: number;
  y: number;
}

export const NODES: NodeDef[] = [
  { id: "sensors", title: "Sensors", sub: "river · rain · soil · LoRa", layer: "world", x: 0, y: 200 },
  { id: "feeds", title: "Feeds", sub: "official reports", layer: "world", x: 0, y: 330 },
  { id: "a1_sentinel", title: "A1 Sentinel", sub: "detect and verify", layer: "sense", x: 190, y: 200 },
  { id: "a2_hazard_intel", title: "A2 Hazard", sub: "risk and people", layer: "sense", x: 390, y: 30 },
  { id: "a5_triage", title: "A5 Triage", sub: "who first", layer: "plan", x: 390, y: 120 },
  { id: "a4_evacuation", title: "A4 Evacuation", sub: "safe routes", layer: "plan", x: 390, y: 210 },
  { id: "a7_shelter", title: "A7 Shelter", sub: "where to", layer: "plan", x: 390, y: 300 },
  { id: "a6_logistics", title: "A6 Logistics", sub: "who goes", layer: "plan", x: 390, y: 390 },
  { id: "a3_incident_commander", title: "A3 Commander", sub: "reconcile · plan · replan", layer: "plan", x: 600, y: 210 },
  { id: "action_gateway", title: "Action Gateway", sub: "policy · ledger", layer: "control", x: 800, y: 210 },
  { id: "human", title: "Human IC", sub: "approve · edit · reject", layer: "control", x: 800, y: 60 },
  { id: "a8_dispatch", title: "A8 Dispatch", sub: "missions", layer: "act", x: 1000, y: 130 },
  { id: "a9_comms", title: "A9 Comms", sub: "alerts · sitreps", layer: "act", x: 1000, y: 300 },
];

export const LAYER_COLOR: Record<string, string> = {
  sense: "var(--sense)",
  plan: "var(--plan)",
  control: "var(--control)",
  act: "var(--act)",
  world: "var(--world)",
};

export const LAYER_ICON: Record<string, string> = { sense: "S", plan: "P", control: "C", act: "A", world: "W" };

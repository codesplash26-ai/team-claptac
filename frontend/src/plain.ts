// Plain-language wording for people who run a disaster, not the system. Ids stay ids in the API; only
// what is shown changes.

export interface Mission {
  id: string;
  incident_id: string;
  title: string;
  team: string;
  status: string;
  progress: number;
  priority: string;
  destination: string | null;
  resources_committed: number;
  exception: string | null;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
}

export interface AlertText { headline: string; body: string; instruction: string; event?: string }
export interface Alert {
  id: string;
  incident_id: string;
  status: string;
  area: string;
  event: string;
  severity: string;
  msg_type: string;
  texts: Record<string, AlertText>;
  sent?: string;
  created_at?: string;
}
export interface Sitrep { id: string; incident_id: string; generated_at: string; texts: Record<string, string> }
export interface Incident { incident_id: string; first_seen: string; last_seen: string; events: number; pending_approvals: number; active_missions: number }

/** 'ratnapura-shelter-01' -> 'Shelter 01'. */
export function friendly(id: string | null | undefined): string {
  if (!id) return "";
  const key = id.replace(/^incident:/, "").replace(/^[a-z]+(?:-fort)?-/, "");
  const words = key.replace(/-/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : id;
}

export function teams(team: string): string {
  return team.split(",").map((t) => friendly(t.trim())).filter(Boolean).join(" and ");
}

export function place(incidentId: string): string {
  const key = incidentId.replace(/^incident:/, "");
  if (key === "ratnapura-demo-river") return "Ratnapura riverside settlements";
  return friendly(key);
}

const STATUS: Record<string, string> = {
  created: "Sent to the team",
  acknowledged: "Team has confirmed",
  in_progress: "Under way",
  completed: "Done",
  cancelled: "Cancelled",
  failed: "Could not be done",
  exception: "Needs attention",
};
export const missionStatus = (s: string) => STATUS[s] ?? s.replace(/_/g, " ");

const LANG: Record<string, string> = { en: "English", si: "සිංහල", ta: "தமிழ்" };
export const languageName = (code: string) => LANG[code] ?? code;

/** What an action will do, in one sentence. */
export function describeAction(action: { action_type: string; params: Record<string, unknown> }): string {
  const p = action.params as Record<string, string | number | null | undefined>;
  switch (action.action_type) {
    case "publish_alert": {
      const hazard = typeof p.event === "string" && p.event ? `${p.event} ` : "";
      const what = p.instruction === "evacuate" ? `a ${hazard}evacuation alert` : p.instruction === "avoid_area" ? `a ${hazard}alert telling people to avoid the area` : `a ${hazard}public alert`;
      return `Publish ${what} for ${p.area ?? "the area"} (${p.severity ?? "severity not set"}, ${p.scope === "district" ? "whole district" : "local"}), in Sinhala, Tamil and English.`;
    }
    case "create_mission": {
      const people = Number(p.people ?? 0);
      return people
        ? `Send ${teams(String(p.team ?? "a team"))} to move ${people} people to ${friendly(String(p.destination ?? "a shelter"))}.`
        : `${String(p.title ?? "Create a mission")} (${teams(String(p.team ?? ""))}).`;
    }
    case "cancel_mission": return `Cancel mission ${p.mission_id ?? ""}.`;
    case "close_major_route": return `Close a major road (${p.edge ?? p.road ?? "road"}).`;
    case "cancel_alert": return "Withdraw a public alert that is already out.";
    case "publish_sitrep": return "Publish a situation report.";
    default: return action.action_type.replace(/_/g, " ");
  }
}

/** Why a person has to decide, in one sentence. */
export function whyHuman(action: { action_type: string; params: Record<string, unknown> }): string {
  const p = action.params as Record<string, unknown>;
  if (action.action_type === "publish_alert") return "Every evacuation or district-wide alert reaches the public, so a person must approve it.";
  if (action.action_type === "create_mission" && Number(p.resources_committed ?? 0) > 2) return `It commits ${p.resources_committed} teams or vehicles at once, above the limit of 2 the system may commit on its own.`;
  if (action.action_type === "cancel_mission") return "Cancelling work already sent to a team needs a person.";
  if (action.action_type === "close_major_route") return "Closing a major road affects everyone, so a person decides.";
  return "The safety rules say this action needs a person.";
}

export function ago(iso: string | number | null | undefined): string {
  if (iso == null) return "";
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString();
}

const PART: Record<string, string> = {
  plans: "", a2_hazard_intel: "Hazard", a4_evacuation: "Evacuation plan", a5_triage: "Priorities",
  a6_logistics: "Teams and supplies", a7_shelter: "Shelter plan", missions: "Mission", summary: "Summary",
};

const AGENT: Record<string, string> = {
  "1": "The sensor watch", "2": "The hazard assessment", "3": "The incident commander", "4": "Evacuation planning",
  "5": "Triage", "6": "Logistics", "7": "Shelter planning", "8": "Dispatch", "9": "Public communications",
};

/** Swap ids and agent codes inside free text for readable words. */
export function readable(text: string): string {
  return text
    .replace(/incident:([a-z0-9-]+)/g, (_, k) => place(`incident:${k}`))
    .replace(/area:([a-z0-9-]+)/g, (_, k) => place(k))
    .replace(/\bA2 rates ([a-z0-9-]+) (\w+) for (\w+)\.?/g, (_, a, level, hazard) => `The hazard assessment rates ${place(a)} at "${level}" for ${hazard}.`)
    .replace(/weakest road belief (\d\.\d+)/g, (_, b) => `riskiest road on the way ${Math.round(Number(b) * 100)}% likely open`)
    .replace(/\((\d+) min,/g, "($1 min away,")
    .replace(/(\d+) unit\(s\)/g, (_, n) => `${n} ${n === "1" ? "unit" : "units"}`)
    .replace(/\b[a-z]+(?:-fort)?-(shelter|vehicle|rescue-team)-(\d+)\b/g, (_, kind, n) => `${friendly(`x-${kind}`)} ${n}`)
    .replace(/\bc(\d+): /g, "Update $1: ")
    .replace(/\bA(\d)\b/g, (_, n) => AGENT[n] ?? `Agent ${n}`)
    .replace(/replanning: trigger_escalation/g, "the situation got worse, so the plan was redone")
    .replace(/\(high\)/g, "(urgent)")
    .replace(/\(cycle (\d+)\)/g, "(update $1)")
    .replace(/\b[a-z0-9]+-demo-river\b/g, (k) => place(k));
}

/** One recorded fact ("subject · path: value") as a readable line. */
export function plainFact(line: string): { what: string; value: string } {
  const m = line.match(/^(\S+)\s+[·\uFFFD]\s+(.+?):\s+([\s\S]*)$/);
  if (!m) return { what: "", value: readable(line) };
  const [, subject, path, value] = m;
  const bits = path.split(".").flatMap((p) => {
    const idx = p.match(/^(.*)\[(\d+)\]$/);
    const name = idx ? idx[1] : p;
    const group = name.match(/^g(\d+)$/);
    const label = group ? `Group ${group[1]}` : name.startsWith("rescue:") ? "Rescue standby" : name in PART ? PART[name] : name.replace(/_/g, " ");
    return [label + (idx ? ` ${Number(idx[2]) + 1}` : "")].filter(Boolean);
  });
  const who = subject.startsWith("incident:") || subject.startsWith("area:") ? place(subject.replace(/^area:/, "")) : friendly(subject.split(":").pop());
  return { what: `${who} › ${bits.join(" › ")}`, value: readable(value.replace(/^"|"$/g, "")) };
}

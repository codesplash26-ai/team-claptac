# Demo guide

A browser demo of ATLAS, click by click, with what each screen shows and what to say. It takes
about ten minutes. Everything runs on the laptop: no internet is needed except for the map's
background imagery and the rain radar.

## Before you start

```bash
docker compose down -v          # a clean start (deletes the last demo's state)
docker compose up --build
```

Open <http://localhost:8000/> (it lands on **Overview**). No login is needed: the console runs in open
demo mode. (Without Docker: `pip install -r requirements.txt`, `uvicorn atlas.api.main:app`, and delete
`data/atlas.db` for a clean start.)

Know one detail before you present: the river level is shown at the scale of our tabletop flood
model, where **1 cm on screen is 20 cm of real river**. The replay uses the real Ratnapura gauge
record from Cyclone Ditwah; its real peak of 10.2 m shows as about 51 cm. The danger line on screen
(25 cm) is 5 m of real river.

---

## 1. Overview: "What is happening, and what needs me?"

**Click:** Overview (it is the first page).

**What you see:** a green "All quiet" banner, four counters (decisions waiting, missions, public
alerts, readings above the danger line), and the sensors with small graphs.

**Say:** "This is what a district officer opens first. It answers two questions: what is happening,
and what needs me. Everything here updates by itself."

## 2. Start a real disaster

**Click:** Practice drills → speed **30×** → *Ratnapura, Cyclone Ditwah floods, November 2025* →
**Start this drill**. Then click **Watch the overview**.

**What you see:** a blue "Practice drill running" banner. Within a minute the river graph climbs past
the red dashed danger line, the banner turns orange ("An incident is open, and 2 decisions are
waiting for you"), and missions appear.

**Say:** "This is the real river record from Cyclone Ditwah last November, replayed thirty times
faster. Nobody pressed anything: the sensor watch noticed three independent warnings (rain, soil and
river), opened an incident, and the planning agents worked out who to move, where, and with which
vehicles. Routine work, like sending vehicles within limits, it does on its own. The soil saturated
before the river rose, so it warns of a landslide first and a flood after; Ditwah caused both."

## 3. The map

**Click:** Map.

**What you see:**

- **Top left, "Situation now":** alert level, river level against the danger line, roads probably
  closed, people being moved, missions under way.
- **The shaded circle:** the affected area, coloured by alert level (red is severe).
- **Coloured roads:** green probably open, orange uncertain, red probably closed, grey no reports yet.
  Fainter means less sure. ATLAS never assumes a road is fine just because nobody reported it.
- **Blue moving lines:** evacuation routes; the dashes flow toward the shelter.
- **Markers:** ⌂ shelters (with "120 coming"), 🚐 ⛑ vehicles and rescue teams, 〰 sensors, ⚠ areas.
- **Right, "Near the centre of the map":** everything close to where you are looking.

**Click, in this order:**

1. **▶ Tour.** A narrated fly-through: the area and its population, the river gauge, the routes,
   the shelter. Captions appear at the top. Say nothing, let it run (about 30 seconds).
2. **Satellite.** The same picture over satellite imagery; **3D hills** shows why routes bend (Ratnapura is
   in a valley).
3. **Rain radar.** Live rain over Sri Lanka right now (needs internet).
4. Type **moving** in the search box and press Enter. A route lights up gold with a card:
   "Evacuating 40 people · Vehicle 04 to Shelter 01".
5. **Fly along this route.** The camera follows the route like a drone to the shelter.
6. Click a **red road**. The card says in words how likely it is to be passable and how sure ATLAS is.
7. Click the **sensor** marker. River level, rainfall and soil wetness against their danger lines.

**Say:** "This is the same information the agents use, drawn so anyone can read it. The colours are
the system's belief about each road, not a guess dressed up as a fact. When it is unsure it says so,
and the fainter line shows it."

## 4. Decisions: the human in the loop

**Click:** Decisions (the menu shows an orange badge).

**What you see:** two cards, "Publish a landslide evacuation alert…" and "Publish a flood
evacuation alert for Ratnapura riverside settlements (Moderate, local), in Sinhala, Tamil and
English", each with **Why ATLAS suggests it** and **Why it needs you**. Once decisions have been
made, four figures at the top show how much ATLAS did on its own, how many went to a person, how
many were approved or rejected, and how long a decision typically takes.

**Click:** type a name in **Your name**, then **✓ Approve** on each.

**Say:** "ATLAS will not warn the public by itself. Anything that reaches the public, or commits
more than two teams at once, waits here. The person's name and decision go into a record that
cannot be edited afterwards. If nobody answers, nothing goes out."

## 5. Alerts & reports

**Click:** Alerts & reports. Switch between **English**, **සිංහල** and **தமிழ்**.

**What you see:** the alert that just went out, in all three languages, and a link to download it in
CAP, the standard format warning systems and broadcasters accept. On the right, the situation
reports ATLAS wrote after each planning round.

**Say:** "Alert wording comes from fixed, checked phrasing in each language, never from free AI
text, so a translation mistake cannot reach the public."

## 6. Teams & missions

**Click:** Teams & missions.

**What you see:** each mission with its team, destination and a progress bar. A red note appears if
a team has not confirmed within five minutes.

**Click:** **Team confirmed**, then **Report progress**, then **Mark done** on one mission.

**Say:** "This is where the operations room records what teams report by radio. It feeds back in: a
mission that fails makes ATLAS replan."

## 7. Ask ATLAS

**Click:** Ask ATLAS → the suggested question **How many people are being evacuated?**

**What you see:** the matching records (three groups of 40), and below, ATLAS's own reading of the
situation with a green count of statements checked against the records.

**Say:** "It only answers from what it has recorded. With an AI model connected it writes prose, but
every statement is still checked, and anything it cannot back up is struck out. If it mentions a
place that does not exist, like 'Shelter 99', it is flagged instead of shown as fact."

## 7b. Landslide and earthquake

**Click:** Practice drills → *Ratnapura slope failure after heavy rain (made-up example)* → **Start
this drill**, then Map and Decisions. Or the earthquake drill.

**What you see:** a slope sensor's soil wetness and ground movement rise, an official landslide
warning arrives, ATLAS opens a **landslide** incident, treats steep roads as blocked, and asks
before sending a landslide alert that says "move away from slopes and hillsides". The earthquake
drill does the same for a magnitude 6.8 quake: "stay away from damaged buildings and steep slopes".

**Say:** "Different hazards, different advice to the public. A flood alert says go to higher
ground; that would be the wrong thing to say in a landslide."

## 7c. Field radio (optional, with the LoRa gateway)

On a machine with the repository, run the gateway in mock-radio mode against the running system
(see `hardware/lora/README.md`). **Overview → Field radio (LoRa)** then shows each node: when it
was last heard, its signal (good, fair or weak), how many messages arrived, any gaps, and how many
frames were rejected for a bad signature.

## 8. Prove the safety rules

**Click:** Practice drills → **Run the safety test**.

**What you see:** "16 of 16 handled correctly". Open **See each attempt** for the list: an agent acting
outside its job, skipping approval, using stale information, repeating an order, and more.

**Say:** "These are the ways an automated system could go wrong. Each one is refused by the rules
in code, not by asking the AI nicely."

## 9. For the technical judges (optional)

Open **For technical staff** in the menu:

- **Runs and traces:** open the incident. Every step, which agent, how long it took, and what caused it.
- **Agent graph:** the agents and the messages between them; replay the run.
- **Event timeline:** every message, filterable, with the reasoning and evidence behind each.
- **Evaluations:** **Evaluate live traces now**. About 18 automatic checks, 0 critical failures.
- **Agents:** pause an agent (for example Logistics) and resume it; nothing is lost.
- **Benchmarks:** ten repeatable exercises, each with a full recording.

---

## If something goes wrong

| Problem | Do this |
|---|---|
| The map background is blank | No internet. The roads, routes and markers still draw; say it works offline. |
| Rain radar says it needs internet | Skip that step. |
| Nothing happens after starting a drill | Check **Practice drills** shows "Drill running"; if another drill finished, start again. |
| An old incident is on screen | `docker compose down -v`, then `docker compose up` (without Docker: stop, delete `data/atlas.db`, start). |
| "You are not signed in" on Decisions | Access codes are set (`ATLAS_TOKENS`). Use **sign in** at the bottom of the menu. |

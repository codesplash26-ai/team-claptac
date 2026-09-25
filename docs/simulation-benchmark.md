# ATLAS simulation benchmark

Run ten synthetic response exercises against the actual local A1–A9 system. Each task gets a fresh runtime, simulated clock, policy engine, COP, planner and local action executors. Results never enter the live responder database. The benchmark runs without API keys, network access or radio hardware.

## Run and inspect

In the manager, open **Benchmarks**, select scenarios and seeds, choose **Fast** or **Watch**, then run. Select **Watch timeline** during a case or **Explore timeline** afterward. The inspector shows recorded inputs, handler/executor durations, causal links, policy decisions, planner alternatives, advisor explanations and cited evidence. It shows completed executions, not a live stream of internal model thoughts. A case's checkpoint refreshes after each input.

The results screen shows all checks, observed values, mission counts, replans and agent coverage. **Download all traces & reports (.zip)** produces a portable bundle: unzip it and open `report.html`. Each case has a searchable HTML event timeline and links to raw files. Download buttons support the manager's operator token.

From the repository root, with its Python dependencies installed:

```sh
.venv/bin/python -m atlas.benchmark validate
.venv/bin/python -m atlas.benchmark run --seeds 42,43,44
.venv/bin/python -m atlas.benchmark run --task 07-conflicting-reports --seeds 42 --step-delay-ms 500
.venv/bin/python -m atlas.benchmark run --seeds 42 --no-random
```

Default output: `outputs/benchmarks/<UTC timestamp>-<unique suffix>/`. Use `--output <new-directory>` to choose a destination; existing directories are never overwritten. Runs saved outside the default root are CLI artifacts and do not appear in the web history. Exit code is zero only when every case completes and passes its checks. `--tasks <directory>` loads another task collection. One API run can be active per server process, with a maximum of 100 cases (up to ten seeds); use a single API worker for this local benchmark service.

**Stop run & save partial traces** interrupts between inputs and keeps a report, JSONL traces and checkpoint. A cancelled case cannot pass. Shutting down the API requests a graceful stop too. After an abrupt process kill, previously flushed JSONL/checkpoints remain; the UI labels an orphaned running run interrupted. Final reports/ZIP are not guaranteed after an abrupt kill. CLI runs are separate processes; the web service cannot control them and may label an unfinished CLI run interrupted.

## The ten tasks

| Task ID | Situation | Main checks |
| --- | --- | --- |
| `01-quiet-watch` | Ordinary river readings plus background traffic | No trigger or mission |
| `02-river-evacuation` | Confirmed river rise and responder updates | Planning, local alerts, completed mission |
| `03-flash-flood` | Rapid rise and modelled road closures | Closed-road beliefs, replanning |
| `04-heavy-rain` | Rainfall crosses the demo threshold | Trigger, proposal, recorded alert |
| `05-saturated-slope` | Soil saturation with rain | Landslide risk update and response loop |
| `06-link-recovery` | Stale reading, reconnection and retransmission | Stale data rejected, duplicate suppressed |
| `07-conflicting-reports` | Sensor and resident report disagree | Conflict flag and recorded planner simulation |
| `08-full-shelters` | All demonstration shelters already full | Unplaced groups, no overcapacity plan |
| `09-fleet-and-failure` | Small mixed fleet, then a mission fails | Unserved jobs, retained failure, replanning |
| `10-human-command` | Missions require review; one is rejected | Escalation, rejection, approval and safe execution |

Every case also checks the hash-linked ledger, handler/executor errors, recorded execution of blocked/rejected/unapproved high-consequence actions, and shelter capacity within each individual plan. Missing **known counters** mean zero; unknown metric names are rejected when loading the task.

## Add a scenario task

1. Copy `data/benchmarks/task-template.json` to `data/benchmarks/tasks/<unique-id>.json`. The template is a worked, runnable river-rise exercise.
2. Set a unique lowercase `id`, descriptive `title`, `description`, and honest `assumptions` describing synthetic data and what is mocked. Files in the task directory must be valid tasks; keep drafts elsewhere.
3. Choose `initial` conditions, add time-ordered `steps`, configure random events, and state measurable `expectations`. Use the documented metrics below or the schema's metric enum.
4. Validate and run just your task. Review its input log and outcomes before running multiple seeds. An expectation should detect incorrect behaviour; do not change the expected value just to hide a failure.
5. Share the task JSON and benchmark bundle. Record assumptions and reproducible failures in review. The server reads the catalogue on request, so a new file needs a page refresh, not a code rebuild.

```sh
cp data/benchmarks/task-template.json data/benchmarks/tasks/my-response-task.json
# Edit the copied task before validating.
.venv/bin/python -m atlas.benchmark validate
.venv/bin/python -m atlas.benchmark run --task my-response-task --seeds 42,43,44
```

Use `data/benchmarks/task.schema.json` for editor validation. Regenerate after changing the Python schema:

```sh
.venv/bin/python -m atlas.benchmark schema > data/benchmarks/task.schema.json
```

### Task fields

All unknown fields are rejected. Current tasks target the cached `ratnapura` site and `ratnapura-demo-river` area; extending geography requires implementation, not just renaming these fields.

- `schema_version`: `1`.
- `id`: lowercase letters, numbers, `_` or `-`, maximum 80 characters. Step IDs obey the same format and must be unique. The `random-` prefix is reserved for injected inputs.
- `initial.shelter_occupancy_fraction`: 0–1, applied separately to every shelter (default 0).
- `initial.available_resource_fraction`: 0–1, applied **within each resource kind**, retaining the first IDs in sorted order; count is rounded with Python `round` (default 1). This keeps a limited fleet from accidentally containing rescuers but no vehicles.
- `initial.strict_mission_approval`: when true, each mission requires human review (default false).
- `initial.auto_review`: `approve`, `reject`, or `ignore`. Default `approve` scripts approval after each input. Use `ignore` with explicit `review` steps to test pending/rejected actions.
- `steps`: 1–200 entries, ordered by receive time. `at_seconds` is 0–7200 relative to the fixed exercise epoch, 2026-09-20 00:00 UTC. Equal-time entries retain file order. Optional `note` describes the situation.
- `random_events`: `count` 0–30, `kinds`, `start_seconds`, `end_seconds` (ordered, 0–7200). The final random input may extend simulated duration past the last scripted step.
- `expectations`: 1–30 checks with a known `metric`, `operator` (`eq`, `gte`, `lte`), numeric `value` and explanatory `reason`.

### Step types

| `kind` | Required / optional fields | Effect |
| --- | --- | --- |
| `sensor` | `sensor_type`, `value`; optional `node_id`, `observed_seconds` | Sends a real sensor-ingestion event. Water uses cm, rain mm, soil moisture %, temperature C. Soil values are 0–100. Other values are nonnegative and capped at 10000. |
| `report` | `attribute`, `value`; optional `source`, `report_kind`, `text`, `observed_seconds` | Sends a structured field report or official warning through report ingestion. Attributes: water_level, rainfall, soil_moisture. |
| `duplicate` | `target` | Republishes the exact event from an earlier sensor/report step; records whether it was suppressed. |
| `mission` | `change`: acknowledge/progress/complete/fail; optional `limit`, `percent` | Calls the actual dispatch transition on eligible missions. No eligible mission is explicitly logged as skipped, never fabricated. Failure is fatal and publishes a mission-status event. |
| `review` | `decision`: approve/reject; optional `limit` | Reviews pending requests as `simulated-commander`, using the real approval gateway. |
| `checkpoint` | no extra fields | Advances time and drains the control loop, including mission deadlines. |

`observed_seconds` differs from receive time to simulate delayed/out-of-order or future-dated observations. It defaults to `at_seconds`. Synthetic mission updates are applied in the dispatch store's returned order. Limit is 1–100. The benchmark drains control work to a bounded fixed point after each input; it does not assume background workers have finished.

### Random events and reproduction

Built-in tasks inject five extra inputs per seed: ambient temperature readings, stale water observations, and exact retransmissions. The template also supports `field_report` for low-water resident reports that may contradict the sensors. An attempted random duplicate with no earlier sensor/report becomes an ambient reading. The frozen schedule is the authoritative record.

Random generation uses a task-local `random.Random` seeded from SHA256 of task ID plus integer seed. Identical task/configuration and seed produce the same input schedule; wall-clock timings, trace UUIDs and other runtime metadata are not guaranteed byte-identical. Changing a task ID changes its random stream. `--no-random` removes all injected inputs. Playback delay changes presentation speed only; it does not alter simulated timestamps.

The manifest includes task hashes, Python version and hashes of local Python sources, configuration and reference/road data. Each case saves its resolved settings and policy. The schedule includes its SHA256. Compare on the same code/configuration and dependency versions; the manifest is provenance, not a complete environment lockfile.

### Metrics

The full accepted list is in `task.schema.json` (`$defs.Expectation.properties.metric.enum`). Useful groups:

- `events.<event_type>`: number of recorded events of a known ATLAS event type.
- `inputs.total`, `inputs.random`, `inputs.duplicates`: attempted steps, injected steps, suppressed duplicate attempts.
- `missions.total`, `missions.<status>`: final mission counts by status.
- `actions.total`, `actions.escalated_total`, `actions.<status>`, `approvals.<status>`: final action/review counts; escalation total counts emitted escalation events.
- `incidents.total`, `incidents.replans`: recorded incident count and total replans.
- `roads.closed`: distinct COP edge subjects marked closed explicitly or below A4's close threshold at the end of the case.
- `shelters.unplaced`, `logistics.unserved`: entries summed **across planning cycles**, not distinct people/jobs. `shelters.over_capacity`: individual plans that put a shelter over capacity.
- `hazards.landslide_updates`: recorded landslide risk updates.
- `reasoning.planner_runs`, `reasoning.advice`, `reasoning.accepted_claims`, `reasoning.rejected_claims`: recorded planning and advisor output counts. Most tasks can use deterministic decisions without invoking the uncertainty planner.
- `integrity.ledger_valid`, `integrity.execution_errors`, `integrity.unsafe_executions`: universal integrity checks.
- `timing.execution_p95_ms`: measured span duration percentile across handler and executor invocations. Hardware/load affect it; it is not total incident response time.

`metrics.agent_activity` also records invocation counts. Coverage means an agent executed; some handlers correctly emit no output. All nine agents are exercised across the suite; no claim is made that each task must execute every agent.

## Files and persistence

Suite: `manifest.json`, `status.json`, `summary.json`, `results.csv`, `report.html`, `artifacts.zip`.

Each `<task-id>--seed-<seed>/` contains:

| File | Content |
| --- | --- |
| `task.json`, `configuration.json`, `schedule.json` | Resolved task, runtime settings/policy, seed and exact inputs |
| `inputs.jsonl` | Every attempted step, result/event IDs, skipped transitions and errors |
| `events.jsonl` | Complete emitted event envelopes and ledger sequence numbers |
| `ledger.jsonl` | Full hash-linked audit records including beliefs, decisions and explanations |
| `spans.jsonl` | Measured agent input/output work, timings, status and causal chain |
| `state.sqlite` | Atomic complete checkpoint after every input; the web inspector reads it read-only |
| `timeline.json`, `reasoning.json`, `evidence.json` | Enriched event timeline, recorded planner simulations, frozen advisor evidence |
| `final-state.json`, `result.json`, `report.html` | Final state, observed checks/metrics, portable case report |

JSONL is appended and closed after every input; SQLite checkpoints are replaced atomically. Successful, failed and cancelled runs are retained. Outputs are git-ignored and **not automatically deleted**. Copy bundles somewhere durable when sharing results. Failed experiments stay visible rather than being overwritten by corrected runs.

## Scope and limitations

These are real software executions inside synthetic exercises. They use cached road geometry with synthetic populations, shelters, resources and demonstration thresholds; they are not replayed ground-truth disasters or validated forecasts. Delays/duplicates model ingestion behaviour, not LoRa physical performance. Human approvals, responder progress and vehicle failures are scripted. A1's planner uses its local simulation model. The advisor is explicitly offline even if the main app has a configured provider; the benchmark does not evaluate an external LLM or incur model charges. Field-report steps use structured ingestion rather than free-text extraction.

No public alert is sent. Planning availability/capacity is based on recorded state; these tests do not prove global physical resource reservation, real route accessibility, successful evacuation, model quality or deployment readiness. The slope exercise checks the existing limited landslide risk output; it does not implement a calibrated landslide model. The ten tasks are regression/simulation coverage with declared assertions, not an accuracy leaderboard.

## API and permissions

- `GET /benchmark/tasks`, `/template`, `/schema`: `scenario:read`.
- `POST /benchmark/runs`: `scenario:run`; body includes `task_ids`, `seeds`, `random_enabled`, `step_delay_ms`.
- `POST /benchmark/runs/{run}/cancel`: `scenario:run`.
- `GET /benchmark/runs`, `/runs/{run}`, `/files/{run}/{artifact}`: `trace:read`.
- Read-only case endpoints under `/benchmark/runs/{run}/cases/{case}`: `/timeline`, `/timeline/{event}`, `/timeline/{event}/planning`, `/chain/{event}`, `/span?id=...`, `/incidents`, `/evidence/{ref}`.

Artifacts are allowlisted and constrained to the benchmark output directory. Viewer roles can inspect traces; developers/admins can run simulations. Authentication follows the existing app configuration.

## Local verification

The completed `verified-30-cases` run used all ten tasks with seeds 42, 43 and 44: **30/30 cases, 252/252 checks, 1,487 recorded events, 150 injected inputs**, and all nine core agents exercised. Its full bundle is `outputs/benchmarks/verified-30-cases/artifacts.zip` (approximately 4.2 MB). Earlier failed diagnostic runs are retained separately.

Validation after implementation: **641 pytest tests passed**, including twelve benchmark tests covering all tasks, the runnable template, reproducibility/pacing, malformed tasks, failure/cancellation persistence, isolation, permissions and saved-trace inspection. The production TypeScript/Vite build and Ruff checks passed. A river-evacuation case was also launched through the browser and its saved reasoning timeline inspected. These are local verification results under the limitations above.

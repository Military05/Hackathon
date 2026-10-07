# Working on Military05/Hackathon

Read README.md, docs/DEVELOPMENT_STAGES.md, docs/RUNTIME_QUICKSTART.md,
docs/RELEASE.md, docs/CONTRACTS.md, docs/PREBUILD_FIXES.md, docs/PRODUCT_V5.md
and your role guide first. Read docs/FACTORY_V6.md for current map/safety/scenarios.
Current v5 account/checkpoint requirements replace old
"no accounts/no shifts/demo header login" exceptions in historical planning files.
Report the branch and full source commit SHA. Never claim tests were run unless they were.

## Ownership

- Egor S.: src/core, src/storage, data/demo/site.json, requirements.txt, server startup.
- Egor M.: src/interface/web and interface tests.
- Grisha / separate controller: src/simulator, src/ml, datasets and model artifacts.
- Gadzhi / separate controller: src/agent, local model provider and agent jobs.
- Liya: QA scenarios, integration acceptance and target laptop measurements.

The current delivery covers Egor S. and Egor M. only. Agent, external simulator and ML
extensions are disabled by default and are implemented separately. Do not edit or
merge those modules without a direct task from their owner/coordinator.
Use the shared Event/Incident contracts; coordinate any schema/API change with its
consumers. Do not invent successful model or simulation responses.

The coordinator requested built-in factory traffic in src/core/demo_traffic.py.
It generates actual demo Events on shared routes and is enabled by default.
Keep this fallback separate from the external simulator, ML and agent owners.
Read docs/FACTORY_TRAFFIC_V4.md and docs/FACTORY_LAYOUT_V4.md for this update.
For current behavior also read docs/AUTH_SECURITY_V5.md, docs/GATE_SHIFT_V5.md
and docs/MAP_V5.md. Authentication defaults ON; bootstrap has no default password.
Preserve PBKDF2/session/CSRF/source-key checks. A session supplies the operator,
never a URL or caller-supplied demo header; Expected-User detects stale browser tabs.
Three accounts require separate browser profiles because ordinary tabs share cookies.
Dispatcher-3 owns checkpoint work; existing coordination ID and fallback routing remain.
It cannot claim unrelated sector incidents without addressed escalation/transfer rules.
Shift waits for confirmed authorized gate passages before transport; keep Event JSON unchanged.

Work in isolated branches/checkouts. Do not modify another person's dirty checkout.
Use precise allowed files, full SHA and commands for every handoff. Preserve existing
remote work when integrating; no force push. Merge only when the coordinator asks.

## Checks

python -m unittest discover -s tests/runtime -p 'test_*.py' -v
python -m unittest discover -s tests/qa -p 'test_*.py' -v
node --check src/interface/web/app.js
node --test tests/map-view.test.cjs tests/sensors-panel.test.cjs
node --test tests/interface/*.cjs
node --test tests/interface.test.js

Show missing prerequisites as unavailable or NOT RUN. CI success is not a target
laptop benchmark or proof that a real LLM is connected.
The basic path uses 6 sources at 1 Hz, bounded map interpolation up to 15 fps,
one worker and SQLite without GPU/heavy installations. CSV exports are bounded:
gate <=200, dispatcher history default1000/max2000; all-history scope requires admin.
Do not claim target hardware, trusted remote TLS or ML/AI end-to-end acceptance
without the actual corresponding report.

V6 extends Incident v2 with route_deviation and collision (optional other_asset_id).
VehicleSafety uses fresh stored positions, never client animation. Keep unknown
on stale data and require two fresh confirmations for recovery. site safety_routes,
typed restricted zones and policy_version must match demo routes and asset_policy.
Rules remain independent of ML. Reports use Russian headers/values, UTF-8 BOM and
semicolon; JSON contract field names stay unchanged. Checkbox "На смене" publishes
the existing readiness lease; removing it requires a replacement for away/reserve.
AI/remote handoff: docs/AI_INTEGRATION_V6.md and docs/REMOTE_CLIENT_V6.md.

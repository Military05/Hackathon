# Working on Military05/Hackathon

Read README.md, docs/DEVELOPMENT_STAGES.md, docs/RUNTIME_QUICKSTART.md,
docs/RELEASE.md, docs/CONTRACTS.md, docs/PREBUILD_FIXES.md and your role guide first.
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

Work in isolated branches/checkouts. Do not modify another person's dirty checkout.
Use precise allowed files, full SHA and commands for every handoff. Preserve existing
remote work when integrating; no force push. Merge only when the coordinator asks.

## Checks

python -m unittest discover -s tests/runtime -p 'test_*.py' -v
python -m unittest discover -s tests/qa -p 'test_*.py' -v
node --check src/interface/web/app.js
node --test tests/interface/*.cjs
node --test tests/interface.test.js

Show missing prerequisites as unavailable or NOT RUN. CI success is not a target
laptop benchmark or proof that a real LLM is connected.

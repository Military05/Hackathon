(function (root) {
    "use strict";
    const core = typeof module !== "undefined" && module.exports ? require("./dispatch-core.js") : root.DispatchCore;
    function list(value, key) {
        const result = Array.isArray(value) ? value : value?.[key];
        if (!Array.isArray(result)) throw core.error("invalid_response", `Ответ ${key} не является списком`, 502);
        return result;
    }
    function createApiProvider(operatorId, fetcher = root.fetch.bind(root)) {
        async function request(path, method = "GET", body) {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 5000);
            try {
                const response = await fetcher(`/api${path}`, {method, credentials: "same-origin", signal: controller.signal,
                    headers: {"X-Demo-Operator": operatorId, ...(root.ProductAuth?.headers?.() || {}), ...(body ? {"Content-Type": "application/json"} : {})},
                    ...(body ? {body: JSON.stringify(body)} : {})});
                let value;
                try { value = await response.json(); } catch { throw core.error("invalid_response", "Сервер вернул ответ без корректного JSON", response.status || 502); }
                if (!response.ok) throw core.error(value.code || value.error?.code || "api_error", value.message || value.error?.message || `Ошибка HTTP ${response.status}`, response.status);
                return value;
            } finally { clearTimeout(timer); }
        }
        return {
            mode: "api", request,
            async initial() {
                const [health, site, operators] = await Promise.all([request("/health"), request("/site"), request("/operator-profiles")]);
                if (health.contract_version !== 2) throw core.error("contract_mismatch", "Требуется сервер CONTRACTS v2", 502);
                return {site, profiles: list(operators, "operator_profiles"), health};
            },
            async dynamic(scope = "workstation") {
                const [assets, incidents, sensors, overview] = await Promise.all([
                    request("/assets"), request(`/incidents?scope=${scope === "all" ? "all" : "workstation"}`), request("/sensors"), request("/dispatch-summary")]);
                return {assets: list(assets, "assets"), incidents: list(incidents, "incidents"), sensors: list(sensors, "sensors"), overview};
            },
            mutate: (id, command) => request(`/incidents/${encodeURIComponent(id)}`, "PATCH", command),
            presence: (sessionId, availability) => request("/operator-presence", "POST", {session_id: sessionId, availability}),
            notifications: seq => request(`/dispatch-notifications?after_seq=${seq}&limit=100`),
            analyse: id => request(`/incidents/${encodeURIComponent(id)}/analysis`, "POST", {}),
            job: id => request(`/agent-jobs/${encodeURIComponent(id)}`)
        };
    }
    function createMockProvider() {
        const now = Date.now();
        const stamp = seconds => new Date(now + seconds * 1000).toISOString();
        const rectangle = (x, y, width, height) => ({x, y, width, height});
        const site = {demo: true, buildings: [
            {id: "W1", name: "Склад сырья", rectangle: rectangle(10, 10, 20, 15), responsible_sector_id: "logistics"},
            {id: "W2", name: "Склад готовой продукции", rectangle: rectangle(60, 10, 20, 15), responsible_sector_id: "logistics"},
            {id: "P1", name: "Цех 1", rectangle: rectangle(10, 50, 25, 20), responsible_sector_id: "production"},
            {id: "P2", name: "Цех 2", rectangle: rectangle(55, 50, 25, 20), responsible_sector_id: "production"},
            {id: "O1", name: "Офис", rectangle: rectangle(35, 75, 20, 12), responsible_sector_id: "coordination"},
            {id: "G1", name: "КПП", rectangle: rectangle(5, 80, 12, 10), responsible_sector_id: "coordination"}],
            zones: [{id: "Z1", rectangle: rectangle(35, 30, 15, 15), responsible_sector_id: "logistics"},
                {id: "Z2", rectangle: rectangle(75, 75, 15, 15), responsible_sector_id: "coordination"}],
            sectors: [{id: "logistics", map_bounds: rectangle(0, 0, 100, 48)}, {id: "production", map_bounds: rectangle(0, 48, 100, 26)}, {id: "coordination", map_bounds: rectangle(0, 0, 100, 100)}],
            roads: [{points: "20,27 20,40 90,40"}, {points: "43,40 43,72 65,72"}, {points: "11,78 25,72 43,72"}],
            dispatch_config: {position_stale_seconds: 5, presence_interval_seconds: 3}};
        let assets = [
            {asset_id: "V1", type: "vehicle", vehicle_type: "Грузовик", destination: "W1 — склад сырья", x: 40, y: 40, last_seen: stamp(0)},
            {asset_id: "V2", type: "vehicle", vehicle_type: "Погрузчик", destination: "P1 — цех 1", x: 43, y: 60, last_seen: stamp(-20)},
            {asset_id: "V3", type: "vehicle", vehicle_type: "Электротележка", destination: "W2 — склад продукции", x: 65, y: 35, last_seen: null}];
        const sensors = [
            {sensor_id: "POS-V1", type: "position", asset_id: "V1", responsible_sector_id: "logistics", status: "online", last_received_at: stamp(0)},
            {sensor_id: "POS-V2", type: "position", asset_id: "V2", responsible_sector_id: "production", status: "offline", last_received_at: stamp(-20)},
            {sensor_id: "POS-V3", type: "position", asset_id: "V3", responsible_sector_id: "logistics", status: "unknown", last_received_at: null},
            {sensor_id: "ACCESS-O1", type: "access", responsible_sector_id: "coordination", status: "online", last_received_at: stamp(0)}];
        function incident(id, type, sector, extra = {}) {
            return {incident_id: id, type, responsible_sector_id: sector, site_area_id: sector === "logistics" ? "warehouse-raw" : sector === "production" ? "workshop-1" : "office",
                severity: type === "forbidden_zone" || type === "unauthorized_access" ? "critical" : "warning",
                detected_at: stamp(-5), status: "open", condition_active: type !== "unauthorized_access", condition_state: type === "unauthorized_access" ? "restored" : "active",
                assigned_operator_id: null, dispatch_revision: 0, pending_transfer: null, escalation_level: 0, disposition: null,
                evidence_event_ids: [`fixture-${id}`], demo: true, ...extra};
        }
        let incidents = [
            incident("INC-001", "forbidden_zone", "logistics", {asset_id: "V1", zone_id: "Z1"}),
            incident("INC-002", "sensor_offline", "production", {asset_id: "V2", sensor_id: "POS-V2"}),
            incident("INC-003", "unauthorized_access", "coordination", {employee_id: "U1", building_id: "O1", access_kind: "passage_confirmed"})];
        const replay = new Map();
        let burst = 0;
        return {mode: "mock", async initial() { return {site: structuredClone(site), profiles: structuredClone(core.profiles), health: {contract_version: 2, agent: "unavailable"}}; },
            async dynamic() {
                if (incidents[0].condition_active && sensors[0].status === "online") {
                    assets[0].x = 42 + 2 * Math.sin(Date.now() / 3000);
                    assets[0].last_seen = new Date().toISOString();
                    sensors[0].last_received_at = assets[0].last_seen;
                }
                return structuredClone({assets, incidents, sensors, overview: core.summary(incidents)});
            },
            async mutate(id, command, operatorId) {
                const profile = core.profiles.find(item => item.operator_id === operatorId);
                if (!profile) throw core.error("unknown_profile", "Неизвестное рабочее место", 404);
                const key = `${id}:${operatorId}:${command.request_id}`;
                const body = JSON.stringify(Object.keys(command).sort().map(key => [key, command[key]]));
                if (replay.has(key)) {
                    const saved = replay.get(key);
                    if (saved.body !== body) throw core.error("request_conflict", "Повтор request_id с другим действием");
                    return structuredClone(saved.result);
                }
                const index = incidents.findIndex(item => item.incident_id === id);
                if (index < 0) throw core.error("not_found", "Нет такого происшествия", 404);
                const result = core.applyAction(incidents[index], command, profile);
                incidents[index] = result;
                replay.set(key, {body, result: structuredClone(result)});
                return structuredClone(result);
            },
            recover(id) {
                const item = incidents.find(row => row.incident_id === id);
                if (!item || !core.isWorkItem(item)) return;
                item.condition_active = false;
                item.condition_state = "restored";
                item.dispatch_revision += 1;
                if (id === "INC-001") {assets[0].x = 54; assets[0].last_seen = new Date().toISOString();}
                if (id === "INC-002") {sensors[1].status = "online"; sensors[1].last_received_at = new Date().toISOString(); assets[1].last_seen = sensors[1].last_received_at;}
            },
            losePosition() {sensors[0].status = "offline"; sensors[0].last_received_at = stamp(-20); assets[0].last_seen = stamp(-20); incidents[0].condition_state = "unknown";},
            addBurst() {
                burst += 1;
                incidents.push(incident(`BURST-${burst}-A`, "model_anomaly", "logistics", {asset_id: "V1", score: 0.82}),
                    incident(`BURST-${burst}-B`, "sensor_offline", "logistics", {sensor_id: "POS-V3", site_area_id: "warehouse-finished"}));
            }
        };
    }
    const api = {createApiProvider, createMockProvider, list};
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.DispatchProviders = api;
})(globalThis);

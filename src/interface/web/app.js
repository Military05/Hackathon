const mockSite = {
    demo: true,

    buildings: [
        {
            id: "W1",
            name: "Склад сырья",
            rectangle: {
                x: 10,
                y: 10,
                width: 20,
                height: 15
            }
        },
        {
            id: "W2",
            name: "Склад готовой продукции",
            rectangle: {
                x: 60,
                y: 10,
                width: 20,
                height: 15
            }
        },
        {
            id: "P1",
            name: "Цех 1",
            rectangle: {
                x: 10,
                y: 50,
                width: 25,
                height: 20
            }
        },
        {
            id: "P2",
            name: "Цех 2",
            rectangle: {
                x: 55,
                y: 50,
                width: 25,
                height: 20
            }
        },
        {
            id: "O1",
            name: "Офис",
            rectangle: {
                x: 35,
                y: 75,
                width: 20,
                height: 12
            }
        },
        {
            id: "G1",
            name: "КПП",
            rectangle: {
                x: 5,
                y: 80,
                width: 12,
                height: 10
            }
        }
    ],

    zones: [
        {
            id: "Z1",
            rectangle: {
                x: 35,
                y: 30,
                width: 15,
                height: 15
            }
        },
        {
            id: "Z2",
            rectangle: {
                x: 75,
                y: 75,
                width: 15,
                height: 15
            }
        }
    ]
};

const mockAssets = [
    {
        asset_id: "V1",
        type: "vehicle",
        x: 20,
        y: 35,
        last_seen: "2026-10-07T07:00:00Z"
    },
    {
        asset_id: "V2",
        type: "vehicle",
        x: 45,
        y: 60,
        last_seen: "2026-10-07T07:00:01Z"
    },
    {
        asset_id: "V3",
        type: "vehicle",
        x: 80,
        y: 40,
        last_seen: "2026-10-07T07:00:02Z"
    }
];

const mockIncidents = [
    {
        incident_id: "INC-001",
        type: "forbidden_zone",
        asset_id: "V1",
        severity: "critical",
        detected_at: "2026-10-07T07:01:00Z",
        status: "open",
        condition_active: true,
        evidence_event_ids: ["event-001"],
        demo: true
    },
    {
        incident_id: "INC-002",
        type: "sensor_offline",
        asset_id: "V2",
        severity: "warning",
        detected_at: "2026-10-07T07:02:00Z",
        status: "open",
        condition_active: true,
        evidence_event_ids: ["event-002"],
        demo: true
    }
];

const mockSensors = [
    {
        sensor_id: "POS-V1",
        type: "position",
        asset_id: "V1",
        status: "online",
        last_received_at: new Date().toISOString()
    },
    {
        sensor_id: "POS-V2",
        type: "position",
        asset_id: "V2",
        status: "offline",
        last_received_at: "2026-10-07T07:00:01Z"
    },
    {
        sensor_id: "POS-V3",
        type: "position",
        asset_id: "V3",
        status: "unknown",
        last_received_at: null
    }
];

const USE_MOCK = true;

async function getSite() {
    if (USE_MOCK) {
        return mockSite;
    }

    const response = await fetch("/api/site");

    if (!response.ok) {
        throw new Error("Не удалось получить данные предприятия");
    }

    return response.json();
}

async function getAssets() {
    if (USE_MOCK) {
        return mockAssets;
    }

    const response = await fetch("/api/assets");

    if (!response.ok) {
        throw new Error("Не удалось получить данные транспорта");
    }

    return response.json();
}

async function getIncidents() {
    if (USE_MOCK) {
        return mockIncidents;
    }

    const response = await fetch("/api/incidents");

    if (!response.ok) {
        throw new Error("Не удалось получить происшествия");
    }

    return response.json();
}

async function getSensors() {
    if (USE_MOCK) {
        return mockSensors;
    }

    const response = await fetch("/api/sensors");

    if (!response.ok) {
        throw new Error("Не удалось получить состояние датчиков");
    }

    return response.json();
}

async function updateIncidentStatus(incidentId, newStatus) {
    if (USE_MOCK) {
        const incident = mockIncidents.find(
            item => item.incident_id === incidentId
        );

        if (!incident) {
            throw new Error("Происшествие не найдено");
        }

        incident.status = newStatus;

        return incident;
    }

    const response = await fetch(`/api/incidents/${incidentId}`, {
        method: "PATCH",

        headers: {
            "Content-Type": "application/json"
        },

        body: JSON.stringify({
            status: newStatus
        })
    });

    if (!response.ok) {
        throw new Error("Не удалось изменить статус происшествия");
    }

    return response.json();
}

async function loadInitialData() {
    try {
        document.getElementById("system-status").textContent =
            "Загрузка данных...";

        const site = await getSite();

        renderSite(site);

        document.getElementById("system-status").textContent =
            "Система работает";
    } catch (error) {
        console.error(error);

        document.getElementById("system-status").textContent =
            "Ошибка получения данных";
    }
}

const incidentTypeLabels = {
    forbidden_zone: "Запрещённая зона",
    unauthorized_access: "Проход без допуска",
    sensor_offline: "Потеря связи с датчиком",
    model_anomaly: "Аномальное движение"
};

const incidentStatusLabels = {
    open: "Открыто",
    acknowledged: "Подтверждено оператором",
    closed: "Закрыто"
};

const incidentSeverityLabels = {
    info: "Информация",
    warning: "Предупреждение",
    critical: "Критично"
};

const SVG_NS = "http://www.w3.org/2000/svg";

function renderSite(site) {
    const map = document.getElementById("site-map");

    map.innerHTML = "";

    site.zones.forEach(zone => {
        const rect = document.createElementNS(SVG_NS, "rect");

        rect.setAttribute("x", zone.rectangle.x);
        rect.setAttribute("y", zone.rectangle.y);
        rect.setAttribute("width", zone.rectangle.width);
        rect.setAttribute("height", zone.rectangle.height);

        rect.setAttribute("fill", "#3f1d1d");
        rect.setAttribute("stroke", "#ef4444");
        rect.setAttribute("stroke-dasharray", "2 2");

        map.appendChild(rect);

        const label = document.createElementNS(SVG_NS, "text");

        label.setAttribute(
            "x",
            zone.rectangle.x + zone.rectangle.width / 2
        );

        label.setAttribute(
            "y",
            zone.rectangle.y + zone.rectangle.height / 2
        );

        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "3");
        label.setAttribute("fill", "#fca5a5");

        label.textContent = zone.id;

        map.appendChild(label);
    });

    site.buildings.forEach(building => {
        const rect = document.createElementNS(SVG_NS, "rect");

        rect.setAttribute("x", building.rectangle.x);
        rect.setAttribute("y", building.rectangle.y);
        rect.setAttribute("width", building.rectangle.width);
        rect.setAttribute("height", building.rectangle.height);

        rect.setAttribute("fill", "#1e3a5f");
        rect.setAttribute("stroke", "#60a5fa");

        map.appendChild(rect);

        const label = document.createElementNS(SVG_NS, "text");

        label.setAttribute(
            "x",
            building.rectangle.x + building.rectangle.width / 2
        );

        label.setAttribute(
            "y",
            building.rectangle.y + building.rectangle.height / 2
        );

        label.setAttribute("text-anchor", "middle");
        label.setAttribute("font-size", "3");
        label.setAttribute("fill", "#bfdbfe");

        label.textContent = building.id;

        map.appendChild(label);
    });
}

function renderAssets(assets) {
    const map = document.getElementById("site-map");

    let assetsLayer = document.getElementById("assets-layer");

    if (!assetsLayer) {
        assetsLayer = document.createElementNS(SVG_NS, "g");
        assetsLayer.setAttribute("id", "assets-layer");
        map.appendChild(assetsLayer);
    }

    assetsLayer.innerHTML = "";

    assets.forEach(asset => {
        const group = document.createElementNS(SVG_NS, "g");

        group.setAttribute("id", `asset-${asset.asset_id}`);

        const circle = document.createElementNS(SVG_NS, "circle");

        const stale = isAssetStale(asset);

        circle.setAttribute("cx", asset.x);
        circle.setAttribute("cy", asset.y);
        circle.setAttribute("r", 2);

        if (stale) {
            circle.setAttribute("fill", "#f59e0b");
            circle.setAttribute("stroke", "#92400e");
        } else {
            circle.setAttribute("fill", "#16a34a");
            circle.setAttribute("stroke", "#166534");
        }

        group.appendChild(circle);

        const label = document.createElementNS(SVG_NS, "text");

        label.setAttribute("x", asset.x + 3);
        label.setAttribute("y", asset.y + 1);
        label.setAttribute("font-size", "3");
        label.setAttribute("fill", "#f8fafc");

        label.textContent = asset.asset_id;

        group.appendChild(label);

        const timeLabel = document.createElementNS(SVG_NS, "text");

        timeLabel.setAttribute("x", asset.x + 3);
        timeLabel.setAttribute("y", asset.y + 4);
        timeLabel.setAttribute("font-size", "2");
        if (stale) {
            timeLabel.setAttribute("fill", "#fde68a");
        } else {
            timeLabel.setAttribute("fill", "#bfdbfe");
        }

        const time = new Date(asset.last_seen);

        timeLabel.textContent =
            `${stale ? "устарело" : "обновлено"} ${time.toLocaleTimeString()}`;

        group.appendChild(timeLabel);

        assetsLayer.appendChild(group);
    });
}

function renderIncidentDetails(incident) {
    const container = document.getElementById("incident-details");

    container.innerHTML = "";

    const heading = document.createElement("h3");
    heading.textContent = "Подробности происшествия";

    const id = document.createElement("div");
    id.textContent = `ID: ${incident.incident_id}`;

    const type = document.createElement("div");
    type.textContent = `Тип: ${incidentTypeLabels[incident.type] ?? incident.type}`;

    const asset = document.createElement("div");
    asset.textContent = `Объект: ${incident.asset_id ?? "—"}`;

    const status = document.createElement("div");
    status.textContent = `Статус: ${incidentStatusLabels[incident.status] ?? incident.status}`;

    const severity = document.createElement("div");
    severity.textContent = `Важность: ${incidentSeverityLabels[incident.severity] ?? incident.severity}`;

    const time = document.createElement("div");
    time.textContent = `Время: ${incident.detected_at}`;

    const condition = document.createElement("div");
    condition.textContent =
        `Условие активно: ${incident.condition_active ? "Да" : "Нет"}`;

    const evidenceTitle = document.createElement("h4");
    evidenceTitle.textContent = "Evidence";

    const evidenceList = document.createElement("ul");

    incident.evidence_event_ids.forEach(eventId => {
        const item = document.createElement("li");

        item.textContent = eventId;

        evidenceList.appendChild(item);
    });

    const buttonsContainer = document.createElement("div");
    buttonsContainer.className = "incident-actions";

    const acknowledgeButton = document.createElement("button");
    acknowledgeButton.textContent = "Увидел происшествие";

    acknowledgeButton.addEventListener("click", async () => {
        try {
            const updatedIncident = await updateIncidentStatus(
                incident.incident_id,
                "acknowledged"
            );

            renderIncidentDetails(updatedIncident);

            const incidents = await getIncidents();
            renderIncidents(incidents);

        } catch (error) {
            console.error(error);
            alert("Не удалось подтвердить происшествие");
        }
    });

    const closeButton = document.createElement("button");
    closeButton.textContent = "Закрыть";

    closeButton.addEventListener("click", async () => {
        try {
            const updatedIncident = await updateIncidentStatus(
                incident.incident_id,
                "closed"
            );

            renderIncidentDetails(updatedIncident);

            const incidents = await getIncidents();
            renderIncidents(incidents);

        } catch (error) {
            console.error(error);
            alert("Не удалось закрыть происшествие");
        }
    });

    if (incident.status === "acknowledged") {
        acknowledgeButton.disabled = true;
    }

    if (incident.status === "closed") {
        acknowledgeButton.disabled = true;
        closeButton.disabled = true;
    }

    buttonsContainer.appendChild(acknowledgeButton);
    buttonsContainer.appendChild(closeButton);

    container.appendChild(heading);
    container.appendChild(id);
    container.appendChild(type);
    container.appendChild(asset);
    container.appendChild(status);
    container.appendChild(severity);
    container.appendChild(time);
    container.appendChild(condition);
    container.appendChild(evidenceTitle);
    container.appendChild(evidenceList);
    container.appendChild(buttonsContainer);
}

function renderIncidents(incidents) {
    const container = document.getElementById("incidents-list");

    container.innerHTML = "";

    if (incidents.length === 0) {
        showEmpty("incidents-list", "Происшествий нет");
        return;
    }

    incidents.forEach(incident => {
        const card = document.createElement("div");

        card.className = `incident incident-${incident.severity}`;
        card.addEventListener("click", () => {
            renderIncidentDetails(incident);
        });

        const title = document.createElement("strong");
        title.textContent = incidentTypeLabels[incident.type] ?? incident.type;

        const asset = document.createElement("div");
        asset.textContent = `Объект: ${incident.asset_id ?? "—"}`;

        const status = document.createElement("div");
        status.textContent = `Статус: ${incidentStatusLabels[incident.status] ?? incident.status}`;

        const time = document.createElement("div");
        time.textContent = `Время: ${incident.detected_at}`;

        const severity = document.createElement("div");
        severity.textContent = `Важность: ${incidentSeverityLabels[incident.severity] ?? incident.severity}`;

        card.appendChild(title);
        card.appendChild(asset);
        card.appendChild(status);
        card.appendChild(time);
        card.appendChild(severity);

        container.appendChild(card);
    });
}

async function loadDynamicData() {
    try {
        const assets = await getAssets();
        const incidents = await getIncidents();
        const sensors = await getSensors();

        renderAssets(assets);
        renderIncidents(incidents);
        renderSensors(sensors);

        document.getElementById("system-status").textContent =
            "Система работает";
    } catch (error) {
        console.error(error);

        document.getElementById("system-status").textContent =
            "Ошибка получения данных";

        showError(
            "incidents-list",
            "Не удалось загрузить происшествия"
        );

        showError(
            "sensors-list",
            "Не удалось загрузить датчики"
        );
    }
}

function isAssetStale(asset) {
    const lastSeenTime = new Date(asset.last_seen).getTime();
    const now = Date.now();

    const ageMs = now - lastSeenTime;

    return ageMs > 5000;
}

function renderSensors(sensors) {
    const container = document.getElementById("sensors-list");

    container.innerHTML = "";

    if (sensors.length === 0) {
        showEmpty("sensors-list", "Датчиков нет");
        return;
    }

    sensors.forEach(sensor => {
        const card = document.createElement("div");

        card.className = `sensor sensor-${sensor.status}`;

        const title = document.createElement("strong");
        title.textContent = sensor.sensor_id;

        const type = document.createElement("div");
        type.textContent = `Тип: ${sensor.type}`;

        const asset = document.createElement("div");
        asset.textContent = `Объект: ${sensor.asset_id ?? "—"}`;

        const status = document.createElement("div");

        const statusLabels = {
            online: "Связь есть",
            offline: "Связь потеряна",
            unknown: "Состояние неизвестно"
        };

        status.textContent =
            `Состояние: ${statusLabels[sensor.status] ?? sensor.status}`;

        const lastSeen = document.createElement("div");

        if (sensor.last_received_at) {
            lastSeen.textContent =
                `Последний сигнал: ${new Date(sensor.last_received_at).toLocaleTimeString()}`;
        } else {
            lastSeen.textContent = "Последний сигнал: нет данных";
        }

        card.appendChild(title);
        card.appendChild(type);
        card.appendChild(asset);
        card.appendChild(status);
        card.appendChild(lastSeen);

        container.appendChild(card);
    });
}

function updateMockData() {
    if (!USE_MOCK) {
        return;
    }

    mockAssets[0].x += 1;

    if (mockAssets[0].x > 90) {
        mockAssets[0].x = 10;
    }

    const now = new Date().toISOString();

    mockAssets[0].last_seen = now;
    mockSensors[0].last_received_at = now;
}

function showLoading(elementId, message = "Загрузка...") {
    const element = document.getElementById(elementId);
    element.textContent = message;
}

function showError(elementId, message) {
    const element = document.getElementById(elementId);
    element.textContent = message;
}

function showEmpty(elementId, message) {
    const element = document.getElementById(elementId);
    element.textContent = message;
}

async function refreshLoop() {
    updateMockData();

    await loadDynamicData();

    setTimeout(refreshLoop, 1000);
}

async function startApp() {
    await loadInitialData();

    refreshLoop();
}

startApp();


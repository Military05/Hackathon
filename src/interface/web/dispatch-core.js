(function (root) {
    "use strict";

    const profiles = [
        {operator_id: "dispatcher-1", sector_id: "logistics", name: "Склады"},
        {operator_id: "dispatcher-2", sector_id: "production", name: "Производство"},
        {operator_id: "dispatcher-3", sector_id: "coordination", name: "КПП и координация"}
    ];
    const severityOrder = {critical: 0, warning: 1, info: 2};
    const responses = {
        forbidden_zone: {contact: "Водитель / ответственный за транспорт", steps: "Проверить объект и свежесть позиции. Связаться с водителем по действующей рации и передать инструкцию предприятия. Проверить подтверждённый выход."},
        unauthorized_access: {contact: "Охрана / ответственный за доступ", steps: "Уточнить: отказ, подтверждённый проход или открытие двери. Проверить допуск и связаться с охраной. Отказ карточки не доказывает проникновение."},
        sensor_offline: {contact: "Ответственный за датчик / техническая служба", steps: "Проверить последний сигнал и канал связи. Направить проверку источника. Отсутствие координат не означает устранение другого нарушения."},
        model_anomaly: {contact: "Ответственный за транспорт", steps: "Проверить исходные наблюдения и штатную операцию. Оценка модели — подозрение, а не вероятность аварии. Зафиксировать результат проверки."}
    };

    function error(code, message, status = 409) {
        return Object.assign(new Error(message), {code, status});
    }
    function isWorkItem(incident) {
        return incident.status !== "closed" && !(incident.type === "model_anomaly" && incident.disposition === "rejected_model_signal");
    }
    function isActive(incident) {
        return incident.condition_active === true && !(incident.type === "model_anomaly" && incident.disposition === "rejected_model_signal");
    }
    function isVisible(incident, profile) {
        return incident.responsible_sector_id === profile.sector_id ||
            incident.assigned_operator_id === profile.operator_id ||
            incident.pending_transfer?.to_operator_id === profile.operator_id ||
            (incident.escalated_to_operator_ids || []).includes(profile.operator_id);
    }
    function sortedIncidents(incidents) {
        return [...incidents].sort((a, b) =>
            (severityOrder[a.severity] ?? 3) - (severityOrder[b.severity] ?? 3) ||
            Number(Boolean(a.assigned_operator_id)) - Number(Boolean(b.assigned_operator_id)) ||
            Date.parse(a.detected_at) - Date.parse(b.detected_at) ||
            a.incident_id.localeCompare(b.incident_id));
    }
    function quality(asset, now = Date.now(), thresholdSeconds = 5) {
        const time = asset?.last_seen ? Date.parse(asset.last_seen) : NaN;
        if (!Number.isFinite(time)) return {state: "unknown", ageSeconds: null};
        const ageSeconds = Math.max(0, (now - time) / 1000);
        return {state: ageSeconds > thresholdSeconds ? "stale" : "fresh", ageSeconds};
    }
    function conditionLabel(incident, asset, now = Date.now()) {
        if (incident.type === "model_anomaly" && incident.disposition === "rejected_model_signal") return "Модельное подозрение отклонено; исходная оценка сохранена";
        if (incident.condition_state === "unknown" ||
            (incident.type === "forbidden_zone" && quality(asset, now).state !== "fresh")) {
            return `Текущее состояние неизвестно. Последнее наблюдение: ${incident.condition_active ? "нарушение было активно" : "условие не было активно"}`;
        }
        return incident.condition_active ? "Условие активно" : "Проблема устранена / разовое событие";
    }
    function canClaim(incident, profile) {
        return isWorkItem(incident) && !incident.assigned_operator_id &&
            (incident.responsible_sector_id === profile.sector_id || profile.operator_id === "dispatcher-3" ||
                (incident.escalated_to_operator_ids || []).includes(profile.operator_id));
    }
    function closeBlockedReason(incident, profile) {
        if (!isWorkItem(incident)) return "Случай уже обработан";
        if (incident.assigned_operator_id !== profile.operator_id) return "Сначала принять ответственность";
        if (incident.pending_transfer) return "Сначала завершить или отменить передачу";
        if (incident.condition_active || incident.condition_state === "unknown") return "Нельзя закрыть активное или неизвестное условие";
        return "";
    }
    function applyAction(incident, command, profile, now = new Date().toISOString()) {
        if (command.expected_revision !== incident.dispatch_revision) throw error("revision_conflict", "Карточка изменилась. Перечитайте её и повторите действие.");
        if (!command.request_id || !Number.isInteger(command.expected_revision)) throw error("invalid_command", "Нужны request_id и expected_revision", 422);
        const next = structuredClone(incident);
        const reason = typeof command.reason === "string" ? command.reason.trim() : "";
        if (command.action === "claim") {
            if (!canClaim(incident, profile)) throw error("operator_conflict", "Случай уже принят или не адресован этому рабочему месту.");
            next.assigned_operator_id = profile.operator_id;
            next.status = "acknowledged";
            next.acknowledged_at = now;
        } else if (command.action === "close") {
            const blocked = closeBlockedReason(incident, profile);
            if (blocked) throw error("close_blocked", blocked);
            if (!reason || reason.length > 500) throw error("reason_required", "Укажите результат обработки (1–500 символов)", 422);
            next.status = "closed";
            next.closed_at = now;
            next.close_reason = reason;
        } else if (command.action === "record_response") {
            if (!isWorkItem(incident) || incident.assigned_operator_id !== profile.operator_id) throw error("operator_conflict", "Запись действия доступна текущему ответственному.");
            if (!reason || reason.length > 500 || !["contacted", "inspection_requested", "checked"].includes(command.response_code)) throw error("response_required", "Выберите действие и опишите результат (1–500 символов)", 422);
            next.response_history = [...(incident.response_history || []), {response_code: command.response_code, reason, actor_operator_id: profile.operator_id, created_at: now}];
        } else if (command.action === "dismiss_model") {
            if (!isWorkItem(incident) || incident.type !== "model_anomaly" || incident.assigned_operator_id !== profile.operator_id) throw error("operator_conflict", "Отклонить можно только собственное модельное подозрение.");
            if (incident.pending_transfer) throw error("transfer_pending", "Сначала завершите или отмените передачу.");
            if (!reason || reason.length > 500) throw error("reason_required", "Укажите основание проверки (1–500 символов)", 422);
            next.disposition = "rejected_model_signal";
            next.disposition_reason = reason;
            next.reviewed_at = now;
        } else {
            throw error("action_required", "Неизвестная операция", 422);
        }
        next.dispatch_revision += 1;
        return next;
    }
    function summary(incidents, operatorProfiles = profiles) {
        return {as_of: new Date().toISOString(), sectors: operatorProfiles.map(profile => {
            const items = incidents.filter(item => item.responsible_sector_id === profile.sector_id);
            return {sector_id: profile.sector_id, active_count: items.filter(isActive).length,
                unclaimed_count: items.filter(item => isWorkItem(item) && !item.assigned_operator_id).length,
                escalated_count: items.filter(item => isWorkItem(item) && item.escalation_level > 0).length};
        }), unknown_count: incidents.filter(item => item.site_area_id === "unknown" && isWorkItem(item)).length};
    }
    const api = {profiles, responses, error, isWorkItem, isActive, isVisible, sortedIncidents, quality, conditionLabel, canClaim, closeBlockedReason, applyAction, summary};
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.DispatchCore = api;
})(globalThis);

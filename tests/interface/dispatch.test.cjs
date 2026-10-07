const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../src/interface/web/dispatch-core.js");
const {createMockProvider, createApiProvider} = require("../../src/interface/web/provider.js");
const claim = revision => ({action: "claim", expected_revision: revision, request_id: "claim-once"});
const close = revision => ({action: "close", expected_revision: revision, request_id: "close-once", reason: "Проверка выполнена"});

test("Три одновременных случая адресованы разным рабочим местам", async () => {
    const data = await createMockProvider().dynamic();
    assert.equal(data.incidents.length, 3);
    for (const p of core.profiles) assert.equal(data.incidents.filter(i => core.isVisible(i, p)).length, 1);
    assert.deepEqual(data.overview.sectors.map(s => s.unclaimed_count), [1,1,1]);
});
test("Перегрузка одного сектора не сокращает остальные происшествия", async () => {
    const p = createMockProvider(); p.addBurst(); const d = await p.dynamic();
    assert.equal(d.incidents.filter(i => core.isVisible(i, core.profiles[0])).length, 3);
    assert.equal(d.overview.sectors[0].unclaimed_count, 3);
});
test("Координатор может принять чужой случай; прежняя revision уже недействительна", async () => {
    const p = createMockProvider(); const result = await p.mutate("INC-001", claim(0), "dispatcher-3");
    assert.equal(result.assigned_operator_id, "dispatcher-3");
    await assert.rejects(p.mutate("INC-001", claim(0), "dispatcher-1"), {code: "revision_conflict"});
});
test("Точный повтор команды не создаёт второе действие", async () => {
    const p = createMockProvider(); const first = await p.mutate("INC-001", claim(0), "dispatcher-1");
    assert.deepEqual(await p.mutate("INC-001", claim(0), "dispatcher-1"), first);
    await assert.rejects(p.mutate("INC-001", {...claim(0), action: "close", reason: "другое"}, "dispatcher-1"), {code: "request_conflict"});
});
test("Активная зона не закрывается даже назначенным оператором", async () => {
    const p = createMockProvider(); await p.mutate("INC-001", claim(0), "dispatcher-1");
    await assert.rejects(p.mutate("INC-001", close(1), "dispatcher-1"), {code: "close_blocked"});
});
test("Восстановленный случай закрывает только владелец и с причиной", async () => {
    const p = createMockProvider(); await p.mutate("INC-001", claim(0), "dispatcher-1"); p.recover("INC-001");
    await assert.rejects(p.mutate("INC-001", close(2), "dispatcher-2"), {code: "close_blocked"});
    const result = await p.mutate("INC-001", close(2), "dispatcher-1"); assert.equal(result.status, "closed");
    assert.equal(core.summary((await p.dynamic()).incidents).sectors[0].active_count, 0);
});
test("Потеря позиции не восстанавливает последнее активное нарушение", async () => {
    const p = createMockProvider(); p.losePosition(); const d = await p.dynamic();
    assert.equal(d.incidents[0].condition_active, true);
    assert.match(core.conditionLabel(d.incidents[0], d.assets[0]), /неизвестно/);
    assert.equal(core.quality(d.assets[0]).state, "stale");
});
test("Отсутствующая или неверная дата не становится fresh", () => {
    for (const last_seen of [null, "", "bad"]) assert.equal(core.quality({last_seen}).state, "unknown");
});
test("Модельное подозрение отклоняется с сохранённой оценкой; зону так не скрыть", async () => {
    const p = createMockProvider(); p.addBurst(); const id = "BURST-1-A";
    await p.mutate(id, claim(0), "dispatcher-1");
    const r = await p.mutate(id, {action: "dismiss_model", expected_revision: 1, request_id: "dismiss", reason: "Штатная погрузка"}, "dispatcher-1");
    assert.equal(r.condition_active, true); assert.equal(r.score, 0.82); assert.equal(core.isWorkItem(r), false);
    await p.mutate("INC-001", claim(0), "dispatcher-1");
    await assert.rejects(p.mutate("INC-001", {action: "dismiss_model", expected_revision: 1, request_id: "wrong", reason: "скрыть"}, "dispatcher-1"), {code: "operator_conflict"});
});
test("Запись связи фиксирует действие и не устраняет нарушение", async () => {
    const p = createMockProvider(); await p.mutate("INC-001", claim(0), "dispatcher-1");
    const r = await p.mutate("INC-001", {action: "record_response", expected_revision: 1, request_id: "response", response_code: "contacted", reason: "Водитель ответил"}, "dispatcher-1");
    assert.equal(r.condition_active, true); assert.equal(r.response_history[0].reason, "Водитель ответил");
});
test("Сортировка ставит непринятое critical выше принятого и warning", () => {
    const items = [{incident_id:"c",severity:"warning",assigned_operator_id:null,detected_at:"2026-10-07T00:00:00Z"},
        {incident_id:"a",severity:"critical",assigned_operator_id:"dispatcher-1",detected_at:"2026-10-07T00:00:00Z"},
        {incident_id:"b",severity:"critical",assigned_operator_id:null,detected_at:"2026-10-07T00:00:01Z"}];
    assert.deepEqual(core.sortedIncidents(items).map(i=>i.incident_id), ["b","a","c"]);
});
test("Адресованная передача видна вне своего сектора", () => {
    assert.equal(core.isVisible({responsible_sector_id:"production",pending_transfer:{to_operator_id:"dispatcher-1"}}, core.profiles[0]), true);
});
test("Отклонение модели не обходит незавершённую передачу", () => {
    const incident = {incident_id:"ML",type:"model_anomaly",status:"acknowledged",assigned_operator_id:"dispatcher-1",dispatch_revision:1,pending_transfer:{to_operator_id:"dispatcher-2"}};
    assert.throws(() => core.applyAction(incident,{action:"dismiss_model",expected_revision:1,request_id:"x",reason:"Проверено"},core.profiles[0]),{code:"transfer_pending"});
});
test("API отправляет v2 action/revision/request_id и заголовок оператора", async () => {
    let actual; const p = createApiProvider("dispatcher-2", async (url, options) => {actual={url,options}; return {ok:true,status:200,json:async()=>({dispatch_revision:1})};});
    await p.mutate("case/1", claim(0));
    assert.equal(actual.url,"/api/incidents/case%2F1");assert.equal(actual.options.headers["X-Demo-Operator"],"dispatcher-2");
    assert.deepEqual(JSON.parse(actual.options.body),claim(0));assert.equal(actual.options.method,"PATCH");
});
test("Ошибка API не подменяется успешными mock-данными", async () => {
    const p = createApiProvider("dispatcher-1", async () => ({ok:false,status:409,json:async()=>({code:"revision_conflict",message:"Новое состояние"})}));
    await assert.rejects(p.mutate("INC-001",claim(0)),{code:"revision_conflict",status:409});
});
test("Повреждённый ответ и неправильная версия сервера отклоняются", async () => {
    const invalid = createApiProvider("dispatcher-1",async()=>({ok:true,status:200,json:async()=>{throw new SyntaxError();}}));
    await assert.rejects(invalid.initial(),{code:"invalid_response"});
    const v1 = createApiProvider("dispatcher-1",async url=>({ok:true,status:200,json:async()=>url.endsWith("/health")?{contract_version:1}:url.endsWith("/operator-profiles")?core.profiles:{}}));
    await assert.rejects(v1.initial(),{code:"contract_mismatch"});
});

import test from "node:test";
import assert from "node:assert/strict";
import { createDemoAdapter } from "../assets/demo-adapter.js";
import { createRestAdapter } from "../assets/rest-adapter.js";
import { config } from "../assets/config.js";

function memoryStorage() {
  const map = new Map();
  return { getItem: (key) => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: (key) => map.delete(key) };
}

test("demo data survives adapter recreation and supports CRUD and reset", async () => {
  const storage = memoryStorage();
  const first = createDemoAdapter({ storage });
  const initial = await first.list("records");
  const created = await first.create("records", { name: "Test", status: "Новый" });
  assert.equal((await createDemoAdapter({ storage }).list("records")).length, initial.length + 1);
  const updated = await first.update("records", created.id, { name: "Updated" });
  assert.equal(updated.name, "Updated");
  assert.equal(updated.id, created.id);
  await first.remove("records", created.id);
  assert.equal((await first.list("records")).length, initial.length);
  await first.reset();
  assert.deepEqual(await first.list("records"), initial);
  const extended = createDemoAdapter({ storage, resourceIds: ["records", "new_section"] });
  assert.deepEqual(await extended.list("new_section"), []);
  await assert.rejects(() => first.remove("records", "missing"), /не найдена/);
  await assert.rejects(() => first.list("unknown"), /Неизвестный раздел/);
});

test("REST adapter uses configured paths, credentials, and encoded IDs", async () => {
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url, ...init });
    return { ok: true, status: init.method === "DELETE" ? 204 : 200, headers: { get: () => "application/json" }, json: async () => init.method === "GET" ? { items: [{ id: 1 }] } : { id: 1 } };
  };
  const adapter = createRestAdapter({ baseUrl: "/api/", resources: { entries: "/entries" }, headers: () => ({ "X-Test": "value" }), fetchImpl });
  assert.deepEqual(await adapter.list("entries"), [{ id: 1 }]);
  await adapter.create("entries", { name: "A" });
  await adapter.update("entries", "a/b", { name: "B" });
  await adapter.remove("entries", "a/b");
  assert.deepEqual(requests.map(({ url, method }) => [url, method]), [["/api/entries", "GET"], ["/api/entries", "POST"], ["/api/entries/a%2Fb", "PUT"], ["/api/entries/a%2Fb", "DELETE"]]);
  assert.equal(requests[0].credentials, "same-origin");
  assert.equal(requests[1].headers["X-Test"], "value");
  assert.equal(requests[1].body, '{"name":"A"}');
  await assert.rejects(() => adapter.list("missing"), /Нет API-маршрута/);
});

test("section config has unique IDs and usable table and form fields", () => {
  assert.equal(new Set(config.sections.map((section) => section.id)).size, config.sections.length);
  for (const section of config.sections) {
    assert.match(section.id, /^[a-z][a-z0-9_-]*$/);
    assert.ok(section.fields.some((field) => field.table));
    assert.equal(new Set(section.fields.map((field) => field.key)).size, section.fields.length);
  }
});

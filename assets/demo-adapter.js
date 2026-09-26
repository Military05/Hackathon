export const sampleData = {
  records: [
    { id: "r1", name: "Первая запись", category: "Пример", status: "В работе", description: "Демонстрационные данные" },
    { id: "r2", name: "Вторая запись", category: "Пример", status: "Готово", description: "Записи можно редактировать" },
    { id: "r3", name: "Новая идея", category: "Идеи", status: "Новый", description: "Удалите и добавьте свою" },
  ],
  categories: [
    { id: "c1", name: "Пример", description: "Общая категория" },
    { id: "c2", name: "Идеи", description: "Новые предложения" },
  ],
  team: [
    { id: "t1", name: "Участник команды", role: "Администратор", email: "demo@example.com" },
  ],
};

const copy = (value) => JSON.parse(JSON.stringify(value));

// storage can be injected for tests or replaced by an adapter for any backend.
export function createDemoAdapter({ storage = globalThis.localStorage, key = "hackathon-admin-demo-v1", initial = sampleData, resourceIds = Object.keys(initial) } = {}) {
  function read() {
    const saved = storage.getItem(key);
    if (!saved) return copy(initial);
    try { return JSON.parse(saved); } catch { return copy(initial); }
  }
  function write(data) { storage.setItem(key, JSON.stringify(data)); }
  function listResource(data, section) {
    if (!resourceIds.includes(section)) throw new Error(`Неизвестный раздел: ${section}`);
    if (!Object.hasOwn(data, section)) data[section] = [];
    if (!Array.isArray(data[section])) throw new Error(`Некорректные данные раздела: ${section}`);
    return data[section];
  }
  return {
    async list(section) { return copy(listResource(read(), section)); },
    async create(section, value) {
      const data = read();
      const item = { ...copy(value), id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}` };
      listResource(data, section).unshift(item);
      write(data);
      return copy(item);
    },
    async update(section, id, value) {
      const data = read();
      const items = listResource(data, section);
      const index = items.findIndex((item) => String(item.id) === String(id));
      if (index < 0) throw new Error("Запись не найдена");
      items[index] = { ...items[index], ...copy(value), id: items[index].id };
      write(data);
      return copy(items[index]);
    },
    async remove(section, id) {
      const data = read();
      const items = listResource(data, section);
      const index = items.findIndex((item) => String(item.id) === String(id));
      if (index < 0) throw new Error("Запись не найдена");
      items.splice(index, 1);
      write(data);
    },
    async reset() { storage.removeItem(key); },
  };
}

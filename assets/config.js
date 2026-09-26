// Change only this file to rename the panel and describe your project's sections.
// Each section's id is the resource name passed to the adapter.
export const config = {
  title: "Hackathon",
  subtitle: "Панель управления",
  // "demo" stores sample data in this browser; "api" uses the REST adapter below.
  mode: "demo",
  api: {
    baseUrl: "/api",
    resources: { records: "/records", categories: "/categories", team: "/team" },
    // For an existing session-based API, cookies are sent to the same origin.
    // For token-based APIs, return headers from your own authentication flow here.
    headers: () => ({}),
  },
  sections: [
    {
      id: "records", label: "Записи", singular: "запись", icon: "▤",
      description: "Управляйте основными объектами вашего проекта.",
      fields: [
        { key: "name", label: "Название", type: "text", required: true, table: true },
        { key: "category", label: "Категория", type: "text", table: true },
        { key: "status", label: "Статус", type: "select", options: ["Новый", "В работе", "Готово"], table: true, badge: true },
        { key: "description", label: "Описание", type: "textarea" },
      ],
    },
    {
      id: "categories", label: "Категории", singular: "категорию", icon: "◫",
      description: "Пример второго независимого раздела.",
      fields: [
        { key: "name", label: "Название", type: "text", required: true, table: true },
        { key: "description", label: "Описание", type: "text", table: true },
      ],
    },
    {
      id: "team", label: "Команда", singular: "участника", icon: "♧",
      description: "Пример раздела для участников проекта.",
      fields: [
        { key: "name", label: "Имя", type: "text", required: true, table: true },
        { key: "role", label: "Роль", type: "text", table: true },
        { key: "email", label: "Почта", type: "email", table: true },
      ],
    },
  ],
};

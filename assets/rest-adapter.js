// Expected endpoints: GET/POST /resource and PUT/DELETE /resource/{id}.
// Supply a different adapter implementing list/create/update/remove for other APIs.
export function createRestAdapter({ baseUrl = "/api", resources = {}, headers = () => ({}), fetchImpl = globalThis.fetch, credentials = "same-origin" } = {}) {
  const root = baseUrl.replace(/\/$/, "");
  function url(section, id) {
    const resource = resources[section];
    if (!resource || !resource.startsWith("/") || resource.startsWith("//")) throw new Error(`Нет API-маршрута для ${section}`);
    return `${root}${resource}${id === undefined ? "" : `/${encodeURIComponent(id)}`}`;
  }
  async function request(section, method, id, body) {
    const response = await fetchImpl(url(section, id), {
      method, credentials,
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...headers() },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`API: ${response.status} ${response.statusText}`.trim());
    if (response.status === 204) return undefined;
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : undefined;
  }
  return {
    async list(section) {
      const data = await request(section, "GET");
      const items = Array.isArray(data) ? data : data?.items;
      if (!Array.isArray(items)) throw new Error(`API ${section}: ожидается массив или { items: [...] }`);
      return items;
    },
    create: (section, value) => request(section, "POST", undefined, value),
    update: (section, id, value) => request(section, "PUT", id, value),
    remove: (section, id) => request(section, "DELETE", id),
  };
}

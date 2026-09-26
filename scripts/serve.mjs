import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, relative } from "node:path";

const root = resolve(import.meta.dirname, "..");
const port = Number(process.env.PORT || 8088);
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml" };

createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const path = resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
    const subpath = relative(root, path);
    if (subpath.startsWith("..") || subpath.includes("\\") || !["index.html", "assets"].some((entry) => subpath === entry || subpath.startsWith(`${entry}/`))) {
      response.writeHead(404); response.end("Not found"); return;
    }
    const content = await readFile(path);
    response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream", "Cache-Control": "no-store" });
    response.end(content);
  } catch {
    response.writeHead(404); response.end("Not found");
  }
}).listen(port, "127.0.0.1", () => console.log(`Admin template: http://127.0.0.1:${port}`));

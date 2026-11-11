const http = require("http");
const UP = { host: "ai-stack-new-api-1", port: 3000 };
function norm(body) {
  try {
    const j = JSON.parse(body);
    if (Array.isArray(j.messages)) {
      for (const m of j.messages) {
        if (Array.isArray(m.content)) {
          m.content = m.content.map(p => (p && p.type === "text" && typeof p.text === "string") ? p.text : (typeof p === "string" ? p : "")).join("");
        }
        if (m.content === null || m.content === undefined) m.content = "";
      }
    }
    return JSON.stringify(j);
  } catch (e) { return body; }
}
http.createServer((req, res) => {
  const chunks = [];
  req.on("data", c => chunks.push(c));
  req.on("end", () => {
    let body = Buffer.concat(chunks);
    const ct = req.headers["content-type"] || "";
    if (ct.includes("application/json") && body.length) body = Buffer.from(norm(body.toString("utf8")));
    const headers = { ...req.headers, host: UP.host + ":" + UP.port };
    delete headers["transfer-encoding"];
    headers["content-length"] = body.length;
    const up = http.request({ host: UP.host, port: UP.port, path: req.url, method: req.method, headers }, u => {
      res.writeHead(u.statusCode, u.headers);
      u.pipe(res);
    });
    up.on("error", () => { if (!res.headersSent) res.writeHead(502); res.end("shim upstream error"); });
    up.end(body);
  });
}).listen(3013, "0.0.0.0", () => console.log("foreman-shim listening on 3013"));

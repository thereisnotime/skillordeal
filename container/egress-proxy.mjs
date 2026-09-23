// Allowlisting CONNECT proxy for bout egress. Bouts sit on an internal-only podman network;
// this proxy is the only way out and it only tunnels to ALLOW hosts on port 443.
// Every decision is logged as one JSON line, which the engine folds into the bout record.
import http from "node:http";
import net from "node:net";

const allow = new Set((process.env.ALLOW || "").split(",").map((h) => h.trim()).filter(Boolean));
const port = Number(process.env.PORT || 3128);
const log = (o) => console.log(JSON.stringify({ t: Date.now(), ...o }));

const server = http.createServer((req, res) => {
  // Plain-HTTP proxying is never allowed; everything legitimate is TLS via CONNECT.
  log({ kind: "http", host: req.headers.host || "", allowed: false });
  res.writeHead(403).end();
});

server.on("connect", (req, sock, head) => {
  const idx = req.url.lastIndexOf(":");
  const host = req.url.slice(0, idx).toLowerCase();
  const p = req.url.slice(idx + 1);
  const ok = allow.has(host) && p === "443";
  log({ kind: "connect", host, port: p, allowed: ok });
  if (!ok) {
    sock.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    return;
  }
  const up = net.connect(443, host, () => {
    sock.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    if (head.length) up.write(head);
    up.pipe(sock);
    sock.pipe(up);
  });
  up.on("error", () => sock.destroy());
  sock.on("error", () => up.destroy());
});

server.listen(port, () => log({ kind: "ready", port, allow: [...allow] }));

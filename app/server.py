"""HTTP 服务（标准库实现，零第三方依赖）。

路由：
  GET  /            页面
  GET  /healthz     健康检查
  POST /api/audit   审计：校验 -> 求解；失败返回 422 与可定位错误，
                    成功返回完整结果。
端口由环境变量 PORT 决定（默认 8000）。
"""

from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .cpp import AuditError, solve
from .validate import validate

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "CPPAudit/1.0"

    def log_message(self, fmt, *args):  # 静默默认访问日志，保留错误
        pass

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if path in ("/", "/index.html"):
            page = STATIC_DIR / "index.html"
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(404, {"code": "not_found", "message": "页面不存在"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/audit":
            self._send_json(404, {"code": "not_found", "message": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": {"code": "bad_json", "message": "请求不是合法 JSON"}})
            return
        try:
            nodes, edges, depot = validate(raw)
            result = solve(edges, nodes, depot)
        except AuditError as exc:
            self._send_json(422, {"error": exc.to_payload()})
            return
        except Exception:  # pragma: no cover - 防御性 500
            traceback.print_exc()
            self._send_json(500, {"error": {"code": "internal", "message": "服务器内部错误"}})
            return
        self._send_json(200, {"result": result})


def main():
    port = int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"CPP audit service listening on 0.0.0.0:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

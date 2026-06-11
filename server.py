"""
server.py — HTTP API for LARRY G-FORCE (stdlib only, no extra deps).

Exposes the orchestrator and subagents over JSON HTTP so the agent can be
driven by other tools, the Telegram bot, a web UI, or curl.

Run:  python main.py serve            (or: python server.py [host] [port])
Env:  LARRY_API_HOST (default 0.0.0.0), LARRY_API_PORT (default 7333),
      LARRY_API_TOKEN (bearer token; auto-generated + printed if unset)

Auth: every endpoint except GET /health requires
        Authorization: Bearer <token>
      Token resolves env LARRY_API_TOKEN > config.json api.token > generated.
        curl -H "Authorization: Bearer $LARRY_API_TOKEN" http://localhost:7333/agents

Endpoints:
  GET  /health                      -> agent + ollama status, loaded models
  GET  /agents                      -> available subagents
  GET  /tasks?status=&limit=        -> recent tasks from tasks.db
  GET  /memory?q=...&n=5            -> semantic recall from vector memory
  POST /run    {agent?, task, model?, context?}   -> one subagent run
  POST /run/stream {agent?, task, model?, context?} -> SSE token/tool stream
  POST /auto   {goal, model?}                      -> plan + multi-step execute
  POST /remember {text, metadata?}                 -> store a memory

Streaming uses Server-Sent Events: each line is `data: {json-event}\n\n`,
events match subagents/base.py (routing/token/tool/tool_result/final).
"""

import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import CONFIG, OLLAMA_HOST, ensure_dirs, installed_models, resolve_model  # noqa: E402

_orchestrator = None
_memory = None

# Bearer token (set in serve()). Empty string means auth disabled.
API_TOKEN = ""
# Routes reachable without a token (liveness probe only — no secrets).
OPEN_PATHS = {"/health"}


def resolve_token() -> str:
    """env LARRY_API_TOKEN > config.json api.token > freshly generated."""
    return (os.environ.get("LARRY_API_TOKEN")
            or CONFIG.get("api", {}).get("token")
            or secrets.token_urlsafe(24))


def orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from orchestrator import get_orchestrator
        _orchestrator = get_orchestrator()
    return _orchestrator


def memory():
    global _memory
    if _memory is None:
        from utils.memory_manager import MemoryManager
        _memory = MemoryManager()
    return _memory


class Handler(BaseHTTPRequestHandler):
    server_version = "LarryGForce/3.0"

    # ---- helpers ----
    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse_event(self, event: dict):
        self.wfile.write(f"data: {json.dumps(event, default=str)}\n\n".encode())
        self.wfile.flush()

    def log_message(self, *args):
        pass  # quiet; orchestrator logs its own decisions

    def _authorized(self, path: str) -> bool:
        """True if the request may proceed. Sends 401 itself when it may not."""
        if not API_TOKEN or path in OPEN_PATHS:
            return True
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        token = header[len(prefix):] if header.startswith(prefix) else ""
        if token and hmac.compare_digest(token, API_TOKEN):
            return True
        self._send(401, {"error": "unauthorized: send 'Authorization: Bearer <token>'"})
        return False

    # ---- routes ----
    def do_GET(self):
        url = urlparse(self.path)
        if not self._authorized(url.path):
            return
        q = parse_qs(url.query)
        try:
            if url.path == "/health":
                models = installed_models()
                self._send(200, {
                    "agent": CONFIG.get("agent_name"),
                    "version": CONFIG.get("version"),
                    "ollama_host": OLLAMA_HOST,
                    "ollama_online": bool(models),
                    "models_installed": len(models),
                    "roles": {r: resolve_model(r) for r in
                              ("main", "fast", "uncensored", "embedding")},
                })
            elif url.path == "/agents":
                from subagents import SUBAGENTS
                self._send(200, {"agents": sorted(SUBAGENTS)})
            elif url.path == "/tasks":
                from utils.task_manager import TaskManager
                tasks = TaskManager().list(
                    status=(q.get("status", [None])[0]),
                    limit=int(q.get("limit", ["20"])[0]))
                self._send(200, {"tasks": tasks})
            elif url.path == "/memory":
                query = q.get("q", [""])[0]
                if not query:
                    self._send(400, {"error": "missing ?q="})
                    return
                n = int(q.get("n", ["5"])[0])
                self._send(200, {"results": memory().recall(query, n=n)})
            else:
                self._send(404, {"error": f"no route {url.path}"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        if not self._authorized(url.path):
            return
        data = self._body()
        try:
            if url.path == "/run":
                task = data.get("task")
                if not task:
                    self._send(400, {"error": "missing 'task'"})
                    return
                out = orchestrator().run(
                    task, agent=data.get("agent"),
                    context=data.get("context", ""),
                    force_model=data.get("model"))
                self._send(200, out)
            elif url.path == "/run/stream":
                task = data.get("task")
                if not task:
                    self._send(400, {"error": "missing 'task'"})
                    return
                self._sse_open()
                try:
                    for ev in orchestrator().run_stream(
                            task, agent=data.get("agent"),
                            context=data.get("context", ""),
                            force_model=data.get("model")):
                        self._sse_event(ev)
                except BrokenPipeError:
                    return  # client disconnected
                except Exception as e:
                    self._sse_event({"type": "error", "error": str(e)})
            elif url.path == "/auto":
                goal = data.get("goal")
                if not goal:
                    self._send(400, {"error": "missing 'goal'"})
                    return
                out = orchestrator().run_goal(goal, force_model=data.get("model"))
                self._send(200, out)
            elif url.path == "/remember":
                text = data.get("text")
                if not text:
                    self._send(400, {"error": "missing 'text'"})
                    return
                doc_id = memory().remember(text, data.get("metadata"))
                self._send(200, {"id": doc_id})
            else:
                self._send(404, {"error": f"no route {url.path}"})
        except Exception as e:
            self._send(500, {"error": str(e)})


def serve(host: str = None, port: int = None):
    global API_TOKEN
    ensure_dirs()
    api_cfg = CONFIG.get("api", {})
    host = host or os.environ.get("LARRY_API_HOST") or api_cfg.get("host", "0.0.0.0")
    port = int(port or os.environ.get("LARRY_API_PORT")
               or api_cfg.get("port", 7333))

    from_env = bool(os.environ.get("LARRY_API_TOKEN"))
    from_cfg = bool(CONFIG.get("api", {}).get("token"))
    API_TOKEN = resolve_token()
    source = "env LARRY_API_TOKEN" if from_env else (
        "config.json api.token" if from_cfg else "GENERATED (set LARRY_API_TOKEN to pin)")

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"LARRY G-FORCE API on http://{host}:{port}  (ollama: {OLLAMA_HOST})")
    print(f"  auth: Bearer token [{source}]")
    print(f"  token: {API_TOKEN}")
    print("  GET  /health(open) /agents /tasks /memory?q=")
    print("  POST /run /run/stream /auto /remember")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    h = sys.argv[1] if len(sys.argv) > 1 else None
    p = sys.argv[2] if len(sys.argv) > 2 else None
    serve(h, p)

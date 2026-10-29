"""`boundary-latch serve`: the browser demo on loopback. It needs boundary-latch[demo].

fastapi and uvicorn are imported inside the functions, so the core package never loads them.
Every request must name a loopback Host. Every POST must also come from the page's own
origin, carry the per start token from the page, and be JSON. There are no cookies.
"""

import html
import secrets
import threading
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict

from .. import live as adapter
from ..errors import ConfigError
from ..live import LiveConfig
from .scenarios import BUILDERS, run_one, to_data
from .session import DemoSession
from .terminal import DEFAULT_DB

LOOPBACK = ("127.0.0.1", "::1")
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
STATIC = {
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
TOKEN_SLOT = 'content="LATCH_TOKEN"'


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposeBody(Body):
    profile: str
    tool: str
    arguments: dict[str, Any]
    operation_id: str | None = None


class OperationBody(Body):
    profile: str
    operation_id: str


class TurnBody(Body):
    profile: str
    text: str


class EmptyBody(Body):
    pass


class LockedSession:
    """The parts of the session run_turn uses, one call at a time."""

    def __init__(self, session: DemoSession, lock: threading.Lock):
        self.session = session
        self.latch = session.latch
        self.lock = lock

    def propose(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            return self.session.propose(*args, **kwargs)

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self.lock:
            return self.session.run(*args, **kwargs)


def static_text(name: str) -> str:
    return resources.files("boundary_latch.demo").joinpath("static", name).read_text()


def create_app(
    session: DemoSession,
    token: str,
    port: int = 8765,
    live: LiveConfig | None = None,
    chat_client: Any = None,
    scenario_db: str = ":memory:",
):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    origins = {f"http://{host}" for host in hosts}
    page = static_text("index.html").replace(TOKEN_SLOT, f'content="{html.escape(token)}"')
    if live is not None and chat_client is None:
        chat_client = adapter.ChatClient(live)
    # FastAPI runs plain routes on a thread pool. The session is used one call at a time.
    lock = threading.Lock()
    locked = LockedSession(session, lock)

    app = FastAPI(title="Latch demo", docs_url=None, redoc_url=None, openapi_url=None)

    def refuse(status: int, detail: str) -> Response:
        return JSONResponse({"detail": detail}, status_code=status, headers=HEADERS)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in hosts:
            return refuse(400, "this demo answers only on its loopback address")
        if request.method not in ("GET", "HEAD"):
            sent = request.headers.get("x-latch-token", "")
            if request.headers.get("origin") not in origins:
                return refuse(403, "request origin is not this page")
            if not secrets.compare_digest(sent.encode(), token.encode()):
                return refuse(403, "missing or wrong page token")
            if not request.headers.get("content-type", "").startswith("application/json"):
                return refuse(403, "requests must be JSON")
        response = await call_next(request)
        response.headers.update(HEADERS)
        return response

    @app.exception_handler(ValueError)
    async def bad_request(request: Request, exc: ValueError):
        return refuse(400, str(exc))

    def state() -> dict[str, Any]:
        live_info = {"on": live is not None, "model": live.model if live else None}
        return {**session.state(), "live": live_info}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(page)

    @app.get("/static/{name}")
    def static(name: str):
        if name not in STATIC:
            raise HTTPException(404, "not found")
        return Response(static_text(name), media_type=STATIC[name])

    @app.get("/api/state")
    def get_state():
        with lock:
            return state()

    @app.get("/api/profiles")
    def get_profiles():
        return session.profiles()

    @app.get("/api/audit")
    def get_audit():
        with lock:
            return session.audit()

    @app.get("/api/scenarios")
    def get_scenarios():
        return {"scenarios": [{"name": name} for name in BUILDERS]}

    @app.post("/api/propose")
    def propose(body: ProposeBody):
        with lock:
            return session.propose(body.profile, body.tool, body.arguments, body.operation_id)

    @app.post("/api/approve")
    def approve(body: OperationBody):
        with lock:
            return session.approve(body.profile, body.operation_id)

    @app.post("/api/deny")
    def deny(body: OperationBody):
        with lock:
            return session.deny(body.profile, body.operation_id)

    @app.post("/api/run")
    def run(body: OperationBody):
        with lock:
            return session.run(body.profile, body.operation_id)

    @app.post("/api/scenario/{name}")
    def scenario(name: str, body: EmptyBody):
        if name not in BUILDERS:
            raise HTTPException(404, f"unknown scenario {name}")
        # Each scenario builds its own session and fake backend, like the terminal demo.
        return to_data(run_one(name, db_path=scenario_db))

    @app.post("/api/live/turn")
    def live_turn(body: TurnBody):
        if live is None:
            raise HTTPException(409, "live mode is off")
        session.ctx(body.profile)
        try:
            events = adapter.run_turn(locked, body.profile, body.text, chat_client, live)
        except adapter.MalformedResponse as exc:
            raise HTTPException(502, f"model response refused: {exc}") from exc
        except adapter.ModelRequestFailed as exc:
            raise HTTPException(502, str(exc)) from exc
        with lock:
            return {"events": events, "state": state()}

    return app


def serve(
    host: str = "127.0.0.1", port: int = 8765, db: str = DEFAULT_DB, live: bool = False
) -> None:
    if host not in LOOPBACK:
        raise ConfigError(f"the demo serves only on 127.0.0.1 or ::1, not {host}")
    config = LiveConfig.from_env() if live else None
    import uvicorn

    session = DemoSession(db)
    app = create_app(session, secrets.token_urlsafe(32), port=port, live=config,
                     scenario_db=db)
    shown = f"[{host}]" if ":" in host else host
    print(f"Latch demo on http://{shown}:{port} with sample identities and a fake backend. "
          "Ctrl-C stops it.", flush=True)
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        session.close()

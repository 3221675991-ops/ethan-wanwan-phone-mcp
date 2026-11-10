"""A small MCP server for iPhone screen/activity data and Jomo session control."""

import hmac
import json
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image as PILImage
from mcp.server.fastmcp import FastMCP, Image
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


DATA = Path(os.getenv("PHONE_MCP_DATA_DIR", "./data")).expanduser().resolve()
LATEST = DATA / "latest.jpg"
STATE = DATA / "state.json"
LOG = DATA / "app-usage.jsonl"
COMMAND = DATA / "command.json"
SCREEN_TIME = DATA / "screen-time.json"
TOKEN = os.environ["PHONE_UPLOAD_TOKEN"].strip()
TZ = ZoneInfo(os.getenv("PHONE_MCP_TIMEZONE", "Asia/Shanghai"))
HOST = os.getenv("PHONE_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("PHONE_MCP_PORT", "18770"))

if not TOKEN:
    raise RuntimeError("PHONE_UPLOAD_TOKEN must not be empty")

DATA.mkdir(parents=True, exist_ok=True)

mcp = FastMCP(
    "Phone Screen MCP",
    instructions=(
        "Read the latest uploaded phone screenshot, app activity and screen time; "
        "send whole-session lock or unlock commands to an iOS Shortcut."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def authorized(request: Request) -> bool:
    supplied = request.headers.get("authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {TOKEN}")


class MCPBearerAuthMiddleware:
    """Protect the MCP endpoint with the same bearer token as phone uploads."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            supplied = ""
            for name, value in scope.get("headers", []):
                if name.lower() == b"authorization":
                    supplied = value.decode("latin-1")
                    break
            if not hmac.compare_digest(supplied, f"Bearer {TOKEN}"):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def write_json_atomic(path: Path, data: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def read_state() -> dict:
    value = read_json(STATE, {"current_app": None, "updated_at": None})
    return value if isinstance(value, dict) else {"current_app": None, "updated_at": None}


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({"ok": True, "time": now()})


@mcp.custom_route("/upload", methods=["POST"])
async def upload_screen(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.body()
    if not body or len(body) > 20 * 1024 * 1024:
        return JSONResponse({"error": "invalid image size"}, status_code=400)

    try:
        image = PILImage.open(BytesIO(body))
        image.verify()
        image = PILImage.open(BytesIO(body)).convert("RGB")
        temporary = DATA / "latest.tmp.jpg"
        image.save(temporary, "JPEG", quality=88, optimize=True)
        temporary.replace(LATEST)
    except Exception:
        return JSONResponse({"error": "invalid image"}, status_code=400)

    state = read_state()
    state["screenshot_at"] = now()
    write_json_atomic(STATE, state)
    return JSONResponse({"ok": True, "uploaded_at": state["screenshot_at"]})


@mcp.custom_route("/event", methods=["POST"])
async def record_app_event(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
        app_name = str(payload["app"]).strip()[:100]
        event = str(payload.get("event", "open")).strip().lower()
        if not app_name or event not in {"open", "close"}:
            raise ValueError
    except Exception:
        return JSONResponse({"error": "invalid event"}, status_code=400)

    timestamp = now()
    state = read_state()
    state.update(
        {
            "current_app": app_name if event == "open" else None,
            "last_app": app_name,
            "last_event": event,
            "updated_at": timestamp,
        }
    )
    write_json_atomic(STATE, state)

    with LOG.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(
                {"app": app_name, "event": event, "time": timestamp},
                ensure_ascii=False,
            )
            + "\n"
        )

    return JSONResponse({"ok": True, "time": timestamp})


@mcp.custom_route("/screen-time", methods=["POST"])
async def upload_today_screen_time(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.body()
    if not body or len(body) > 64 * 1024:
        return JSONResponse({"error": "invalid screen time payload"}, status_code=400)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            payload = {"raw": body.decode("utf-8").strip()}
        except UnicodeDecodeError:
            return JSONResponse(
                {"error": "invalid screen time payload"}, status_code=400
            )

    record = {"screen_time": payload, "updated_at": now()}
    write_json_atomic(SCREEN_TIME, record)
    return JSONResponse({"ok": True, "updated_at": record["updated_at"]})


def set_phone_command(command: str) -> dict:
    data = {"command": command, "created_at": now()}
    write_json_atomic(COMMAND, data)
    return {"ok": True, **data}


@mcp.custom_route("/command", methods=["GET"])
async def get_phone_command(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not COMMAND.exists():
        return JSONResponse({"command": "none"})

    data = read_json(COMMAND, {"command": "none"})
    COMMAND.unlink(missing_ok=True)
    return JSONResponse(data if isinstance(data, dict) else {"command": "none"})


@mcp.tool()
def get_latest_screen() -> Image:
    """Return the latest screenshot uploaded by the phone."""
    if not LATEST.exists():
        raise RuntimeError("The phone has not uploaded a screenshot yet")
    return Image(path=str(LATEST))


@mcp.tool()
def get_phone_status() -> dict:
    """Return the current app, last app event and latest screenshot time."""
    return read_state()


@mcp.tool()
def get_recent_app_events(limit: int = 20) -> list[dict]:
    """Return recent app open/close events."""
    if not LOG.exists():
        return []
    limit = max(1, min(int(limit), 100))
    result = []
    for line in LOG.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except json.JSONDecodeError:
            continue
    return result


@mcp.tool()
def get_today_screen_time() -> dict:
    """Return the phone's most recently uploaded screen-time report."""
    value = read_json(SCREEN_TIME, None)
    if not isinstance(value, dict):
        return {"available": False, "screen_time": None, "updated_at": None}
    return {"available": True, **value}


@mcp.tool()
def lock_phone_apps() -> dict:
    """Ask the phone Shortcut to start the configured whole-app Jomo session."""
    return set_phone_command("lock")


@mcp.tool()
def unlock_phone_apps() -> dict:
    """Ask the phone Shortcut to stop the configured whole-app Jomo session."""
    return set_phone_command("unlock")


@mcp.tool()
def get_phone_control_status() -> dict:
    """Return the command waiting for the phone, if any."""
    value = read_json(COMMAND, None)
    if not isinstance(value, dict):
        return {"command": "none", "pending": False}
    return {"pending": True, **value}


app = MCPBearerAuthMiddleware(mcp.streamable_http_app())

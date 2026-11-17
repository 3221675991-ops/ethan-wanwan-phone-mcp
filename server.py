"""A small MCP server for iPhone screen/activity data and Jomo session control."""

import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from PIL import Image as PILImage
from mcp.server.fastmcp import FastMCP, Image
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


VERSION = "2.1.0"
DATA = Path(os.getenv("PHONE_MCP_DATA_DIR", "./data")).expanduser().resolve()
LATEST = DATA / "latest.jpg"
STATE = DATA / "state.json"
LOG = DATA / "app-usage.jsonl"
COMMAND = DATA / "command.json"
ACK = DATA / "ack.json"
HEARTBEAT = DATA / "heartbeat.json"
SCREEN_TIME = DATA / "screen-time.json"
TOKEN = os.environ["PHONE_UPLOAD_TOKEN"].strip()
TZ = ZoneInfo(os.getenv("PHONE_MCP_TIMEZONE", "Asia/Shanghai"))
HOST = os.getenv("PHONE_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("PHONE_MCP_PORT", "18770"))
BARK_PUSH_URL = os.getenv("BARK_PUSH_URL", "").strip()
HEARTBEAT_TIMEOUT = max(15, int(os.getenv("HEARTBEAT_TIMEOUT", "90")))
COMMAND_LOCK = Lock()

if not TOKEN:
    raise RuntimeError("PHONE_UPLOAD_TOKEN must not be empty")

DATA.mkdir(parents=True, exist_ok=True)

mcp = FastMCP(
    "Phone Screen MCP",
    instructions=(
        "Read the latest uploaded phone screenshot, app activity and screen time; "
        "send whole-session lock or unlock commands to an iOS Shortcut; "
        "request a consent-gated fresh screenshot by sending a Bark notification; "
        "inspect phone heartbeat, command delivery and acknowledgements."
    ),
    host=HOST,
    port=PORT,
    stateless_http=True,
)


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def now_ts() -> float:
    return time.time()


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


def update_heartbeat() -> dict:
    heartbeat = {"last_seen": now(), "last_seen_ts": now_ts()}
    write_json_atomic(HEARTBEAT, heartbeat)
    return heartbeat


def phone_health() -> dict:
    heartbeat = read_json(HEARTBEAT, {})
    if not isinstance(heartbeat, dict) or "last_seen_ts" not in heartbeat:
        return {
            "alive": False,
            "last_seen": None,
            "silence_seconds": None,
            "timeout_threshold": HEARTBEAT_TIMEOUT,
        }
    try:
        elapsed = max(0, round(now_ts() - float(heartbeat["last_seen_ts"])))
    except (TypeError, ValueError):
        elapsed = None
    alive = elapsed is not None and elapsed < HEARTBEAT_TIMEOUT
    return {
        "alive": alive,
        "last_seen": heartbeat.get("last_seen"),
        "silence_seconds": elapsed,
        "timeout_threshold": HEARTBEAT_TIMEOUT,
    }


def command_snapshot() -> dict | None:
    value = read_json(COMMAND, None)
    return value if isinstance(value, dict) else None


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({
        "ok": True, "version": VERSION, "time": now(), "phone": phone_health()
    })


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
    update_heartbeat()
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
    update_heartbeat()
    timestamp = now()
    state = read_state()
    state.update({
        "current_app": app_name if event == "open" else None,
        "last_app": app_name,
        "last_event": event,
        "updated_at": timestamp,
    })
    write_json_atomic(STATE, state)
    with LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(
            {"app": app_name, "event": event, "time": timestamp},
            ensure_ascii=False,
        ) + "\n")
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
            return JSONResponse({"error": "invalid screen time payload"}, status_code=400)
    update_heartbeat()
    record = {"screen_time": payload, "updated_at": now()}
    write_json_atomic(SCREEN_TIME, record)
    return JSONResponse({"ok": True, "updated_at": record["updated_at"]})


def set_phone_command(command: str) -> dict:
    """Queue one command; never overwrite an unacknowledged command."""
    with COMMAND_LOCK:
        pending = command_snapshot()
        if pending and pending.get("command") not in (None, "none"):
            return {
                "ok": False,
                "error": "command_pending",
                "message": "An earlier phone command is still awaiting acknowledgement.",
                "pending_command": pending,
            }
        timestamp = now()
        data = {
            "command": command,
            "id": uuid.uuid4().hex,
            "created_at": timestamp,
            "created_ts": now_ts(),
            "delivered": False,
            "delivery_count": 0,
            "attempts": 0,
        }
        write_json_atomic(COMMAND, data)
    return {"ok": True, **data}


@mcp.custom_route("/command", methods=["GET"])
async def get_phone_command(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    update_heartbeat()
    with COMMAND_LOCK:
        data = command_snapshot()
        if not data or data.get("command") in (None, "none"):
            return JSONResponse({"command": "none"})
        # Migrate a command created by the pre-ACK version without losing it.
        data.setdefault("id", uuid.uuid4().hex)
        data.setdefault("created_at", now())
        data.setdefault("created_ts", now_ts())
        data.setdefault("delivery_count", 0)
        data.setdefault("attempts", 0)
        data["delivered"] = True
        data["delivery_count"] = int(data.get("delivery_count", 0)) + 1
        data["last_delivered_at"] = now()
        data["last_delivered_ts"] = now_ts()
        write_json_atomic(COMMAND, data)
    return JSONResponse(data)


@mcp.custom_route("/ack", methods=["POST"])
async def acknowledge_phone_command(request: Request):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        payload = await request.json()
        command_id = str(payload["command_id"]).strip()
        status = str(payload.get("status", "done")).strip().lower()
        error = str(payload.get("error", "")).strip()[:500]
        if not command_id or status not in {"done", "failed"}:
            raise ValueError
    except Exception:
        return JSONResponse({"error": "invalid acknowledgement"}, status_code=400)
    update_heartbeat()
    with COMMAND_LOCK:
        current = command_snapshot()
        previous_ack = read_json(ACK, None)
        if not current or current.get("id") != command_id:
            if (
                isinstance(previous_ack, dict)
                and previous_ack.get("command_id") == command_id
                and previous_ack.get("status") == status
            ):
                return JSONResponse({"ok": True, "duplicate": True, **previous_ack})
            return JSONResponse({"error": "command_id_mismatch"}, status_code=409)
        acknowledgement = {
            "command_id": command_id,
            "command": current.get("command"),
            "status": status,
            "acked_at": now(),
            "error": error or None,
        }
        write_json_atomic(ACK, acknowledgement)
        if status == "done":
            COMMAND.unlink(missing_ok=True)
        else:
            current["delivered"] = False
            current["attempts"] = int(current.get("attempts", 0)) + 1
            current["last_error"] = error or "Phone reported command failure"
            current["last_failed_at"] = now()
            write_json_atomic(COMMAND, current)
    return JSONResponse({"ok": True, **acknowledgement})


@mcp.tool()
def get_latest_screen() -> Image:
    """Return the latest screenshot uploaded by the phone after owner consent."""
    if not LATEST.exists():
        raise RuntimeError("The phone has not uploaded a screenshot yet")
    return Image(path=str(LATEST))


@mcp.tool()
def get_phone_status() -> dict:
    """Return current app, last event, screenshot time and phone heartbeat."""
    state = read_state()
    state["phone"] = phone_health()
    return state


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


def send_bark_notification(title: str, body: str) -> dict:
    """Send one Bark notification without exposing the private push URL."""
    if not BARK_PUSH_URL:
        raise RuntimeError("BARK_PUSH_URL is not configured on the server")
    payload = json.dumps({
        "title": title,
        "body": body,
        "group": "ethan-wanwan-screen",
        "level": "active",
        "action": "none",
    }, ensure_ascii=False).encode("utf-8")
    push_request = urllib.request.Request(
        BARK_PUSH_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(push_request, timeout=10) as response:
            response.read(64 * 1024)
            status = int(getattr(response, "status", 200))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("Bark push failed") from exc
    if not 200 <= status < 300:
        raise RuntimeError(f"Bark push failed with HTTP {status}")
    return {"ok": True, "provider": "bark", "sent_at": now()}


@mcp.tool()
def request_screenshot() -> dict:
    """Ask the owner for consent before the phone captures and uploads a screenshot.

    This tool only sends a Bark notification. It never queues a screenshot command.
    The iPhone notification automation must receive the owner's confirmation before
    its Shortcut captures the screen and uploads it through the existing /upload route.
    """
    sent = send_bark_notification(
        "Ethan 想看看晚晚现在在干嘛",
        "允许获取一次当前屏幕截图吗？只有你确认后才会截图并上传。",
    )
    return {**sent, "consent_required": True, "queued_phone_command": False}


@mcp.tool()
def get_phone_control_status() -> dict:
    """Return the command waiting for the phone and its last acknowledgement."""
    pending = command_snapshot()
    last_ack = read_json(ACK, None)
    return {
        "pending": bool(pending and pending.get("command") not in (None, "none")),
        "command": pending.get("command", "none") if pending else "none",
        "pending_command": pending,
        "last_ack": last_ack if isinstance(last_ack, dict) else None,
    }


@mcp.tool()
def watchdog_health() -> dict:
    """Return server, phone heartbeat, screenshot freshness and command ACK status."""
    phone = phone_health()
    state = read_state()
    pending = command_snapshot()
    last_ack = read_json(ACK, None)
    screen_time = read_json(SCREEN_TIME, None)
    pending_summary = None
    if pending and pending.get("command") not in (None, "none"):
        try:
            age = max(0, round(now_ts() - float(pending.get("created_ts", now_ts()))))
        except (TypeError, ValueError):
            age = None
        pending_summary = {
            "command": pending.get("command"),
            "id": pending.get("id"),
            "created_at": pending.get("created_at"),
            "delivered": bool(pending.get("delivered")),
            "delivery_count": int(pending.get("delivery_count", 0)),
            "attempts": int(pending.get("attempts", 0)),
            "age_seconds": age,
            "stuck": age is not None and age > HEARTBEAT_TIMEOUT and (
                not phone["alive"] or bool(pending.get("delivered"))
            ),
            "last_error": pending.get("last_error"),
        }
    return {
        "server": {"ok": True, "version": VERSION, "time": now()},
        "phone": phone,
        "screenshot": {"exists": LATEST.exists(), "taken_at": state.get("screenshot_at")},
        "current_app": state.get("current_app"),
        "pending_command": pending_summary,
        "last_ack": last_ack if isinstance(last_ack, dict) else None,
        "screen_time_updated_at": (
            screen_time.get("updated_at") if isinstance(screen_time, dict) else None
        ),
    }


app = MCPBearerAuthMiddleware(mcp.streamable_http_app())

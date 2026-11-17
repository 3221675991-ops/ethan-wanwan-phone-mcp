import asyncio
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image
from starlette.requests import Request

# The local development container may not have the MCP SDK installed.
# CI installs requirements.txt and exercises the real FastMCP integration.
try:
    import mcp.server.fastmcp  # noqa: F401
except ModuleNotFoundError:
    import sys
    import types

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FakeImage:
        def __init__(self, path: str):
            self.path = path

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def custom_route(self, *args, **kwargs):
            return lambda function: function

        def tool(self, *args, **kwargs):
            return lambda function: function

        def streamable_http_app(self):
            async def fake_app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})
            return fake_app

    fastmcp_module.FastMCP = FakeFastMCP
    fastmcp_module.Image = FakeImage
    sys.modules.update({
        "mcp": mcp_module,
        "mcp.server": server_module,
        "mcp.server.fastmcp": fastmcp_module,
    })


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PHONE_UPLOAD_TOKEN", "test-token")
os.environ.setdefault("PHONE_MCP_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("BARK_PUSH_URL", "https://example.invalid/test")

spec = importlib.util.spec_from_file_location("phone_screen_mcp", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(server)


def reset_data():
    for path in [
        server.STATE, server.LOG, server.COMMAND, server.ACK,
        server.HEARTBEAT, server.SCREEN_TIME, server.LATEST,
    ]:
        path.unlink(missing_ok=True)


def request(method: str, path: str, body: bytes = b"", authorized: bool = True):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = []
    if authorized:
        headers.append((b"authorization", b"Bearer test-token"))
    return Request(
        {"type": "http", "method": method, "path": path, "headers": headers, "query_string": b""},
        receive,
    )


async def call_app(path: str, authorized: bool = False):
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    headers = []
    if authorized:
        headers.append((b"authorization", b"Bearer test-token"))
    await server.app(
        {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 18770),
        },
        receive,
        send,
    )
    return messages


async def main():
    reset_data()
    server.now = lambda: "2026-11-17T18:00:00+08:00"
    server.now_ts = lambda: 1794909600.0

    # Commands remain pending until a matching successful ACK arrives.
    created = server.lock_phone_apps()
    assert created["ok"] is True
    assert created["command"] == "lock"
    assert created["id"]
    assert server.unlock_phone_apps()["error"] == "command_pending"

    first_delivery = await server.get_phone_command(request("GET", "/command"))
    first_data = json.loads(first_delivery.body)
    assert first_data["id"] == created["id"]
    assert first_data["delivered"] is True
    assert first_data["delivery_count"] == 1

    # Polling again returns the same ID until the phone confirms execution.
    second_delivery = await server.get_phone_command(request("GET", "/command"))
    second_data = json.loads(second_delivery.body)
    assert second_data["id"] == created["id"]
    assert second_data["delivery_count"] == 2

    mismatch = await server.acknowledge_phone_command(
        request("POST", "/ack", json.dumps({"command_id": "wrong", "status": "done"}).encode())
    )
    assert mismatch.status_code == 409
    assert server.get_phone_control_status()["pending"] is True

    failed = await server.acknowledge_phone_command(
        request("POST", "/ack", json.dumps({
            "command_id": created["id"], "status": "failed", "error": "Jomo action failed"
        }).encode())
    )
    assert failed.status_code == 200
    assert server.get_phone_control_status()["pending"] is True
    assert server.get_phone_control_status()["pending_command"]["attempts"] == 1

    # Failed ACK resets delivery state so the same command can be retried.
    retry = await server.get_phone_command(request("GET", "/command"))
    assert json.loads(retry.body)["id"] == created["id"]

    done = await server.acknowledge_phone_command(
        request("POST", "/ack", json.dumps({"command_id": created["id"], "status": "done"}).encode())
    )
    assert done.status_code == 200
    assert server.get_phone_control_status()["pending"] is False
    assert (await server.get_phone_command(request("GET", "/command"))).body == b'{"command":"none"}'

    # A repeated ACK is idempotent.
    duplicate = await server.acknowledge_phone_command(
        request("POST", "/ack", json.dumps({"command_id": created["id"], "status": "done"}).encode())
    )
    assert duplicate.status_code == 200
    assert json.loads(duplicate.body)["duplicate"] is True

    # A legacy pre-ACK command is upgraded in place and remains executable.
    server.write_json_atomic(server.COMMAND, {"command": "lock", "created_at": server.now()})
    legacy = await server.get_phone_command(request("GET", "/command"))
    legacy_data = json.loads(legacy.body)
    assert legacy_data["id"]
    assert legacy_data["delivery_count"] == 1
    legacy_ack = await server.acknowledge_phone_command(
        request("POST", "/ack", json.dumps({"command_id": legacy_data["id"], "status": "done"}).encode())
    )
    assert legacy_ack.status_code == 200

    # Heartbeat is updated by polling and exposed in health tools.
    assert server.phone_health()["alive"] is True
    health = server.watchdog_health()
    assert health["phone"]["alive"] is True
    assert health["server"]["version"] == "2.1.0"

    # Screenshot requests still require owner consent and do not enter the command queue.
    server.send_bark_notification = lambda title, body: {
        "ok": True, "provider": "bark", "sent_at": server.now()
    }
    screenshot_request = server.request_screenshot()
    assert screenshot_request["ok"] is True
    assert screenshot_request["consent_required"] is True
    assert screenshot_request["queued_phone_command"] is False
    assert server.get_phone_control_status()["pending"] is False

    # Existing event, screen-time and screenshot upload endpoints remain compatible.
    payload = json.dumps({"total_minutes": 123}, ensure_ascii=False).encode()
    response = await server.upload_today_screen_time(request("POST", "/screen-time", payload))
    assert response.status_code == 200
    assert server.get_today_screen_time()["screen_time"]["total_minutes"] == 123

    event = json.dumps({"app": "Example", "event": "open"}).encode()
    response = await server.record_app_event(request("POST", "/event", event))
    assert response.status_code == 200
    assert server.get_phone_status()["current_app"] == "Example"

    image = Image.new("RGB", (8, 8), "pink")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = await server.upload_screen(request("POST", "/upload", buffer.getvalue()))
    assert response.status_code == 200
    assert server.LATEST.exists()

    unauthorized = await server.upload_today_screen_time(
        request("POST", "/screen-time", payload, authorized=False)
    )
    assert unauthorized.status_code == 401
    unauthorized_ack = await server.acknowledge_phone_command(
        request("POST", "/ack", b"{}", authorized=False)
    )
    assert unauthorized_ack.status_code == 401
    messages = await call_app("/mcp", authorized=False)
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 401


if __name__ == "__main__":
    asyncio.run(main())
    print("tests passed")

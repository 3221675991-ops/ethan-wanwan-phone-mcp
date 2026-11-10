import asyncio
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PHONE_UPLOAD_TOKEN", "test-token")
os.environ.setdefault("PHONE_MCP_DATA_DIR", tempfile.mkdtemp())

spec = importlib.util.spec_from_file_location("phone_screen_mcp", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(server)


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
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
        },
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
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 18770),
        },
        receive,
        send,
    )
    return messages


async def main():
    server.now = lambda: "2026-11-10T18:00:00+08:00"

    assert server.lock_phone_apps()["command"] == "lock"
    response = await server.get_phone_command(request("GET", "/command"))
    assert json.loads(response.body)["command"] == "lock"
    assert not server.COMMAND.exists()

    assert server.unlock_phone_apps()["command"] == "unlock"
    assert server.get_phone_control_status()["pending"] is True

    payload = json.dumps({"total_minutes": 123}, ensure_ascii=False).encode()
    response = await server.upload_today_screen_time(
        request("POST", "/screen-time", payload)
    )
    assert response.status_code == 200
    assert server.get_today_screen_time()["screen_time"]["total_minutes"] == 123

    event = json.dumps({"app": "Example", "event": "open"}).encode()
    response = await server.record_app_event(request("POST", "/event", event))
    assert response.status_code == 200
    assert server.get_phone_status()["current_app"] == "Example"

    image = Image.new("RGB", (8, 8), "pink")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = await server.upload_screen(
        request("POST", "/upload", buffer.getvalue())
    )
    assert response.status_code == 200
    assert server.LATEST.exists()

    response = await server.upload_today_screen_time(
        request("POST", "/screen-time", payload, authorized=False)
    )
    assert response.status_code == 401

    messages = await call_app("/mcp", authorized=False)
    start = next(message for message in messages if message["type"] == "http.response.start")
    assert start["status"] == 401


if __name__ == "__main__":
    asyncio.run(main())
    print("tests passed")

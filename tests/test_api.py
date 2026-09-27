import json
import stat

from sphere_mesh.api import API_VERSION, UnixApiServer, request


async def test_api_round_trip_and_private_socket(tmp_path):
    calls = []

    async def handler(method, params):
        calls.append((method, params))
        return {"connected": True, "generation": 4}

    path = tmp_path / "nested" / "sphere.sock"
    server = UnixApiServer(path, handler)
    await server.start()
    try:
        response = await request(path, "health", request_id="probe")
        assert response == {
            "version": API_VERSION,
            "id": "probe",
            "ok": True,
            "result": {"connected": True, "generation": 4},
        }
        assert calls == [("health", {})]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        await server.close()

    assert not path.exists()


async def test_api_returns_public_request_errors(tmp_path):
    from sphere_mesh.api import ApiRequestError

    async def handler(method, params):
        raise ApiRequestError("unknown_method", f"unknown method: {method}")

    path = tmp_path / "sphere.sock"
    server = UnixApiServer(path, handler)
    await server.start()
    try:
        response = await request(path, "secret")
    finally:
        await server.close()

    assert response["ok"] is False
    assert response["error"] == {
        "code": "unknown_method",
        "message": "unknown method: secret",
    }


async def test_api_rejects_unsupported_version(tmp_path):
    async def handler(method, params):
        raise AssertionError("handler must not run")

    path = tmp_path / "sphere.sock"
    server = UnixApiServer(path, handler)
    await server.start()
    try:
        reader, writer = await __import__("asyncio").open_unix_connection(path)
        writer.write(
            json.dumps({"version": 99, "method": "health"}).encode() + b"\n"
        )
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported_version"


async def test_api_refuses_to_replace_regular_file(tmp_path):
    path = tmp_path / "sphere.sock"
    path.write_text("keep me")
    server = UnixApiServer(path, lambda *_: None)

    try:
        await server.start()
    except RuntimeError as exc:
        assert "non-socket" in str(exc)
    else:
        raise AssertionError("regular file was replaced")

    assert path.read_text() == "keep me"


async def test_api_rejects_oversized_request(tmp_path):
    import asyncio

    async def handler(method, params):
        raise AssertionError("handler must not run")

    path = tmp_path / "sphere.sock"
    server = UnixApiServer(path, handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(b"x" * (64 * 1024 + 1) + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert response["ok"] is False
    assert response["error"]["code"] == "message_too_large"

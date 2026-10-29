"""Local visual QA tool. Binds to localhost and reviews images with esora-api.

    python3 server.py
    open http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 8765
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CHECK_SCRIPT = ROOT / "check_image.py"
MAX_BODY = 80 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
ALLOWED_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

_login_lock = threading.Lock()


def esora_python() -> Path:
    binary = shutil.which("esora-api")
    if not binary:
        raise RuntimeError("esora-api が見つかりません。CLI をインストールしてください。")
    shebang = Path(binary).read_text(encoding="utf-8").splitlines()[0]
    if not shebang.startswith("#!"):
        raise RuntimeError("esora-api の Python を特定できません。")
    return Path(shebang[2:].strip())


def run_esora(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["esora-api", "--json", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "image-check/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/status":
            self.handle_status()
            return
        if path == "/api/models":
            self.handle_models()
            return
        self.serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/login":
            self.handle_login()
            return
        if path == "/api/check":
            self.handle_check()
            return
        self.send_error(404)

    def handle_status(self) -> None:
        try:
            completed = run_esora("auth", "status", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})
            return
        payload = parse_cli_json(completed.stdout)
        if completed.returncode != 0 and "error" not in payload:
            payload = {"signed_in": False, "error": cli_error(completed)}
        self.send_json(200, payload)

    def handle_models(self) -> None:
        try:
            completed = run_esora("model", "list", "--task", "text", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})
            return
        if completed.returncode != 0:
            self.send_json(completed.returncode == 2 and 401 or 502, {"ok": False, "error": cli_error(completed)})
            return
        models = parse_cli_json(completed.stdout)
        if not isinstance(models, list):
            self.send_json(502, {"ok": False, "error": "モデル一覧を読めませんでした。"})
            return
        visible = []
        for item in models:
            caps = item.get("capabilities") or {}
            if not caps.get("supports_reference_images"):
                continue
            visible.append({"id": item.get("id"), "label": item.get("label") or item.get("id")})
        self.send_json(200, {"models": visible})

    def handle_login(self) -> None:
        if not _login_lock.acquire(blocking=False):
            self.send_json(409, {"ok": False, "error": "サインインを待っています。ブラウザを確認してください。"})
            return
        try:
            completed = run_esora("auth", "login", timeout=320)
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "error": "サインインがタイムアウトしました。"})
            return
        except OSError as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})
            return
        finally:
            _login_lock.release()
        if completed.returncode != 0:
            self.send_json(401, {"ok": False, "error": cli_error(completed)})
            return
        self.send_json(200, {"ok": True})

    def handle_check(self) -> None:
        try:
            payload = self.read_json()
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        mime = str(payload.get("mime") or "")
        suffix = ALLOWED_TYPES.get(mime)
        if suffix is None:
            self.send_json(400, {"ok": False, "error": "PNG、JPEG、WebP を選んでください。"})
            return
        encoded = payload.get("data_base64")
        if not isinstance(encoded, str) or not encoded:
            self.send_json(400, {"ok": False, "error": "画像データがありません。"})
            return
        try:
            raw = decode_base64(encoded)
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if len(raw) > MAX_IMAGE:
            self.send_json(400, {"ok": False, "error": "画像は 25MB 以下にしてください。"})
            return

        note = payload.get("note") if isinstance(payload.get("note"), str) else ""
        model = payload.get("model") if isinstance(payload.get("model"), str) else ""
        try:
            reference = optional_image(payload, "reference_mime", "reference_base64")
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        try:
            result = review_image(raw, suffix, note=note, model=model, reference=reference)
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "error": "確認がタイムアウトしました。時間をおいて再度お試しください。"})
            return
        except (OSError, RuntimeError) as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})
            return

        status = 200 if result.get("ok") else {"auth": 401, "timeout": 504}.get(str(result.get("code")), 502)
        self.send_json(status, result)

    def serve_static(self, path: str) -> None:
        name = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.css": "app.css",
            "/app.js": "app.js",
            "/redline.js": "redline.js",
            "/checklist.js": "checklist.js",
        }.get(path)
        if name is None:
            self.send_error(404)
            return
        file = STATIC / name
        content = file.read_bytes()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("リクエストのサイズが不正です。")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON を読めませんでした。") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON オブジェクトが必要です。")
        return payload

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def review_image(raw: bytes, suffix: str, *, note: str, model: str, reference: tuple[bytes, str] | None = None) -> dict:
    python = esora_python()
    with tempfile.TemporaryDirectory(prefix="image-check-") as directory:
        image = Path(directory) / f"upload{suffix}"
        image.write_bytes(raw)
        options = {"note": note, "model": model}
        if reference is not None:
            reference_bytes, reference_suffix = reference
            reference_path = Path(directory) / f"reference{reference_suffix}"
            reference_path.write_bytes(reference_bytes)
            options["reference"] = str(reference_path)
        completed = subprocess.run(
            [str(python), str(CHECK_SCRIPT), str(image)],
            input=json.dumps(options, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=360,
            check=False,
        )
    if completed.stderr.strip():
        print(completed.stderr.strip(), flush=True)
    payload = parse_cli_json(completed.stdout)
    if not isinstance(payload, dict) or "ok" not in payload:
        message = cli_error(completed) or "確認結果を読めませんでした。"
        return {"ok": False, "error": message, "code": "invalid"}
    return payload


def optional_image(payload: dict, mime_key: str, data_key: str) -> tuple[bytes, str] | None:
    mime = payload.get(mime_key) or ""
    encoded = payload.get(data_key) or ""
    if not mime and not encoded:
        return None
    suffix = ALLOWED_TYPES.get(str(mime))
    if suffix is None or not isinstance(encoded, str) or not encoded:
        raise ValueError("参考画像は PNG、JPEG、WebP にしてください。")
    raw = decode_base64(encoded)
    if len(raw) > MAX_IMAGE:
        raise ValueError("参考画像は 25MB 以下にしてください。")
    return raw, suffix


def decode_base64(value: str) -> bytes:
    import base64

    compact = value.split(",", 1)[-1].strip()
    try:
        return base64.b64decode(compact, validate=True)
    except Exception as exc:
        raise ValueError("画像データを解釈できませんでした。") from exc


def parse_cli_json(stdout: str) -> object:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {}


def cli_error(completed: subprocess.CompletedProcess[str]) -> str:
    for stream in (completed.stderr, completed.stdout):
        parsed = parse_cli_json(stream)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str) and error:
                return error
    text = (completed.stderr or completed.stdout).strip()
    return text or f"esora-api exited {completed.returncode}"


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Image check tool: http://{HOST}:{PORT}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

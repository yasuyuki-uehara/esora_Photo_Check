"""Send one image to Esora and return visual QA findings as JSON.

Run with the esora-api CLI's Python so the installed client and keychain
session are reused:

    esora-python check_image.py image.png <<'EOF'
    {"note": "正しい表記", "model": "gemini_3_5_flash_text"}
    EOF
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from esora_api_cli.api import Api, GenerationTimeout
from esora_api_cli.auth import AuthError
from esora_api_cli.commands import UploadRefused, _upload_file
from esora_api_cli.config import resolve_profile
from esora_api_cli.http import ApiError, Client

DEFAULT_MODEL = "gemini_3_5_flash_text"
WAIT_SECONDS = 300.0

CATEGORIES = ("文字切れ", "誤字", "余白", "レイアウト")
SEVERITIES = ("high", "medium", "low")

PROMPT = """あなたは日本語のWeb画像（バナー、LP、告知、UIパーツ）の校正者です。渡された参照画像を見て、次の4種類だけを指摘してください。

- 文字切れ: 文字が枠や画像の端で切れている、欠けている、はみ出している
- 誤字: 誤字、脱字、文字化け、不自然な日本語、指定された正しい表記との不一致
- 余白: 文字や要素の間隔・余白が不揃い、詰まりすぎ、空きすぎ
- レイアウト: 重なり、傾き、欠け、意図せず崩れた配置

デザインの好みや改善案は書かない。画像から読み取れる事実だけを書く。同じ箇所を繰り返さない。問題がなければ findings は空配列にする。
斜め配置、文字の重ね、画面端までの枠、文字数の違いによる段のずれは、装飾として組まれていれば指摘しない。文字が読めない、文字の一部が欠けている、明らかな誤字、不自然な空白があるときだけ指摘する。

次の形のJSONオブジェクトだけを返す。
{
  "summary": "全体を一文で",
  "findings": [
    {
      "category": "文字切れ" または "誤字" または "余白" または "レイアウト",
      "severity": "high" または "medium" または "low",
      "location": "画像のどこか",
      "detail": "何が問題か"
    }
  ]
}

severity は high=公開前に直すべき、medium=見た目に影響する、low=軽微。"""


class _Quiet:
    as_json = True


def main() -> int:
    if len(sys.argv) != 2:
        emit_error("Usage: check_image.py <image>", code="invalid")
        return 1
    image = Path(sys.argv[1]).expanduser()
    if not image.is_file():
        emit_error(f"No such file: {image}", code="invalid")
        return 1

    options = read_options()
    try:
        result = check(image, note=options.get("note") or "", model=options.get("model") or "")
    except AuthError as exc:
        emit_error(str(exc) or "サインインが必要です。", code="auth")
        return 2
    except UploadRefused as exc:
        emit_error(str(exc), code="upload")
        return 1
    except GenerationTimeout as exc:
        emit_error(str(exc), code="timeout")
        return 1
    except ApiError as exc:
        code = "auth" if exc.status_code in (401, 403) else "api"
        emit_error(str(exc), code=code)
        return 2 if code == "auth" else 1
    except (OSError, ValueError, RuntimeError) as exc:
        emit_error(str(exc), code="invalid")
        return 1

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def read_options() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Options must be a JSON object.")
    return parsed


def check(image: Path, *, note: str, model: str) -> dict[str, Any]:
    profile = resolve_profile()
    with Client(profile) as client:
        api = Api(client)
        chosen = choose_model(api, model)
        uploaded = _upload_file(
            api,
            image,
            _Quiet(),
            name=image.name,
            tags=["image-check"],
            kinds={"image"},
        )
        record = api.create_generation(
            {
                "type": "text",
                "model": chosen,
                "prompt": build_prompt(note),
                "reference_asset_ids": [uploaded["asset_id"]],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_output_tokens": 4096,
                "thinking_level": "medium",
                "media_resolution": "high",
            }
        )
        record = api.wait_for_generation(record["id"], timeout=WAIT_SECONDS)
        if record.get("status") != "completed":
            raise RuntimeError(record.get("error") or f"Generation {record.get('status')}.")
        summary, findings = parse_result(record.get("result"))
        return {
            "ok": True,
            "summary": summary,
            "findings": findings,
            "model": record.get("model") or chosen,
            "generation_id": record.get("id"),
        }


def choose_model(api: Api, requested: str) -> str:
    models = [
        item
        for item in api.models(task="text_generate")
        if (item.get("capabilities") or {}).get("supports_reference_images")
    ]
    ids = {item.get("id") for item in models}
    if requested:
        if requested not in ids:
            raise ValueError(f"Model {requested} cannot review an image.")
        return requested
    if DEFAULT_MODEL in ids:
        return DEFAULT_MODEL
    if not models:
        raise RuntimeError("No text model that accepts a reference image is available.")
    return str(models[0]["id"])


def build_prompt(note: str) -> str:
    extra = note.strip()
    if not extra:
        return PROMPT
    clipped = extra[:2000]
    return (
        PROMPT
        + "\n\n確認してほしい正しい表記や注意点:\n"
        + clipped
        + "\nこの内容と画像内の文字が違えば、誤字として指摘する。"
    )


def parse_result(result: Any) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(result, dict):
        raise RuntimeError("The model returned no review.")
    if result.get("type") == "json":
        payload = result.get("value")
    else:
        payload = parse_json_text(str(result.get("content") or ""))
    if isinstance(payload, str):
        payload = parse_json_text(payload)
    if not isinstance(payload, dict):
        raise RuntimeError("The review was not a JSON object.")

    summary = str(payload.get("summary") or "").strip()
    raw_findings = payload.get("findings") or []
    if not isinstance(raw_findings, list):
        raise RuntimeError("findings was not a list.")
    findings = [item for item in (normalize_finding(entry) for entry in raw_findings) if item]
    if not summary:
        summary = "問題は見当たりませんでした。" if not findings else f"{len(findings)}件の指摘があります。"
    return summary, findings


def parse_json_text(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.removesuffix("```").strip()
    return json.loads(cleaned)


def normalize_finding(entry: Any) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    detail = str(entry.get("detail") or entry.get("message") or "").strip()
    location = str(entry.get("location") or "").strip()
    if not detail and not location:
        return None
    category = str(entry.get("category") or "").strip()
    if category not in CATEGORIES:
        category = "レイアウト"
    severity = str(entry.get("severity") or "medium").strip().lower()
    severity = {"高": "high", "中": "medium", "低": "low"}.get(severity, severity)
    if severity not in SEVERITIES:
        severity = "medium"
    return {
        "category": category,
        "severity": severity,
        "location": location or "画像全体",
        "detail": detail or location,
    }


def emit_error(message: str, *, code: str) -> None:
    json.dump({"ok": False, "error": message, "code": code}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)

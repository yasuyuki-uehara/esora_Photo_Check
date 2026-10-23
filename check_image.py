"""Send one image to Esora and return visual QA findings as JSON.

Run with the esora-api CLI's Python so the installed client and keychain
session are reused:

    esora-python check_image.py image.png <<'EOF'
    {"note": "正しい表記", "model": "gemini_3_5_flash_text"}
    EOF
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from esora_api_cli.api import Api, GenerationTimeout
from esora_api_cli.auth import AuthError
from esora_api_cli.commands import UploadRefused, _upload_file
from esora_api_cli.config import resolve_profile
from esora_api_cli.http import ApiError, Client

DEFAULT_MODEL = "gemini_3_5_flash_text"
WAIT_SECONDS = 300.0

CATEGORIES = ("文字切れ", "誤字", "余白", "レイアウト", "人体構造", "AIっぽさ", "差分")
SEVERITIES = ("high", "medium", "low")

PROMPT = """あなたは日本語のWeb画像（バナー、LP、告知、UIパーツ、イラスト）の校正者です。渡された参照画像を見て、次の種類だけを指摘してください。

- 文字切れ: 文字が枠や画像の端で切れている、欠けている、はみ出している
- 誤字: 誤字、脱字、文字化け、不自然な日本語、指定された正しい表記との不一致
- 余白: 文字や要素の間隔・余白が不揃い、詰まりすぎ、空きすぎ
- レイアウト: 重なり、傾き、欠け、意図せず崩れた配置。全体を見て、絵として明らかに不自然なところがないかも見る
- 人体構造: 人体としておかしい部分を細かく見る。特に次を確認する。
  - 指: 本数（多い・少ない）、長さ、関節の曲がり、親指の向き、指同士の溶け・分岐
  - 手・腕・足・脚: 本数、関節の向き、左右の取り違え、不自然なねじれ
  - 顔: 目鼻口の位置と数、左右対称の崩れ、耳の有無と位置
  - 肌の色: 部位ごとの色むら、左右や顔と体の不自然な違い、影ではない変色
  - 髪の毛: 量の破綻、不自然な塊、頭皮から浮いた髪、服や背景との溶け
  - 髪の生え方: 生え際の形、分け目、耳まわりや後頭部の生え方の不自然さ
  - 体の比率: 頭・胴・手足のバランス、肩幅、首の長さ、体のつながり
- AIっぽさ: 生成AI特有の破綻。ぼやけた細部、溶けた指や手足、記憶にない文字、対称の崩れ、質感の不自然な混ざり、境界の溶けなど

デザインの好みや改善案は書かない。画像から読み取れる事実だけを書く。同じ箇所を繰り返さない。問題がなければ findings は空配列にする。
斜め配置、文字の重ね、画面端までの枠、文字数の違いによる段のずれは、装飾として組まれていれば指摘しない。文字が読めない、文字の一部が欠けている、明らかな誤字、不自然な空白があるときだけ指摘する。
人物や体の一部が写っていない、または表現が簡略化されたデザインで構造を判断できない場合は、人体構造を無理に指摘しない。見える範囲は指・肌・髪まで細かく見る。軽い生成感だけでは AIっぽさを出さず、はっきりした破綻だけを指摘する。

次の形のJSONオブジェクトだけを返す。
{
  "summary": "全体を一文で",
  "findings": [
    {
      "category": "文字切れ" または "誤字" または "余白" または "レイアウト" または "人体構造" または "AIっぽさ" または "差分",
      "severity": "high" または "medium" または "low",
      "location": "画像のどこか",
      "detail": "何が問題か。赤入れに書く短い一文で、60文字以内",
      "box": [ymin, xmin, ymax, xmax]
    }
  ]
}

severity は high=公開前に直すべき、medium=見た目に影響する、low=軽微。
box は問題の箇所だけをぴったり囲む四角で、画像全体を 0〜1000 とした整数 [ymin, xmin, ymax, xmax] にする。画像全体や箇所を特定できない指摘は null にする。参考画像と比べるときも、box は1枚目（確認する画像）の上の位置にする。"""

BODY_MODEL = "gemini_3_1_pro_text"

BODY_PROMPT = """あなたは人物・キャラクターの描写を数える検査員です。画像に人物やキャラクターの体が写っているときだけ検査する。見えたものをそのまま数え、推測や補正はしない。

手ごとに、親指を含めて指を一本ずつ数える。指同士がくっついている、溶けている、分かれている、余分な指があるときは、見えたままの本数を数える。
手が握られている、物で隠れている、画像の端で切れているなど、指全体を数えられない手は fully_visible を false にする。

次の形のJSONオブジェクトだけを返す。
{
  "has_person": 人物やキャラクターの体が写っているか(true/false),
  "hands": [
    {
      "owner": "誰の手か",
      "side": "左" または "右" または "不明",
      "location": "画像のどこか",
      "fully_visible": 指全体を数えられるか(true/false),
      "finger_count": 数えた指の本数(整数),
      "detail": "どの指が足りない・余分か、形の異常。なければ空文字",
      "box": [ymin, xmin, ymax, xmax]
    }
  ],
  "others": [
    {
      "part": "腕・脚・顔・耳・髪・肌・体の比率など",
      "location": "画像のどこか",
      "detail": "数や位置、向き、色むらなどのはっきりした異常。赤入れに書く短い一文で、60文字以内",
      "box": [ymin, xmin, ymax, xmax]
    }
  ]
}

box は、その手や部位だけをぴったり囲む四角で、画像全体を 0〜1000 とした整数 [ymin, xmin, ymax, xmax] にする。位置を特定できないときは null にする。

others は、腕や脚の本数、関節の向き、顔のパーツの数と位置、耳、髪の根元や生え方、肌の色むら、体の比率のはっきりした異常だけを入れる。異常がなければ空配列にする。"""

# Counting fingers in one shot on a whole picture is unreliable: the model tends
# to answer "5" for any hand. So every hand found by the body pass is cropped,
# enlarged and counted again as "thumb" + "other fingers", which the models do
# much more accurately than naming five fingers in turn.
ZOOM_MAX_HANDS = 4
ZOOM_WAIT_SECONDS = 120.0
ZOOM_LONG_SIDE = 960
ZOOM_PADDING = 0.35
ZOOM_MIN_SIDE = 0.12

# A single model is noisy on borderline hands (the same crop reads 4 or 5), so
# several models vote. Keep this prompt short: extra instructions measurably
# pushed the answers back to "5".
ZOOM_MODELS = ("gemini_3_5_flash_text", "gemini_3_8_flash_text", "gemini_3_1_pro_text")

ZOOM_PROMPT = """画像の手を観察する。親指と、親指以外の指を別々に数える。
手は5本とは限らない。多い・少ないイラストもある。見えたとおりに数え、5本に合わせようとしない。
曲がって重なっている指も、指先（爪がある先端）が別々に見えるなら別の指として数える。
次のJSONだけを返す。
{"thumb_visible": true,
 "other_fingers": [{"tip_box": [ymin, xmin, ymax, xmax], "look": "この指の見た目を一言"}],
 "other_count": 親指以外の指の数,
 "total_count": 親指を含めた指の数}
tip_box は指先を囲む四角で、画像全体を0〜1000とする。"""

COMPARE_PROMPT = """
1枚目が確認する画像、2枚目が正しい参考画像です。
1枚目を2枚目と比べ、1枚目にだけある問題と、2枚目との違いを指摘してください。2枚目と同じ見た目は指摘しない。
特に次も必ず見る。
- 色: 主要な色が参考と違う場合は指摘する。分かる範囲で色コード（例: #FF0000）も書く
- 数: ボタン、紐、リボン、バッジ、アイコン、文字ブロックなど、数えられる要素の個数が違う場合は指摘する
- 衣装: 靴の形や色、ベルトの有無、帽子・手袋・アクセサリー・服の細部など、衣装パーツの違いを指摘する
色、数、衣装、要素の有無、文言、図柄の違いで、上の分類に当てはまらないものは category を「差分」にする。"""


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
        reference = options.get("reference") or ""
        reference_path = Path(reference).expanduser() if reference else None
        if reference_path is not None and not reference_path.is_file():
            emit_error(f"No such reference image: {reference_path}", code="invalid")
            return 1
        result = check(
            image,
            note=options.get("note") or "",
            model=options.get("model") or "",
            reference=reference_path,
        )
    except AuthError as exc:
        emit_error(str(exc) or "サインインが必要です。", code="auth")
        return 2
    except UploadRefused as exc:
        emit_error(str(exc), code="upload")
        return 1
    except GenerationTimeout as exc:
        emit_error(
            f"esora側の生成が{int(WAIT_SECONDS)}秒たっても終わりませんでした（サーバーが混み合っている可能性があります）。"
            f"少し待ってからもう一度チェックしてください。詳細: {exc}",
            code="timeout",
        )
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


def check(image: Path, *, note: str, model: str, reference: Path | None = None) -> dict[str, Any]:
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
        asset_ids = [uploaded["asset_id"]]
        if reference is not None:
            compared = _upload_file(
                api,
                reference,
                _Quiet(),
                name=reference.name,
                tags=["image-check", "reference"],
                kinds={"image"},
            )
            asset_ids.append(compared["asset_id"])
        body_model = choose_body_model(api, chosen)
        record = api.create_generation(
            {
                "type": "text",
                "model": chosen,
                "prompt": build_prompt(note, compared=reference is not None),
                "reference_asset_ids": asset_ids,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_output_tokens": 8192,
                "thinking_level": "high",
                "media_resolution": "high",
            }
        )
        # The body pass looks only at the checked image, so a reference never
        # gets its own hands counted as findings.
        body_record = api.create_generation(
            {
                "type": "text",
                "model": body_model,
                "prompt": build_body_prompt(note),
                "reference_asset_ids": asset_ids[:1],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_output_tokens": 8192,
                "thinking_level": "high",
                "media_resolution": "high",
            }
        )
        record = api.wait_for_generation(record["id"], timeout=WAIT_SECONDS)
        if record.get("status") != "completed":
            raise RuntimeError(record.get("error") or f"Generation {record.get('status')}.")
        summary, findings = parse_result(record.get("result"))

        body_findings: list[dict[str, str]] = []
        body_warning = ""
        try:
            body_record = api.wait_for_generation(body_record["id"], timeout=WAIT_SECONDS)
            if body_record.get("status") != "completed":
                raise RuntimeError(body_record.get("error") or "body check did not finish")
            payload = read_body_payload(body_record.get("result"))
            zooms = zoom_check(api, image, payload, body_model)
            body_findings = parse_body_payload(payload, zooms)
        except (GenerationTimeout, RuntimeError, ValueError) as exc:
            body_warning = f"人体の詳細チェックを完了できませんでした: {exc}"

        if not body_warning:
            findings = merge_findings(findings, body_findings)
        if body_findings and not any(item["category"] != "人体構造" for item in findings):
            summary = f"人体に{len(body_findings)}件の指摘があります。"
        elif body_findings:
            summary = f"{summary}（人体に{len(body_findings)}件）"
        result = {
            "ok": True,
            "summary": summary,
            "findings": findings,
            "model": record.get("model") or chosen,
            "body_model": body_record.get("model") or body_model,
            "generation_id": record.get("id"),
            "compared": reference is not None,
        }
        if body_warning:
            result["warning"] = body_warning
        return result


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


def choose_body_model(api: Api, fallback: str) -> str:
    models = {
        item.get("id")
        for item in api.models(task="text_generate")
        if (item.get("capabilities") or {}).get("supports_reference_images")
    }
    return BODY_MODEL if BODY_MODEL in models else fallback


def build_body_prompt(note: str) -> str:
    extra = note.strip()
    if not extra:
        return BODY_PROMPT
    return BODY_PROMPT + "\n\n特に注意して見る項目:\n" + extra[:2000]


def read_body_payload(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("no body review returned")
    payload = result.get("value") if result.get("type") == "json" else parse_json_text(str(result.get("content") or ""))
    if isinstance(payload, str):
        payload = parse_json_text(payload)
    if not isinstance(payload, dict):
        raise ValueError("body review was not a JSON object")
    return payload


def parse_body_result(result: Any) -> list[dict[str, str]]:
    return parse_body_payload(read_body_payload(result), {})


def valid_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        return None
    return value


def parse_body_payload(payload: dict[str, Any], zooms: dict[int, list[int]]) -> list[dict[str, str]]:
    """Turn the body pass into findings.

    ``zooms`` maps a hand's index to the finger count read from its enlarged
    crop. The enlarged count wins; when the two counts disagree the hand is
    still reported so a human looks at it.
    """
    findings: list[dict[str, str]] = []
    hands = payload.get("hands") or []
    if isinstance(hands, list):
        for index, hand in enumerate(hands):
            if not isinstance(hand, dict):
                continue
            fully_visible = hand.get("fully_visible") is True
            whole = valid_count(hand.get("finger_count")) if fully_visible else None
            votes = zooms.get(index) or []
            zoom, unanimous = decide_count(votes)
            owner = str(hand.get("owner") or "").strip()
            side = str(hand.get("side") or "").strip()
            hand_name = f"{side}手" if side in ("左", "右") else "手"
            label = f"{owner}の{hand_name}" if owner else hand_name
            extra = str(hand.get("detail") or "").strip()
            severity = "high"
            if votes:
                tally = "・".join(f"{value}本" for value in sorted(votes))
                if zoom is None:
                    severity = "medium"
                    detail = f"{label}の指の数が判定できません（拡大確認の結果: {tally}）。目視で確認してください。"
                elif zoom == 5:
                    if unanimous and whole in (None, 5):
                        continue
                    severity = "medium"
                    if whole not in (None, 5):
                        detail = f"{label}の指の数が判定できません（全体では{whole}本、拡大すると5本）。目視で確認してください。"
                    else:
                        detail = f"{label}の指の数が判定できません（拡大確認の結果: {tally}）。目視で確認してください。"
                else:
                    wording = "少ない" if zoom < 5 else "多い"
                    detail = f"{label}の指が{zoom}本に見えます（5本より{wording}）。"
                    if not unanimous:
                        detail += f"拡大確認の結果: {tally}。"
                    if whole is not None and whole != zoom:
                        detail += f"全体では{whole}本に見えました。"
                    elif extra and whole == zoom:
                        detail += extra
                    if not fully_visible:
                        # Curled or overlapping fingers: worth a look, but less certain.
                        severity = "medium"
                        detail += "指が重なって数えにくい手です。"
            else:
                if whole is None or whole == 5:
                    continue
                wording = "少ない" if whole < 5 else "多い"
                detail = f"{label}の指が{whole}本に見えます（5本より{wording}）。" + extra
            findings.append(
                {
                    "category": "人体構造",
                    "severity": severity,
                    "location": str(hand.get("location") or "").strip() or label,
                    "detail": detail,
                    "box": parse_box(hand.get("box")),
                }
            )

    others = payload.get("others") or []
    if isinstance(others, list):
        for entry in others:
            if not isinstance(entry, dict):
                continue
            detail = str(entry.get("detail") or "").strip()
            if not detail:
                continue
            part = str(entry.get("part") or "").strip()
            findings.append(
                {
                    "category": "人体構造",
                    "severity": "high",
                    "location": str(entry.get("location") or "").strip() or part or "画像全体",
                    "detail": f"{part}: {detail}" if part else detail,
                    "box": parse_box(entry.get("box")),
                }
            )
    return findings


def crop_hand(image: Path, box: list[float], out: Path) -> Path | None:
    """Crop around a hand and enlarge it, using macOS ``sips`` (no extra packages)."""
    try:
        info = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        sizes = {}
        for line in info.splitlines():
            key, _, value = line.strip().partition(":")
            if key in ("pixelWidth", "pixelHeight"):
                sizes[key] = int(value.strip())
        width, height = sizes["pixelWidth"], sizes["pixelHeight"]
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return None

    x0, y0, x1, y1 = box
    half_w = max((x1 - x0) * (1 + 2 * ZOOM_PADDING), ZOOM_MIN_SIDE) / 2
    half_h = max((y1 - y0) * (1 + 2 * ZOOM_PADDING), ZOOM_MIN_SIDE) / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = max(0, int((cx - half_w) * width))
    top = max(0, int((cy - half_h) * height))
    right = min(width, int((cx + half_w) * width) + 1)
    bottom = min(height, int((cy + half_h) * height) + 1)
    crop_w, crop_h = right - left, bottom - top
    if crop_w < 24 or crop_h < 24:
        return None

    try:
        subprocess.run(
            ["sips", "-c", str(crop_h), str(crop_w), "--cropOffset", str(top), str(left),
             "-s", "format", "png", str(image), "--out", str(out)],
            capture_output=True,
            timeout=60,
            check=True,
        )
        scale = ZOOM_LONG_SIDE / max(crop_w, crop_h)
        if scale > 1:
            subprocess.run(
                ["sips", "-z", str(round(crop_h * scale)), str(round(crop_w * scale)), str(out)],
                capture_output=True,
                timeout=60,
                check=True,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out.exists() else None


def zoom_check(api: Api, image: Path, payload: dict[str, Any], fallback_model: str) -> dict[int, list[int]]:
    """Count the fingers of each hand again on an enlarged crop, one vote per model.

    Every hand with a box is checked, even one the body pass called "not fully
    visible": that flag flips between runs on curled, overlapping fingers, and
    skipping those hands is exactly how a 4-finger hand slipped through. Any
    failure just drops that vote.
    """
    hands = payload.get("hands")
    if not isinstance(hands, list):
        return {}
    available = {
        item.get("id")
        for item in api.models(task="text_generate")
        if (item.get("capabilities") or {}).get("supports_reference_images")
    }
    models = [name for name in ZOOM_MODELS if name in available] or [fallback_model]

    targets: list[tuple[int, list[float]]] = []
    for index, hand in enumerate(hands):
        if not isinstance(hand, dict):
            continue
        box = parse_box(hand.get("box"))
        if box is not None:
            targets.append((index, box))

    pending: list[tuple[int, str]] = []
    votes: dict[int, list[int]] = {}
    with tempfile.TemporaryDirectory(prefix="hand-zoom-") as tmp:
        for index, box in targets[:ZOOM_MAX_HANDS]:
            try:
                crop = crop_hand(image, box, Path(tmp) / f"hand{index}.png")
                if crop is None:
                    continue
                uploaded = _upload_file(
                    api, crop, _Quiet(), name=f"hand{index}.png", tags=["image-check", "zoom"], kinds={"image"}
                )
            except (ApiError, UploadRefused, GenerationTimeout, RuntimeError, OSError, KeyError):
                continue
            for model in models:
                request = {
                    "type": "text",
                    "model": model,
                    "prompt": ZOOM_PROMPT,
                    "reference_asset_ids": [uploaded["asset_id"]],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                    "max_output_tokens": 4096,
                    "thinking_level": "high",
                    "media_resolution": "high",
                }
                try:
                    try:
                        record = api.create_generation(request)
                    except ApiError:
                        # Some models reject a temperature; retry with defaults.
                        request.pop("temperature")
                        record = api.create_generation(request)
                    pending.append((index, record["id"]))
                except (ApiError, KeyError):
                    continue
        # The zoom votes are a refinement: one stuck generation must not hold
        # the whole review, so they share a single time budget.
        deadline = time.monotonic() + ZOOM_WAIT_SECONDS
        for index, generation_id in pending:
            try:
                remaining = max(deadline - time.monotonic(), 1.0)
                record = api.wait_for_generation(generation_id, timeout=remaining)
                if record.get("status") != "completed":
                    continue
                result = record.get("result") or {}
                value = result.get("value") if result.get("type") == "json" else parse_json_text(str(result.get("content") or ""))
                if isinstance(value, dict):
                    count = valid_count(value.get("total_count"))
                    if count is not None:
                        votes.setdefault(index, []).append(count)
            except (ApiError, GenerationTimeout, RuntimeError, ValueError):
                continue
    return votes


def decide_count(votes: list[int]) -> tuple[int | None, bool]:
    """Return (count, unanimous). A strict majority wins; otherwise no count."""
    if not votes:
        return None, False
    best = max(set(votes), key=lambda value: (votes.count(value), value != 5))
    if votes.count(best) * 2 > len(votes):
        return best, votes.count(best) == len(votes)
    return None, False


def parse_box(value: Any) -> list[float] | None:
    """[ymin, xmin, ymax, xmax] (0-1000, or 0-1) -> [x0, y0, x1, y1] as 0-1 fractions."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    ymin, xmin, ymax, xmax = (float(item) for item in value)
    scale = 1.0 if max(ymin, xmin, ymax, xmax) <= 1.0 else 1000.0
    x0, x1 = sorted((min(max(xmin / scale, 0.0), 1.0), min(max(xmax / scale, 0.0), 1.0)))
    y0, y1 = sorted((min(max(ymin / scale, 0.0), 1.0), min(max(ymax / scale, 0.0), 1.0)))
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def merge_findings(general: list[dict[str, str]], body: list[dict[str, str]]) -> list[dict[str, str]]:
    # The body pass is the authority on anatomy. Keep the general pass for
    # everything else so the same hand is not reported twice.
    kept = [item for item in general if item["category"] != "人体構造"]
    return body + kept


def build_prompt(note: str, *, compared: bool = False) -> str:
    parts = [PROMPT]
    if compared:
        parts.append(COMPARE_PROMPT.strip())
    extra = note.strip()
    if extra:
        parts.append(
            "追加で確認する項目:\n"
            + extra[:2000]
            + "\nこの項目は特に細かく見て、問題があれば必ず指摘する。服の色・装飾デザインは差分、パースの崩れはレイアウトか差分にする。"
        )
    return "\n\n".join(parts)


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
    aliases = {
        "人体": "人体構造",
        "人体的構造": "人体構造",
        "解剖": "人体構造",
        "AI": "AIっぽさ",
        "AI感": "AIっぽさ",
        "生成AI": "AIっぽさ",
    }
    category = aliases.get(category, category)
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
        "box": parse_box(entry.get("box")),
    }


def emit_error(message: str, *, code: str) -> None:
    json.dump({"ok": False, "error": message, "code": code}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)

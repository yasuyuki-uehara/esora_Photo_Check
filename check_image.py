"""Send one image to Esora and return visual QA findings as JSON.

Run with the esora-api CLI's Python so the installed client and keychain
session are reused:

    esora-python check_image.py image.png <<'EOF'
    {"note": "正しい表記", "model": "gemini_3_5_flash_text"}
    EOF
"""

from __future__ import annotations

import difflib
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

CATEGORIES = ("文字切れ", "誤字", "余白", "レイアウト", "人体構造", "衣装・質感", "パース", "AIっぽさ", "差分")
SEVERITIES = ("high", "medium", "low")

PROMPT = """あなたは日本語のWeb画像（バナー、LP、告知、UIパーツ、イラスト）の校正者です。渡された参照画像を端から端まで丁寧に見て、おかしい点を漏れなくすべて挙げてください。
重要な問題だけに絞らない。小さな問題、軽微な問題、確信が持てない問題も、見つけたものはすべて findings に入れる。迷ったら出す。重要度は severity で表す（軽微なら low、確信が持てなければ low か medium）。
一つの箇所に複数の問題があるときは、問題ごとに別の項目にする。他の人が見落としそうな細部ほど、念入りに探す。次の種類で見る。

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
- 衣装・質感: 服・靴・小物の細部のおかしさ。服の前合わせ、縫い目、ライン、柄、ボタンの位置や形が途中で途切れる・ずれる・消える。シワや折り返しが不自然（角ばっている、流れが合わない）。影や光の向きが揃っていない。布・革・金属などの質感が混ざっている。靴や足首の角度、足の向き、左右の形の違い。小物や持ち物の形・数の不整合
- パース: 遠近、奥行き、角度、大きさの比率、床や壁との接地、体と衣装・小物の前後関係のおかしさ
- AIっぽさ: 生成AI特有の破綻。ぼやけた細部、溶けた指や手足、記憶にない文字、対称の崩れ、質感の不自然な混ざり、境界の溶け、輪郭線の太さの不揃い、色のはみ出し、ノイズや斑点など

見つけた問題は、軽微でも出す。デザインの好みや改善案は書かない（「もっとこうしたほうがよい」は不要）。画像から読み取れる事実だけを書く。まったく同じ箇所の同じ問題を繰り返さない。問題が本当に何もないときだけ findings は空配列にする。
文字を斜めに配置した、文字を重ねた、画面端まで枠を伸ばしたなどが、明らかに装飾として組まれているときは、それだけでは指摘しない。ただし、文字が読めない、一部が欠けている、誤字、不自然な空白は指摘する。
人物や体の一部が写っていないとき、または記号的に簡略化された絵のときは、構造を判断できる範囲だけ見る。写っている手・指・肌・髪・顔・足は、一つずつ細かく確認する。判断に迷う箇所は、severity を low にして出す。

次の形のJSONオブジェクトだけを返す。
{
  "summary": "全体を一文で",
  "findings": [
    {
      "category": "文字切れ" または "誤字" または "余白" または "レイアウト" または "人体構造" または "衣装・質感" または "パース" または "AIっぽさ" または "差分",
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

others は、腕や脚の本数、関節の向き、足首や靴の角度、顔のパーツの数と位置、耳、髪の根元や生え方、肌の色むら、体の比率などの異常を、小さなものも含めてすべて入れる。迷うものも入れる。異常が本当にないときだけ空配列にする。"""

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

# Colour comparison against a reference. A vision model cannot read colour
# values reliably (it happily answers the same hex for a visibly darker jacket),
# so it only says WHERE each part is. The colours themselves are measured from
# the real pixels of both images here, and compared as numbers.
COLOR_DELTA_MEDIUM = 7.0
COLOR_DELTA_HIGH = 18.0
# When pixels cannot be measured, fall back to the model's own hex guesses,
# which are much coarser, so only larger differences count.
COLOR_GUESS_MEDIUM = 10.0
COLOR_GUESS_HIGH = 22.0
COLOR_MAX_AREAS = 24
COLOR_SAMPLE_SIDE = 24

COLOR_PROMPT = """1枚目が確認する画像、2枚目が正しい参考画像です。2枚の色を部位ごとに比べるため、部位の位置を教えてください。
画像に見える色のまとまりを、小さなものも含めて漏れなく挙げる。例: 髪、肌、目、唇、上着、シャツ、ズボン・スカート、靴、靴下、ベルト、手袋、帽子、ボタン、ライン・縁取り、リボン・紐、アクセサリー、小物、背景、文字、ロゴ。
2枚目にない部位は挙げない。1枚目にない部位は checked_sample を null にする。

各部位について、次を答える。
- box: 1枚目の上でその部位全体を囲む四角
- checked_sample: 1枚目の、その部位の色が均一な面だけを囲む小さな四角。影、ハイライト、線、他の部位、背景を含めない
- reference_sample: 2枚目の、同じ部位の色が均一な面だけを囲む小さな四角。同じく影、線、他の部位、背景を含めない
- checked_hex と reference_hex: 見た目の色コードの推測。色が違うと思い込まず、見えた色をそのまま書く

次の形のJSONオブジェクトだけを返す。
{
  "areas": [
    {
      "part": "部位の名前",
      "box": [ymin, xmin, ymax, xmax],
      "checked_sample": [ymin, xmin, ymax, xmax],
      "reference_sample": [ymin, xmin, ymax, xmax],
      "checked_hex": "#RRGGBB",
      "reference_hex": "#RRGGBB"
    }
  ]
}
四角はすべて、その画像全体を 0〜1000 とした整数 [ymin, xmin, ymax, xmax] にする。位置を特定できないときは null にする。"""

# Where each body part / costume part is. Used only to place the reviewer's
# own check marks on the image; it judges nothing.
REGION_KINDS = ("person", "face", "eyes", "hair", "hands", "arms", "legs", "clothes", "accessories", "structures")
REGION_MAX = 40

LOCATE_PROMPT = """画像に写っているものの位置を調べる。良し悪しの判断はしない。位置だけを返す。
次の種類ごとに、写っているものを1つずつ別々の項目にする。写っていない種類は挙げない。複数のキャラクターがいるときは、それぞれ別に挙げる。
- person: 人物・キャラクター1人の全身（見えている範囲）
- face: 顔
- eyes: 目（左右それぞれ別々に）
- hair: 髪全体
- hands: 手（左右それぞれ別々に。指全体を含める）
- arms: 腕（左右それぞれ別々に。手は含めない）
- legs: 脚・足（左右それぞれ別々に。靴は含めない）
- clothes: 服・靴など身につけているもの。上着、ズボン・スカート、靴など、パーツごとに別々に
- accessories: アクセサリー、ベルト、持ち物、小物
- structures: 背景にある建物・家具・乗り物・道具などの構造物

次の形のJSONオブジェクトだけを返す。
{
  "regions": [
    {"kind": "上の種類のどれか", "label": "短い名前（例: 右手、髪、上着、左目）。10文字以内", "box": [ymin, xmin, ymax, xmax]}
  ]
}
box は、そのものだけをぴったり囲む四角で、画像全体を 0〜1000 とした整数 [ymin, xmin, ymax, xmax] にする。"""

COMPARE_PROMPT = """
1枚目が確認する画像、2枚目が正しい参考画像です。
1枚目を2枚目と比べ、1枚目にだけある問題と、2枚目との違いを指摘してください。2枚目と同じ見た目は指摘しない。
特に次も必ず見る。
- 色: 髪、肌、服、靴、装飾、ライン、背景など、部位ごとに参考と並べて比べる。少しの色味の違い（赤みが強い、暗い、くすんでいる、彩度が違うなど）も指摘する。分かる範囲で色コード（例: #FF0000）も書く
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
        locate_record = None
        try:
            locate_record = api.create_generation(
                {
                    "type": "text",
                    "model": body_model,
                    "prompt": LOCATE_PROMPT,
                    "reference_asset_ids": asset_ids[:1],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                    "max_output_tokens": 8192,
                    "thinking_level": "medium",
                    "media_resolution": "high",
                }
            )
        except ApiError:
            locate_record = None

        # A second, independent sweep with a different model: any single run
        # misses things the other catches, so the union finds more.
        second_model = body_model if body_model != chosen else (DEFAULT_MODEL if DEFAULT_MODEL != chosen else chosen)
        second_record = None
        try:
            second_record = api.create_generation(
                {
                    "type": "text",
                    "model": second_model,
                    "prompt": build_prompt(note, compared=reference is not None),
                    "reference_asset_ids": asset_ids,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.6,
                    "max_output_tokens": 8192,
                    "thinking_level": "high",
                    "media_resolution": "high",
                }
            )
        except ApiError:
            second_record = None

        color_record = None
        if reference is not None:
            color_record = api.create_generation(
                {
                    "type": "text",
                    "model": body_model,
                    "prompt": COLOR_PROMPT,
                    "reference_asset_ids": asset_ids,
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
        if second_record is not None:
            try:
                second_record = api.wait_for_generation(second_record["id"], timeout=WAIT_SECONDS)
                if second_record.get("status") == "completed":
                    _, extra_findings = parse_result(second_record.get("result"))
                    findings = union_findings(findings, extra_findings)
            except (GenerationTimeout, RuntimeError, ValueError, ApiError):
                pass  # the first sweep alone is still a complete review

        color_findings: list[dict[str, Any]] = []
        color_warning = ""
        if color_record is not None:
            try:
                color_record = api.wait_for_generation(color_record["id"], timeout=ZOOM_WAIT_SECONDS + 120)
                if color_record.get("status") != "completed":
                    raise RuntimeError(color_record.get("error") or "color check did not finish")
                color_findings = parse_color_result(color_record.get("result"), image, reference)
                findings = [
                    item
                    for item in findings
                    if not (item["category"] == "差分" and ("色" in item["detail"] or "#" in item["detail"]))
                ]
                findings = color_findings + findings
            except (GenerationTimeout, RuntimeError, ValueError) as exc:
                color_warning = f"色の詳細チェックを完了できませんでした: {exc}"

        body_findings: list[dict[str, str]] = []
        body_warning = ""
        body_payload: dict[str, Any] = {}
        try:
            body_record = api.wait_for_generation(body_record["id"], timeout=WAIT_SECONDS)
            if body_record.get("status") != "completed":
                raise RuntimeError(body_record.get("error") or "body check did not finish")
            payload = read_body_payload(body_record.get("result"))
            body_payload = payload
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
        if color_findings:
            summary = f"{summary}（参考画像と色が違う部位が{len(color_findings)}件）"

        regions: list[dict[str, Any]] = []
        if locate_record is not None:
            try:
                locate_record = api.wait_for_generation(locate_record["id"], timeout=ZOOM_WAIT_SECONDS + 60)
                if locate_record.get("status") == "completed":
                    regions = parse_locate_result(locate_record.get("result"))
            except (GenerationTimeout, RuntimeError, ValueError, ApiError):
                regions = []
        if not any(region["kind"] == "hands" for region in regions):
            regions += hand_regions(body_payload)

        result = {
            "ok": True,
            "summary": summary,
            "findings": findings,
            "regions": regions,
            "model": record.get("model") or chosen,
            "body_model": body_record.get("model") or body_model,
            "generation_id": record.get("id"),
            "compared": reference is not None,
        }
        warnings = [text for text in (body_warning, color_warning) if text]
        if warnings:
            result["warning"] = " / ".join(warnings)
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


def parse_hex(value: Any) -> tuple[int, int, int] | None:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_delta(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """CIE76 colour difference. Roughly: ~2 is invisible, ~10 is noticeable side by side."""
    la, lb = to_lab(a), to_lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def describe_shift(checked: tuple[int, int, int], reference: tuple[int, int, int]) -> str:
    """A short Japanese hint about how the checked colour moved away from the reference."""
    lc, ac, bc = to_lab(checked)
    lr, ar, br = to_lab(reference)
    hints = []
    if abs(lc - lr) >= 6:
        hints.append("明るい" if lc > lr else "暗い")
    if abs(ac - ar) >= 6:
        hints.append("赤みが強い" if ac > ar else "緑みが強い")
    if abs(bc - br) >= 6:
        hints.append("黄みが強い" if bc > br else "青みが強い")
    chroma_c = (ac * ac + bc * bc) ** 0.5
    chroma_r = (ar * ar + br * br) ** 0.5
    if abs(chroma_c - chroma_r) >= 8 and not hints:
        hints.append("鮮やか" if chroma_c > chroma_r else "くすんでいる")
    return "・".join(hints)


def sample_color(image: Path, box: list[float] | None, workdir: Path, name: str) -> tuple[int, int, int] | None:
    """Median colour of the real pixels inside ``box`` (0-1 fractions), via macOS ``sips``."""
    if box is None:
        return None
    try:
        info = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        sizes = {}
        for line in info.splitlines():
            key, _, value = line.strip().partition(":")
            if key in ("pixelWidth", "pixelHeight"):
                sizes[key] = int(value.strip())
        width, height = sizes["pixelWidth"], sizes["pixelHeight"]
        x0, y0, x1, y1 = box
        left, top = int(x0 * width), int(y0 * height)
        crop_w = max(1, min(width, int(x1 * width) + 1) - left)
        crop_h = max(1, min(height, int(y1 * height) + 1) - top)
        cropped, small = workdir / f"{name}-crop.png", workdir / f"{name}.bmp"
        subprocess.run(
            ["sips", "-c", str(crop_h), str(crop_w), "--cropOffset", str(top), str(left),
             "-s", "format", "png", str(image), "--out", str(cropped)],
            capture_output=True, timeout=60, check=True,
        )
        side_w, side_h = min(COLOR_SAMPLE_SIDE, crop_w), min(COLOR_SAMPLE_SIDE, crop_h)
        subprocess.run(
            ["sips", "-z", str(side_h), str(side_w), "-s", "format", "bmp", str(cropped), "--out", str(small)],
            capture_output=True, timeout=60, check=True,
        )
        return median_bmp_color(small.read_bytes())
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return None


def median_bmp_color(data: bytes) -> tuple[int, int, int] | None:
    """Per-channel median of an uncompressed 24/32-bit BMP (what sips writes)."""
    if len(data) < 54 or data[:2] != b"BM":
        return None
    offset = int.from_bytes(data[10:14], "little")
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = abs(int.from_bytes(data[22:26], "little", signed=True))
    bits = int.from_bytes(data[28:30], "little")
    if bits not in (24, 32) or int.from_bytes(data[30:34], "little") not in (0, 3) or width <= 0 or height <= 0:
        return None
    step = bits // 8
    stride = ((bits * width + 31) // 32) * 4
    reds: list[int] = []
    greens: list[int] = []
    blues: list[int] = []
    for row in range(height):
        base = offset + row * stride
        for col in range(width):
            pixel = base + col * step
            if pixel + 2 >= len(data):
                continue
            blues.append(data[pixel])
            greens.append(data[pixel + 1])
            reds.append(data[pixel + 2])
    if not reds:
        return None
    middle = len(reds) // 2
    return sorted(reds)[middle], sorted(greens)[middle], sorted(blues)[middle]


def parse_color_result(result: Any, image: Path | None = None, reference: Path | None = None) -> list[dict[str, Any]]:
    payload = read_body_payload(result)
    areas = payload.get("areas") or []
    if not isinstance(areas, list):
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    with tempfile.TemporaryDirectory(prefix="color-sample-") as tmp:
        for index, area in enumerate(areas[:COLOR_MAX_AREAS]):
            if not isinstance(area, dict):
                continue
            checked = reference_color = None
            measured = False
            if image is not None and reference is not None:
                checked = sample_color(image, parse_box(area.get("checked_sample")), Path(tmp), f"c{index}")
                reference_color = sample_color(reference, parse_box(area.get("reference_sample")), Path(tmp), f"r{index}")
                measured = checked is not None and reference_color is not None
            if not measured:
                checked = parse_hex(area.get("checked_hex"))
                reference_color = parse_hex(area.get("reference_hex"))
            if checked is None or reference_color is None:
                continue
            medium, high = (COLOR_DELTA_MEDIUM, COLOR_DELTA_HIGH) if measured else (COLOR_GUESS_MEDIUM, COLOR_GUESS_HIGH)
            delta = color_delta(checked, reference_color)
            if delta < medium:
                continue
            part = str(area.get("part") or "").strip() or "色"
            shift = describe_shift(checked, reference_color)
            detail = (
                f"{part}の色が参考と違います"
                f"（確認 #{checked[0]:02X}{checked[1]:02X}{checked[2]:02X} / "
                f"参考 #{reference_color[0]:02X}{reference_color[1]:02X}{reference_color[2]:02X}）"
            )
            detail += f"。{shift}。" if shift else "。"
            scored.append(
                (
                    delta,
                    {
                        "category": "差分",
                        "severity": "high" if delta >= high else "medium",
                        "location": part,
                        "detail": detail,
                        "box": parse_box(area.get("box")) or parse_box(area.get("checked_sample")),
                    },
                )
            )
    scored.sort(key=lambda item: -item[0])
    return [finding for _, finding in scored]


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


def parse_locate_result(result: Any) -> list[dict[str, Any]]:
    payload = read_body_payload(result)
    entries = payload.get("regions") or []
    regions: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return regions
    for entry in entries[:REGION_MAX]:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip()
        box = parse_box(entry.get("box"))
        if kind not in REGION_KINDS or box is None:
            continue
        label = str(entry.get("label") or "").strip()[:12]
        regions.append({"kind": kind, "label": label, "box": box})
    return regions


def hand_regions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Hand boxes from the body pass, used when the locate pass gave none."""
    regions: list[dict[str, Any]] = []
    hands = payload.get("hands") if isinstance(payload, dict) else None
    for hand in hands if isinstance(hands, list) else []:
        box = parse_box(hand.get("box")) if isinstance(hand, dict) else None
        if box is None:
            continue
        side = str(hand.get("side") or "").strip()
        regions.append({"kind": "hands", "label": f"{side}手" if side in ("左", "右") else "手", "box": box})
    return regions


def box_overlap(a: list[float] | None, b: list[float] | None) -> float:
    """Intersection over the smaller box, so a tight box inside a loose one counts as the same spot."""
    if not a or not b:
        return 0.0
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (width * height) / smaller if smaller > 0 else 0.0


def same_finding(a: dict[str, Any], b: dict[str, Any]) -> bool:
    similarity = difflib.SequenceMatcher(None, a["detail"], b["detail"]).ratio()
    if similarity >= 0.7:
        return True
    if a["category"] == b["category"] and box_overlap(a.get("box"), b.get("box")) >= 0.6 and similarity >= 0.3:
        return True
    return False


def union_findings(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(first)
    for item in second:
        if not any(same_finding(item, existing) for existing in merged):
            merged.append(item)
    return merged


def merge_findings(general: list[dict[str, str]], body: list[dict[str, str]]) -> list[dict[str, str]]:
    # The body pass is the authority on finger counts (it re-counts on enlarged
    # crops), so the general pass's finger counts are dropped. Everything else
    # the general pass found about the body is kept unless it repeats a body
    # finding, so nothing the general pass saw is lost.
    kept = []
    for item in general:
        if item["category"] == "人体構造":
            detail = item["detail"]
            if "指" in detail and ("本" in detail or "数" in detail):
                continue
        # The body pass can also report costume or shape problems under its own
        # category, so a repeat is dropped whatever category it was filed under.
        if any(same_finding(item, other) for other in body):
            continue
        kept.append(item)
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
        "衣装": "衣装・質感",
        "質感": "衣装・質感",
        "服": "衣装・質感",
        "服装": "衣装・質感",
        "遠近": "パース",
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

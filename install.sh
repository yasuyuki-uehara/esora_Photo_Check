#!/bin/bash
# AICKtool のインストール（1行で入ります）
#
# ターミナルに次を貼り付けて Enter:
#   curl -fsSL https://raw.githubusercontent.com/yasuyuki-uehara/esora_Photo_Check/main/install.sh | bash
#
# curl で落としたファイルには、ブラウザ経由のような「確認できていない」印が付かないため、
# インストール後は、警告なしでアイコンをダブルクリックして開けます。

set -e

ZIP_URL="https://github.com/yasuyuki-uehara/esora_Photo_Check/archive/refs/heads/main.zip"
DEST_DIR="${AICKTOOL_DEST:-$HOME/Applications}"
APP="$DEST_DIR/AICKtool.app"

echo "=== AICKtool をインストールします ==="
echo ""

WORK="$(mktemp -d "${TMPDIR:-/tmp}/aicktool-install.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "ダウンロードしています…"
if ! curl -fsSL "$ZIP_URL" -o "$WORK/aicktool.zip"; then
  echo ""
  echo "ダウンロードできませんでした。インターネットの接続を確認して、もう一度お試しください。"
  exit 1
fi

# 日本語のファイル名があるので、unzip ではなく macOS 標準の ditto で解凍する。
mkdir -p "$WORK/src"
ditto -x -k "$WORK/aicktool.zip" "$WORK/src"
SRC="$(find "$WORK/src" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ ! -d "$SRC/Contents" ]; then
  echo "ダウンロードしたファイルの中に Contents が見つかりませんでした。"
  exit 1
fi

# 起動中なら、いったん閉じてから入れ替える（更新のとき）。
if curl -fs -m 2 "http://127.0.0.1:${ESORA_CHECK_PORT:-8765}/api/status" >/dev/null 2>&1; then
  pkill -f "Resources/app/server.py" 2>/dev/null || true
  sleep 1
fi

mkdir -p "$DEST_DIR"
rm -rf "$APP"
mkdir -p "$APP"
cp -R "$SRC/Contents" "$APP/Contents"

# 念のため、印を外して実行できるようにする。
xattr -cr "$APP" 2>/dev/null || true
chmod +x "$APP/Contents/MacOS/AICKtool" "$APP/Contents/Resources/launch.sh" 2>/dev/null || true
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
# アイコンを更新して見せる。
touch "$APP"

echo ""
echo "インストールできました: $APP"
echo ""
echo "次回からは、次のどちらかで開けます。"
echo "  ・Command + スペース を押して「AICKtool」と入力し、Enter"
echo "  ・Launchpad（ロケットのアイコン）の「AICKtool」をクリック"
echo ""

if [ -n "${AICKTOOL_NO_LAUNCH:-}" ]; then
  exit 0
fi

# Finder でアイコンの場所を見せる（デスクトップや Dock にドラッグしておくと便利）。
open -R "$APP" >/dev/null 2>&1 || true

echo "このまま起動します。"
echo ""
exec /bin/bash "$APP/Contents/Resources/launch.sh"

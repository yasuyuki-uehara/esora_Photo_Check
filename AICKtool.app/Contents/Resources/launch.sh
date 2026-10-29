#!/bin/bash
# AI生成画像基本チェック: AICKtool.app から、ターミナルで呼ばれる起動処理。
# 初回だけ、必要なものが足りなければインストールの確認が出ます。

# プログラム本体はアプリの中（Resources/app）にある。
cd "$(dirname "$0")/app" || exit 1
# アプリの中は書き込めないことがあるので、.pyc は作らない。
export PYTHONDONTWRITEBYTECODE=1

PORT="${ESORA_CHECK_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"
# esora の CLI を配布している社内のパッケージ置き場（社内ネットワークか VPN が必要）
ESORA_INDEX="http://192.168.92.3:30503/simple/"

# ダブルクリック起動では PATH が少ないので、よく使う場所を足す。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

say() { printf '%s\n' "$*"; }

stop_here() {
  say ""
  read -r -p "Enter キーを押すと、このウィンドウを閉じます。" _
  exit "${1:-1}"
}

ask() {
  local answer
  read -r -p "$1 [Y/n] " answer
  case "$answer" in "" | [Yy]*) return 0 ;; *) return 1 ;; esac
}

open_browser() {
  [ -n "${ESORA_CHECK_NO_BROWSER:-}" ] && return 0
  open "$URL"
}

say "=== AI生成画像基本チェック ==="
say ""

# 1. すでに起動していれば、ブラウザを開くだけ。
if curl -fs -m 2 "${URL}api/status" >/dev/null 2>&1; then
  say "すでに起動しています。ブラウザを開きます: $URL"
  open_browser
  exit 0
fi

# 2. python3（Mac に入っているもの）。
if ! python3 -c 'import sys' >/dev/null 2>&1; then
  say "python3 を使えませんでした。"
  say "「コマンドライン デベロッパ ツール」のインストール画面が出たときは、"
  say "インストールを終えてから、もう一度 AICKtool を開いてください。"
  stop_here 1
fi

# 3. esora-api（esora の CLI）。無ければ入れる。
if ! command -v esora-api >/dev/null 2>&1; then
  say "初回セットアップ: esora の CLI（esora-api）が必要です。"
  say "社内ネットワークか VPN につないでから進めてください。"
  say ""
  ask "esora-api をインストールしますか？" || stop_here 1

  if ! command -v uv >/dev/null 2>&1; then
    say ""
    say "先に、インストール用のツール uv を入れます（https://astral.sh/uv）。"
    if ! curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh; then
      say "uv をインストールできませんでした。インターネットの接続を確認してください。"
      stop_here 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
  fi

  say ""
  # esora-api は Python 3.12 以上が必要。Mac 標準の Python は古いので、uv に用意させる。
  if ! uv tool install --python 3.12 --index "$ESORA_INDEX" esora-api-cli; then
    say ""
    say "esora-api をインストールできませんでした。上のエラーを確認してください。"
    say "- 社内ネットワークか VPN につながっていますか？"
    say "- インターネットにつながっていますか？（Python の取得に使います）"
    say "直したら、もう一度 AICKtool を開いてください。"
    stop_here 1
  fi
  say ""
fi

# 4. 起動。サーバーが応答したらブラウザを開く。
say "起動します: $URL"
say "（使い終わったら、このウィンドウを閉じるか Control + C で終了します）"
say ""
(
  for _ in $(seq 1 30); do
    if curl -fs -m 1 "${URL}api/status" >/dev/null 2>&1; then
      open_browser
      exit 0
    fi
    sleep 0.5
  done
) &

python3 server.py
status=$?
if [ "$status" -ne 0 ]; then
  say ""
  say "起動できませんでした（終了コード $status）。上のメッセージを確認してください。"
  stop_here "$status"
fi

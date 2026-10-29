#!/bin/bash
# AICKtool の起動ファイル。これを開いて使います。

cd "$(dirname "$0")" || exit 1

LAUNCH="Contents/Resources/launch.sh"
if [ ! -f "$LAUNCH" ]; then
  echo "「Contents/Resources/launch.sh」が見つかりません。"
  echo "このファイルは、解凍したフォルダの中で開いてください。"
  read -r -p "Enter キーで閉じます。" _
  exit 1
fi

# GitHub の ZIP などから落としたときに付く「確認できていない」印を外す。
# （このファイル自体が印付きのときは、ダブルクリックではここまで来ません。
#   その場合は「はじめに.txt」の手順へ）
xattr -dr com.apple.quarantine Contents 2>/dev/null || true
xattr -dr com.apple.quarantine "$0" 2>/dev/null || true

exec /bin/bash "$LAUNCH"

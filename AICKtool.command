#!/bin/bash
# AICKtool の起動ファイル。これを開いて使います。
# （本体のアプリは「本体」フォルダの中にあります）

cd "$(dirname "$0")" || exit 1

APP="本体/AICKtool.app"
if [ ! -d "$APP" ]; then
  echo "「本体/AICKtool.app」が見つかりません。"
  echo "このファイルは、解凍したフォルダの中で開いてください。"
  read -r -p "Enter キーで閉じます。" _
  exit 1
fi

# GitHub の ZIP などから落としたときに付く「確認できていない」印を外す。
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
xattr -dr com.apple.quarantine "$0" 2>/dev/null || true

exec /bin/bash "$APP/Contents/Resources/launch.sh"

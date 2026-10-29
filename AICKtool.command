#!/bin/bash
# AICKtool の起動ファイル。これを開いて使います。

cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"

LAUNCH="Contents/Resources/launch.sh"
ICON="Contents/Resources/AICKtool.icns"

if [ ! -f "$LAUNCH" ]; then
  echo "「Contents/Resources/launch.sh」が見つかりません。"
  echo "このファイルは、解凍したフォルダの中で開いてください。"
  read -r -p "Enter キーで閉じます。" _
  exit 1
fi

# GitHub の ZIP などから落としたときに付く「確認できていない」印を外す。
xattr -dr com.apple.quarantine Contents 2>/dev/null || true
xattr -dr com.apple.quarantine "$0" 2>/dev/null || true

# ZIP 経由だとカスタムアイコンが消えることがあるので、無ければ付け直す。
if [ -f "$ICON" ] && ! xattr -p com.apple.ResourceFork "$HERE/AICKtool.command" >/dev/null 2>&1; then
  /usr/bin/swift -e "
import AppKit
let icon = \"${HERE}/${ICON}\"
let target = \"${HERE}/AICKtool.command\"
if let image = NSImage(contentsOfFile: icon) {
  NSWorkspace.shared.setIcon(image, forFile: target, options: [])
}
" >/dev/null 2>&1 || true
fi

exec /bin/bash "$LAUNCH"

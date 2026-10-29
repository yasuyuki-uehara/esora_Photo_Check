#!/bin/bash
# AICKtool.app が開けないときの予備の起動ファイル。
cd "$(dirname "$0")" || exit 1
exec /bin/bash "AICKtool.app/Contents/Resources/launch.sh"

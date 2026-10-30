#!/bin/zsh
# 构建 Presence.app（菜单栏小工具）
#   scripts/build-app.sh             构建到 build/Presence.app
#   scripts/build-app.sh --install   另外复制到 ~/Applications 并打开
# 签名：默认用临时签名（ad-hoc）。设置 PRESENCE_SIGN_IDENTITY 为钥匙串里的证书名可改用固定签名，
# 这样重新构建后不用重新授予辅助功能等权限。
set -euo pipefail
cd "${0:A:h}/.."

swift build -c release --product PresenceAgent
bin="$(swift build -c release --show-bin-path)/PresenceAgent"

app=build/Presence.app
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp "$bin" "$app/Contents/MacOS/PresenceAgent"
# SwiftPM 把二进制里的 SDK 版本写成最低系统版本（14.0），系统会按老 App 的兼容模式显示，
# 用不上新系统的界面样式。这里改成实际编译用的 SDK 版本。
vtool -set-build-version macos 14.0 "$(xcrun --show-sdk-version)" -replace \
  -output "$app/Contents/MacOS/PresenceAgent" "$app/Contents/MacOS/PresenceAgent"
cp Resources/Info.plist "$app/Contents/Info.plist"
cp Resources/AppIcon.icns "$app/Contents/Resources/AppIcon.icns"
codesign --force --sign "${PRESENCE_SIGN_IDENTITY:--}" --identifier ai.ignat.presence.agent "$app"
codesign --verify --strict "$app"
echo "已构建 $app"

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p ~/Applications
  osascript -e 'tell application id "ai.ignat.presence.agent" to quit' 2>/dev/null || true
  rm -rf ~/Applications/Presence.app
  cp -R "$app" ~/Applications/
  open ~/Applications/Presence.app
  echo "已安装到 ~/Applications/Presence.app 并启动"
fi

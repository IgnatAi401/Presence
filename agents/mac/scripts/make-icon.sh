#!/bin/zsh
# 由 Resources/AppIcon-source.png（方形原图）生成 Resources/AppIcon.icns：
# 按 macOS 图标网格裁成圆角方块（1024 画布中 824×824、圆角 185），四周留透明边。
# 换了原图后运行一次，结果提交到 Git。
set -euo pipefail
cd "${0:A:h}/.."

tmp="$(mktemp -d)"
set_dir="$tmp/AppIcon.iconset"
mkdir -p "$set_dir"
magick -size 824x824 xc:black -fill white -draw "roundrectangle 0,0 823,823 185,185" -alpha off "$tmp/mask.png"
magick Resources/AppIcon-source.png -resize 824x824^ -gravity center -extent 824x824 -alpha off \
  "$tmp/mask.png" -compose CopyOpacity -composite \
  -background none -compose Over -gravity center -extent 1024x1024 "$tmp/icon.png"
for size in 16 32 128 256 512; do
  magick "$tmp/icon.png" -resize ${size}x${size} "$set_dir/icon_${size}x${size}.png"
  magick "$tmp/icon.png" -resize $((size * 2))x$((size * 2)) "$set_dir/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$set_dir" -o Resources/AppIcon.icns
rm -rf "$tmp"
echo "已生成 Resources/AppIcon.icns"

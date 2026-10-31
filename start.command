#!/bin/bash
# Double-click entry for macOS. First time after cloning, run:
# chmod +x start.command
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  python3 start.py "$@"
elif command -v python >/dev/null 2>&1; then
  python start.py "$@"
else
  echo "未找到 Python。请安装 Python 3.11+，再重新启动 KnowTrace。"
  read -r -p "按回车关闭窗口..." _
  exit 1
fi
status=$?
if [ "$status" -ne 0 ]; then
  echo
  read -r -p "启动失败。按回车关闭窗口..." _
fi
exit "$status"

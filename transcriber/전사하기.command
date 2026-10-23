#!/bin/bash
# Mac용. 더블클릭하면 창 프로그램이 열립니다. 터미널에서 파일 경로를 붙이면 창 없이 바로 전사합니다.
# 실행 권한 오류가 나면 터미널에서 한 번:  chmod +x 전사하기.command
cd "$(dirname "$0")"
if [ -f ".venv/installed.ok" ] && ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
  echo "[준비] 파이썬이 바뀌어 다시 설치합니다."; rm -f .venv/installed.ok
fi
if [ ! -f ".venv/installed.ok" ]; then
  echo "[준비] 처음 한 번만 파이썬 가상환경과 패키지를 설치합니다. 몇 분 걸립니다."
  rm -rf .venv
  python3 -m venv .venv || { echo "python3가 없습니다. https://www.python.org/downloads/ 에서 설치하세요."; read -p "Enter를 누르면 닫힙니다."; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt || { echo "패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요."; rm -rf .venv; read -p "Enter를 누르면 닫힙니다."; exit 1; }
  .venv/bin/python -c "import tkinter, faster_whisper" || { echo "설치 확인에 실패했습니다. Homebrew 파이썬이면 'brew install python-tk'를 실행하거나, python.org 에서 받은 파이썬을 쓰세요."; rm -rf .venv; read -p "Enter를 누르면 닫힙니다."; exit 1; }
  echo ok > .venv/installed.ok
fi
if [ $# -eq 0 ]; then
  .venv/bin/python app.py || { echo; echo "프로그램이 오류로 종료됐습니다. 위 내용을 알려주세요."; read -p "Enter를 누르면 닫힙니다."; }
  exit 0
fi
.venv/bin/python transcribe.py "$@"

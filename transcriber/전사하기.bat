@echo off
rem 더블클릭: 창 프로그램이 열립니다.  파일을 끌어다 놓기: 창 없이 바로 전사합니다.
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\installed.ok" (
  .venv\Scripts\python -c "import sys" >nul 2>&1 || (echo [준비] 파이썬이 바뀌어 다시 설치합니다. & del ".venv\installed.ok")
)
if not exist ".venv\installed.ok" (
  echo [준비] 처음 한 번만 파이썬 가상환경과 패키지를 설치합니다. 몇 분 걸립니다.
  if exist ".venv" rmdir /s /q ".venv"
  python -m venv .venv || (echo 파이썬이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요. & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\pip install -q -r requirements.txt || (echo 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요. & rmdir /s /q ".venv" & pause & exit /b 1)
  .venv\Scripts\python -c "import tkinter, faster_whisper" || (echo 설치 확인에 실패했습니다. 위 오류 내용을 알려주세요. python.org 에서 받은 파이썬인지 확인하세요. & rmdir /s /q ".venv" & pause & exit /b 1)
  echo ok> ".venv\installed.ok"
)
if "%~1"=="" (
  .venv\Scripts\python app.py || (echo. & echo 프로그램이 오류로 종료됐습니다. 위 내용을 알려주세요. & pause)
  exit /b 0
)
.venv\Scripts\python transcribe.py %*
echo.
echo 작업이 끝났습니다. 결과는 입력 파일 옆 "전사결과" 폴더에 있습니다.
pause

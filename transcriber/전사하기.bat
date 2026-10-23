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
  python -m venv .venv || (echo 가상환경을 만들지 못했습니다. 파이썬이 없다면 https://www.python.org/downloads/ 에서 설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요. 파이썬이 있다면 폴더 경로가 너무 길 수 있으니 C:\전사기 처럼 짧은 곳으로 옮겨 다시 실행하세요. & if exist ".venv" rmdir /s /q ".venv" & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\pip install -q -r requirements.txt || (echo 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요. & rmdir /s /q ".venv" & pause & exit /b 1)
  .venv\Scripts\python -c "import tkinter, faster_whisper" || (echo 설치 확인에 실패했습니다. 위 오류 내용을 알려주세요. python.org 에서 받은 파이썬인지 확인하세요. & rmdir /s /q ".venv" & pause & exit /b 1)
  where nvidia-smi >nul 2>&1 && (
    echo [준비] NVIDIA 그래픽카드가 있어 GPU 라이브러리도 설치합니다. 1GB 이상이라 몇 분 더 걸립니다.
    .venv\Scripts\pip install -q -r requirements-gpu.txt || echo [안내] GPU 라이브러리 설치에 실패했습니다. CPU로는 동작하며, 나중에 .venv\Scripts\pip install -r requirements-gpu.txt 로 다시 시도할 수 있습니다.
  )
  echo ok> ".venv\installed.ok"
)
if "%~1"=="" (
  .venv\Scripts\python app.py || (echo. & echo 프로그램이 오류로 종료됐습니다. 위 내용을 알려주세요. & pause)
  exit /b 0
)
rem 끌어다 놓기 모드. 프로그램이 오류 메시지 없이 강제 종료되면(음성인식 엔진 내부 오류) 완료된 구간은 저장되어 있으므로 자동으로 이어서 다시 시작합니다.
rem 종료 코드 0(완료)·1(파일 문제로 실패)·130(사용자 중단)은 재시작하지 않습니다.
set tries=0
:run
.venv\Scripts\python transcribe.py %*
set code=%errorlevel%
if %code%==0 goto done
if %code%==1 goto done
if %code%==130 goto done
set /a tries+=1
if %tries% geq 3 goto done
echo.
echo [안내] 프로그램이 예기치 않게 종료됐습니다 (코드 %code%). 완료된 구간은 저장되어 있으니 10초 후 이어서 다시 시작합니다. (%tries%/3)
timeout /t 10 /nobreak >nul
goto run
:done
echo.
echo 작업이 끝났습니다. 결과는 입력 파일 옆 "전사결과" 폴더에 있습니다.
pause

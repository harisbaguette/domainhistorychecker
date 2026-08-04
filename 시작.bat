@echo off
chcp 65001 >nul
rem 윈도에서 두 번 눌러 실행하는 시작 스크립트.
cd /d "%~dp0"

echo ===== 낙장도메인 품질 체커 =====
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo 먼저 uv^(파이썬 실행 도구^)를 설치해야 합니다.
  echo 파워셸을 열고 아래 한 줄을 붙여 넣어 실행한 뒤, 이 파일을 다시 눌러 주세요.
  echo.
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo.
  pause
  exit /b 1
)

echo [1/3] 필요한 프로그램을 준비합니다...
call uv sync
if errorlevel 1 (
  echo 준비에 실패했습니다. 인터넷 연결을 확인해 주세요.
  pause
  exit /b 1
)

if not exist "%USERPROFILE%\AppData\Local\ms-playwright" (
  echo [2/3] 화면 사진을 찍을 브라우저를 처음 한 번 내려받습니다 ^(수백 MB^)...
  call uv run playwright install chromium
) else (
  echo [2/3] 캡쳐용 브라우저가 이미 있습니다.
)

if "%DOMAINCHECKER_PORT%"=="" set DOMAINCHECKER_PORT=8765
echo [3/3] 프로그램을 켭니다. 잠시 뒤 웹 브라우저가 열립니다.
echo      끄려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요.
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:%DOMAINCHECKER_PORT%/"
call uv run domainchecker
pause

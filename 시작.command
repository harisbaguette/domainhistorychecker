#!/bin/bash
# 맥에서 두 번 눌러 실행하는 시작 스크립트.
cd "$(dirname "$0")" || exit 1

echo "===== 낙장도메인 품질 체커 ====="
echo

# 1. uv 확인
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "먼저 uv(파이썬 실행 도구)를 설치해야 합니다."
  echo "터미널에 아래 한 줄을 붙여 넣고 실행한 뒤, 이 파일을 다시 눌러 주세요."
  echo
  echo '  curl -LsSf https://astral.sh/uv/install.sh | sh'
  echo
  read -r -p "엔터를 누르면 창이 닫힙니다."
  exit 1
fi

# 2. 파이썬 꾸러미 설치 (처음 한 번은 몇 분 걸립니다)
echo "[1/3] 필요한 프로그램을 준비합니다…"
uv sync || { echo "준비에 실패했습니다. 인터넷 연결을 확인해 주세요."; read -r -p "엔터를 누르면 닫힙니다."; exit 1; }

# 3. 화면 캡쳐용 브라우저 (최초 1회, 수백 MB)
if [ ! -d "$HOME/Library/Caches/ms-playwright" ]; then
  echo "[2/3] 화면 사진을 찍을 브라우저를 처음 한 번 내려받습니다 (수백 MB, 몇 분 걸립니다)…"
  uv run playwright install chromium || echo "브라우저 설치를 건너뜁니다 — 캡쳐 없이도 검사는 됩니다."
else
  echo "[2/3] 캡쳐용 브라우저가 이미 있습니다."
fi

# 4. 서버 실행 + 브라우저 열기
PORT="${DOMAINCHECKER_PORT:-8765}"
echo "[3/3] 프로그램을 켭니다. 잠시 뒤 웹 브라우저가 열립니다."
echo "     끄려면 이 창에서 Control+C 를 누르거나 창을 닫으세요."
( sleep 3; open "http://127.0.0.1:${PORT}/" ) &
DOMAINCHECKER_PORT="$PORT" uv run domainchecker

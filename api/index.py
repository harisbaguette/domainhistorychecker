"""Vercel entry point — the same FastAPI app, pointed at writable folders.

서버리스 칸은 자기 폴더에 글을 못 쓴다. 그래서 설정과 결과가 쓰이는 자리를
쓰기가 되는 /tmp 로 옮겨 놓고 앱을 불러온다(그 자리는 잠깐 있다 지워진다).
"""

import os
import sys
from pathlib import Path

os.environ["HOME"] = "/tmp"  # ~/.domainchecker/config.json 이 쓰기 가능한 곳에 생기게
os.environ["DOMAINCHECKER_RELOAD"] = "0"
Path("/tmp/data").mkdir(parents=True, exist_ok=True)
os.chdir("/tmp")  # config.data_dir 기본값이 cwd/data 라서
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domainchecker.server import app as _app  # noqa: E402


async def app(scope, receive, send):
    """베르셀은 모든 주소를 이 파일 하나로 몰아 준다 — 그래서 앞에 붙는
    `/api/index` 를 떼어 내고 원래 주소 그대로 앱에 넘긴다."""
    if scope.get("type") in {"http", "websocket"}:
        path = scope.get("path", "")
        if path.startswith("/api/index"):
            scope = dict(scope, path=path[len("/api/index") :] or "/")
    await _app(scope, receive, send)

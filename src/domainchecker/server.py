"""Local FastAPI server: UI, run control, SSE progress, settings, report.

Binds to 127.0.0.1 by default — the API keys live in this process, so the
server must not be reachable from outside the machine unless the user opts in.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cache
from . import config as config_module
from .config import MODEL_CHAIN, Config
from .models import DomainResult
from .normalize import MAX_DOMAINS, parse_domains
from .pipeline import Pipeline
from .report import html as report_html

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

# AI 단가(2026-08-04 확인, deepseek-v4-flash-0731 기준, 100만 토큰당 달러)
AI_INPUT_PRICE = 0.09
AI_OUTPUT_PRICE = 0.18
AI_INPUT_TOKENS = 15_000
AI_OUTPUT_TOKENS = 1_000


class RunRequest(BaseModel):
    raw: str = ""
    domains: list[str] = []
    use_cache: bool = True


class PreviewRequest(BaseModel):
    raw: str = ""


class ConfigRequest(BaseModel):
    """빈 문자열로 온 키는 '바꾸지 않음'을 뜻한다."""

    openrouter: str | None = None
    serper: str | None = None
    openpagerank: str | None = None
    safebrowsing: str | None = None
    virustotal: str | None = None
    model: str | None = None
    speed_mode: str | None = None
    enable_safebrowsing: bool | None = None
    enable_virustotal: bool | None = None
    enable_capture: bool | None = None


class RunManager:
    """Owns the single running pipeline and fans progress events out to the UI."""

    def __init__(self) -> None:
        self.pipeline: Pipeline | None = None
        self.task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self.history: list[dict] = []
        self.domains: list[str] = []
        self.results: dict[str, dict] = {}
        self.running = False
        self.error = ""

    def publish(self, event: dict) -> None:
        self.history.append(event)
        if event.get("type") == "domain_done" and event.get("result"):
            self.results[event["result"]["domain"]] = event["result"]
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    @property
    def finished(self) -> bool:
        return bool(self.history) and self.history[-1].get("type") in ("finished", "error")

    async def start(self, config: Config, domains: list[str], use_cache: bool) -> None:
        if self.running:
            raise HTTPException(status_code=409, detail="이미 검사가 진행 중입니다.")
        self.history = []
        self.error = ""
        self.domains = domains
        self.results = {}
        self.running = True
        self.pipeline = Pipeline(config, on_event=self.publish)

        async def runner() -> None:
            try:
                await self.pipeline.run(domains, use_cache=use_cache)
            except Exception as exc:  # noqa: BLE001 — 파이프라인이 통째로 죽어도 UI는 알아야 한다
                self.error = f"{type(exc).__name__}: {exc}"
                self.publish({"type": "error", "message": self.error})
            finally:
                self.running = False

        self.task = asyncio.create_task(runner())

    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()

    def status(self) -> dict:
        done = sum(1 for e in self.history if e.get("type") == "domain_done")
        return {
            "running": self.running,
            "total": len(self.domains),
            "done": done,
            "domains": self.domains,
            "error": self.error,
            "finished": self.finished,
        }


def estimate(config: Config, count: int) -> dict:
    """예상 소요 시간과 무료 쿼터 소진량 (PLAN §4·§5)."""
    per_domain = 1 + config.max_snapshots + (2 if config.enable_capture else 0)
    table_per_domain = 1 + config.max_snapshots
    rpm = config.start_rpm
    minutes = count * per_domain / rpm
    table_minutes = count * table_per_domain / rpm
    slow_minutes = count * per_domain / 12
    ai_cost = count * (
        AI_INPUT_TOKENS * AI_INPUT_PRICE + AI_OUTPUT_TOKENS * AI_OUTPUT_PRICE
    ) / 1_000_000
    quota = [
        f"Serper 검색 {count}쿼리 (무료 2,500쿼리 한도)",
        f"Open PageRank {math.ceil(count / 100)}회 호출 (월 3만 도메인 한도)",
        f"AI 분석 {count}회, 예상 비용 약 ${ai_cost:.2f}",
    ]
    if config.enable_virustotal:
        quota.append(f"바이러스토탈 {count}건 (분당 4건 제한이라 약 {count / 4:.0f}분 추가)")
    return {
        "count": count,
        "wayback_requests": count * per_domain,
        "minutes": round(minutes, 1),
        "table_minutes": round(table_minutes, 1),
        "slow_minutes": round(slow_minutes, 1),
        "summary": (
            "검사할 도메인이 없습니다."
            if count == 0
            else (
                f"도메인 {count}개 · 표 결과는 {_minutes(table_minutes)}, 캡쳐까지 {_minutes(minutes)} 걸립니다. "
                f"웨이백이 속도를 낮추면(429) {_minutes(slow_minutes)}까지 늘어날 수 있습니다."
            )
        ),
        "quota": quota,
    }


def _minutes(value: float) -> str:
    """분 단위를 사람이 읽는 말로."""
    if value < 1:
        return "1분 안쪽"
    if value < 60:
        return f"약 {value:.0f}분"
    return f"약 {value / 60:.1f}시간"


def mask(value: str) -> str:
    if not value:
        return ""
    return "•" * max(0, len(value) - 4) + value[-4:]


def create_app(config_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="낙장도메인 품질 체커", docs_url=None, redoc_url=None)
    manager = RunManager()
    app.state.config_path = config_path
    app.state.manager = manager

    def load_config() -> Config:
        return config_module.load(app.state.config_path)

    def data_root() -> Path:
        return config_module.data_dir(load_config())

    @app.middleware("http")
    async def require_password(request, call_next):
        """외부 접속 모드에서만 켜지는 접속 비밀번호(키 노출 방지 · PLAN §3)."""
        password = os.environ.get("DOMAINCHECKER_PASSWORD", "")
        if password:
            header = request.headers.get("authorization", "")
            expected = "Basic " + base64.b64encode(f"domainchecker:{password}".encode()).decode()
            if not secrets.compare_digest(header, expected):
                return Response(
                    "접속 비밀번호가 필요합니다.",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="domainchecker"'},
                )
        return await call_next(request)

    base = data_root()
    (base / "captures").mkdir(parents=True, exist_ok=True)
    (base / "report").mkdir(parents=True, exist_ok=True)
    app.mount("/captures", StaticFiles(directory=base / "captures"), name="captures")
    app.mount("/report", StaticFiles(directory=base / "report", html=True), name="report")
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def stored_results() -> list[DomainResult]:
        """This run's results, else the last results.json, else whatever is cached.

        앱을 껐다 켠 뒤에도 예전에 검사해 둔 도메인으로 보고서를 만들 수 있어야 한다.
        """
        base_dir = data_root()
        payloads: list[dict] = list(manager.results.values())
        if not payloads:
            path = base_dir / "results.json"
            if path.exists():
                try:
                    payloads = json.loads(path.read_text(encoding="utf-8")).get("results", [])
                except (OSError, ValueError):
                    payloads = []
        if not payloads:
            payloads = [
                found
                for domain in sorted(cache.cached_domains(base_dir))
                if (found := cache.load(domain, base_dir))
            ]
        out = []
        for payload in payloads:
            try:
                out.append(DomainResult.model_validate(payload))
            except ValueError:
                continue
        return out

    def one_result(domain: str) -> DomainResult:
        payload = manager.results.get(domain) or cache.load(domain, data_root())
        if not payload:
            raise HTTPException(status_code=404, detail="아직 검사하지 않은 도메인입니다.")
        try:
            return DomainResult.model_validate(payload)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="저장된 결과를 읽을 수 없습니다.") from exc

    @app.get("/")
    async def index() -> Any:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return JSONResponse({"error": "static/index.html 을 찾을 수 없습니다."}, status_code=500)
        return FileResponse(page)

    @app.get("/style.css")
    async def style() -> Response:
        """UI와 보고서가 같은 스타일을 쓰도록 한 곳에서 내려준다."""
        return Response(report_html.CSS, media_type="text/css")

    @app.post("/api/preview")
    async def preview(request: PreviewRequest) -> dict:
        config = load_config()
        parsed = parse_domains(request.raw, config.max_domains)
        payload = {
            "domains": parsed.domains,
            "invalid": parsed.invalid,
            "duplicates": parsed.duplicates,
            "truncated": parsed.truncated,
            "notice": parsed.notice,
            "limit": MAX_DOMAINS,
            "missing_keys": config.missing_required_keys(),
            "estimate": estimate(config, len(parsed.domains)),
        }
        if parsed.truncated:
            payload["split_notice"] = (
                f"한 번에 {config.max_domains}개까지만 검사합니다. "
                f"남은 {parsed.truncated}개는 다음 회차로 나눠서 넣어 주세요."
            )
        return payload

    @app.post("/api/run")
    async def run(request: RunRequest) -> dict:
        config = load_config()
        domains = request.domains or parse_domains(request.raw, config.max_domains).domains
        if not domains:
            raise HTTPException(status_code=400, detail="검사할 도메인이 없습니다.")
        await manager.start(config, domains, request.use_cache)
        return {"started": True, "count": len(domains), "estimate": estimate(config, len(domains))}

    @app.post("/api/stop")
    async def stop() -> dict:
        manager.stop()
        return {"stopped": True, "note": "끝난 도메인은 저장돼 있어 '이어서 검사'로 재개할 수 있습니다."}

    @app.post("/api/resume")
    async def resume() -> dict:
        if not manager.domains:
            raise HTTPException(status_code=400, detail="이어서 검사할 목록이 없습니다.")
        config = load_config()
        await manager.start(config, manager.domains, use_cache=True)
        return {"started": True, "count": len(manager.domains)}

    @app.get("/api/status")
    async def status() -> dict:
        return manager.status()

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        queue = manager.subscribe()
        replay = list(manager.history)
        finished_already = not manager.running and (manager.finished or not replay)

        async def stream():
            try:
                yield _sse({"type": "snapshot", **manager.status()})
                for event in replay:
                    yield _sse(event)
                if finished_already:
                    return
                while True:
                    event = await queue.get()
                    yield _sse(event)
                    if event.get("type") in ("finished", "error"):
                        return
            finally:
                manager.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/results")
    async def results() -> dict:
        return {"results": [r.model_dump(mode="json") for r in stored_results()]}

    @app.get("/api/results/{domain}")
    async def result_one(domain: str) -> dict:
        return one_result(domain).model_dump(mode="json")

    @app.get("/api/detail/{domain}")
    async def detail(domain: str) -> dict:
        """The same evidence view the static report uses."""
        return {"html": report_html.detail_fragment(one_result(domain), capture_base="/captures")}

    @app.get("/api/config")
    async def get_config() -> dict:
        config = load_config()
        keys = config.keys.model_dump()
        return {
            "keys_masked": {name: mask(value) for name, value in keys.items()},
            "has_key": {name: bool(value) for name, value in keys.items()},
            "model": config.model,
            "models": list(MODEL_CHAIN),
            "speed_mode": config.speed_mode,
            "enable_safebrowsing": config.enable_safebrowsing,
            "enable_virustotal": config.enable_virustotal,
            "enable_capture": config.enable_capture,
            "missing_keys": config.missing_required_keys(),
            "config_path": str(app.state.config_path or config_module.CONFIG_PATH),
        }

    @app.post("/api/config")
    async def set_config(request: ConfigRequest) -> dict:
        config = load_config()
        for name in ("openrouter", "serper", "openpagerank", "safebrowsing", "virustotal"):
            value = getattr(request, name)
            if value:  # 빈 값이면 기존 키를 그대로 둔다
                setattr(config.keys, name, value.strip())
        if request.model:
            config.model = request.model
        if request.speed_mode in ("adaptive", "safe"):
            config.speed_mode = request.speed_mode
        for flag in ("enable_safebrowsing", "enable_virustotal", "enable_capture"):
            value = getattr(request, flag)
            if value is not None:
                setattr(config, flag, value)
        config_module.save(config, app.state.config_path)
        return {"saved": True, "missing_keys": config.missing_required_keys()}

    @app.post("/api/report")
    async def build_report() -> dict:
        found = stored_results()
        if not found:
            raise HTTPException(status_code=400, detail="아직 만들 결과가 없습니다.")
        path = report_html.write_report(found, data_root())
        return {"path": str(path), "url": "/report/index.html", "count": len(found)}

    return app


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


app = create_app()


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main() -> None:
    """Entry point used by 시작.command / 시작.bat."""
    import uvicorn

    host = os.environ.get("DOMAINCHECKER_HOST", "127.0.0.1")
    port = int(os.environ.get("DOMAINCHECKER_PORT", "8765"))
    if host not in LOCAL_HOSTS and not os.environ.get("DOMAINCHECKER_PASSWORD"):
        # 밖에서 접속되게 열면서 비밀번호가 없으면 API 키가 통째로 노출된다.
        raise SystemExit(
            "이 컴퓨터 밖에서도 접속하게 하려면 접속 비밀번호를 반드시 정해야 합니다.\n"
            "  DOMAINCHECKER_PASSWORD=원하는비밀번호 DOMAINCHECKER_HOST=0.0.0.0 uv run domainchecker\n"
            "(비밀번호 없이 밖으로 열면 저장해 둔 API 키가 그대로 새어 나갑니다.)"
        )
    uvicorn.run("domainchecker.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

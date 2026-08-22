"""Local FastAPI server: UI, run control, SSE progress, settings, report.

Binds to 127.0.0.1 only — the API keys live in this process, so nothing outside
this machine can reach it. That binding is the lock; there is no login screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cache
from . import config as config_module
from .config import MODEL_CHAIN, Config
from .models import DomainResult
from .normalize import MAX_DOMAINS, parse_domains
from .pipeline import Pipeline, clear_run_state, is_stale, load_run_state, save_run_state
from .report import html as report_html

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
SOURCE_TREE = Path(__file__).resolve().parents[2] / "pyproject.toml"


def dev_mode() -> bool:
    """Am I running from a checkout with the dev tools installed?

    개발 중이면 파일을 고칠 때마다 알아서 다시 켜지고 브라우저 캐시도 끈다.
    배포본은 소스 트리도 watchfiles 도 없어서 절대 켜지지 않는다.
    DOMAINCHECKER_RELOAD 로 직접 켜고 끌 수도 있다.
    """
    from importlib.util import find_spec

    raw = os.environ.get("DOMAINCHECKER_RELOAD", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return SOURCE_TREE.exists() and find_spec("watchfiles") is not None

# AI 단가(2026-08-04 확인, deepseek-v4-flash-0731 기준, 100만 토큰당 달러)
AI_INPUT_PRICE = 0.09
AI_OUTPUT_PRICE = 0.18
AI_INPUT_TOKENS = 15_000
AI_OUTPUT_TOKENS = 1_000
KRW_PER_USD = 1_400  # 원화 병기 기준(2026-08-04)


class RunRequest(BaseModel):
    raw: str = ""
    domains: list[str] = []
    use_cache: bool = True


class PreviewRequest(BaseModel):
    raw: str = ""


class PurgeRequest(BaseModel):
    """목록에서 치울 도메인들 — 주인이 있어 살 수 없는 줄을 걷어내는 데 쓴다."""

    domains: list[str] = []


class PlannedRequest(BaseModel):
    """매입 예정 표시 — planned=True 면 넣고, False 면 뺀다."""

    domain: str = ""
    planned: bool = True


PLANNED_NAME = "planned.json"


def load_planned(base: Path) -> list[str]:
    """매입 예정으로 골라 둔 도메인들 — 앱을 껐다 켜도 남아야 해서 파일로 산다."""
    path = base / PLANNED_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for one in data.get("domains") or []:
        name = str(one).strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def save_planned(base: Path, domains: list[str]) -> None:
    # 목록을 못 남겨도 앱은 계속 돌아야 한다.
    with contextlib.suppress(OSError):
        (base / PLANNED_NAME).write_text(
            json.dumps({"domains": domains}, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class ConfigRequest(BaseModel):
    """빈 문자열로 온 키는 '바꾸지 않음'을 뜻한다."""

    openrouter: str | None = None
    # 이름을 담아 보내면 그 키를 지운다 — 빈 문자열은 '바꾸지 않음'이라 지우기에 못 쓴다
    clear_keys: list[str] | None = None
    model: str | None = None
    speed_mode: str | None = None
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
        self.base: Path | None = None  # run_state.json 을 찾을 데이터 폴더
        # 검사가 도는 동안 들어온 다음 묶음들 — 지금 검사가 끝나면 차례로 자동 시작한다
        self.pending: list[list[str]] = []
        self.config_loader = None  # create_app 이 넣어 준다(대기줄 자동 시작용)

    def publish(self, event: dict) -> None:
        # step(한 도메인 안의 진행 토막)은 순간 표시용이다 — 기록에 쌓으면 다시
        # 붙을 때마다 수백 토막을 재생하게 되므로 지금 보고 있는 사람에게만 보낸다.
        if event.get("type") != "step":
            self.history.append(event)
        # capture_done도 결과를 실어 온다 — 사진이 붙은 최신본으로 갈아 끼워야
        # 상세 화면이 방금 찍은 캡쳐를 보여 준다.
        if event.get("type") in ("domain_done", "capture_done") and event.get("result"):
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
            raise HTTPException(status_code=409, detail="이미 분석이 진행 중입니다.")
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
                # 대기줄에 다음 묶음이 있으면 이어서 자동 시작한다.
                # 터져서 끝났을 때는 시작하지 않는다 — 같은 원인으로 줄줄이 터진다.
                if self.pending and not self.error and self.config_loader is not None:
                    next_domains = self.pending.pop(0)
                    # 참조를 self.task 에 쥔다 — 안 쥐면 GC 가 도는 중에 지울 수 있다
                    self.task = asyncio.create_task(self._start_next(next_domains))

        self.task = asyncio.create_task(runner())

    async def _start_next(self, domains: list[str]) -> None:
        """대기줄 자동 시작 — 그 짧은 틈에 다른 시작이 끼어들면 묶음을 줄에 되돌린다."""
        try:
            await self.start(self.config_loader(), domains, use_cache=True)
        except HTTPException:
            self.pending.insert(0, domains)

    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.stop()

    def resume_domains(self) -> list[str]:
        """이 실행의 목록, 없으면 앱을 껐다 켜기 전에 남겨 둔 목록.

        끝까지 잘 끝난 판의 목록은 이어서 할 거리가 아니다 — 그걸 세면 화면이
        "중단된 분석이 남아 있습니다"를 영원히 띄운다(진상 검증 지적).
        """
        last = self.history[-1] if self.history else {}
        ended_clean = (
            last.get("type") == "finished"
            and not last.get("stopped")
            and not last.get("failed")
        )
        if self.domains and not ended_clean:
            return list(self.domains)
        return load_run_state(self.base) if self.base else []

    def status(self) -> dict:
        done = sum(1 for e in self.history if e.get("type") == "domain_done")
        return {
            "running": self.running,
            "total": len(self.domains),
            "done": done,
            "domains": self.domains,
            "error": self.error,
            "finished": self.finished,
            "resumable": bool(self.resume_domains()),
            "queued_batches": len(self.pending),
            "queued_domains": sum(len(batch) for batch in self.pending),
        }


# 도메인마다 읽을 양이 달라 미리 알 수 없다 — 평균 가정치로 안내한다.
# (앞페이지 변경본 평균 25장 + AI가 고른 하위 페이지 최대 20장 ≈ 40, 목록 조회 3번.)
AVG_PAGES_ASSUMED = 40
# 3단 깔때기의 바닥값 — 1단에서 끝나는 도메인이 웨이백에 쓰는 횟수는 목록 조회 한 번뿐이다.
# 위험 명단(0단)에 걸린 도메인은 이마저도 0회지만, 목록에 몇 개가 걸릴지는 미리 알 수 없어
# 바닥값에는 세지 않는다(실제는 이보다 짧아진다).
CHEAP_STAGE_REQUESTS = 1


def estimate(config: Config, count: int) -> dict:
    """예상 소요 시간과 무료 쿼터 소진량.

    깔때기라서 도메인마다 드는 값이 다르다 — 1단에서 끝나면 웨이백 한 번, 2단까지
    가면 본문 수십 장이다. 그래서 하나의 값이 아니라 바닥과 천장을 함께 안내한다.
    """
    per_domain = 3 + AVG_PAGES_ASSUMED + (2 if config.enable_capture else 0)
    table_per_domain = 3 + AVG_PAGES_ASSUMED
    rpm = config.start_rpm
    minutes = count * per_domain / rpm
    table_minutes = count * table_per_domain / rpm
    slow_minutes = count * per_domain / 12
    fast_minutes = count * CHEAP_STAGE_REQUESTS / rpm
    ai_cost = count * (
        AI_INPUT_TOKENS * AI_INPUT_PRICE + AI_OUTPUT_TOKENS * AI_OUTPUT_PRICE
    ) / 1_000_000
    quota = []
    if config.keys.openrouter:
        quota.append(f"AI 분석 {count}번, 드는 돈 {_money(ai_cost)}")
    # 키를 안 넣은 검사는 무엇으로 대신 도는지 함께 알려 준다.
    quota += [f"{note} — 공개 자료라 키도 돈도 안 듭니다" for note in config.free_fallbacks()]
    return {
        "count": count,
        "wayback_requests": count * per_domain,
        "minutes": round(minutes, 1),
        "fast_minutes": round(fast_minutes, 1),
        "table_minutes": round(table_minutes, 1),
        "slow_minutes": round(slow_minutes, 1),
        "summary": (
            "분석할 도메인이 없습니다."
            if count == 0
            else (
                f"도메인 {count}개 · 위험 명단에 걸리거나 이미 주인이 있는 것은 앞 단계에서 끝나서, "
                f"그런 것만 있으면 {_minutes(fast_minutes)}에 끝납니다. "
                f"반대로 전부 옛 화면 정독까지 가면 표 결과 {_minutes(table_minutes)}, "
                f"사진까지 {_minutes(minutes)}이고, "
                f"옛날 화면 보관소(웨이백)가 잠깐 막으면 {_minutes(slow_minutes)}까지 늘어날 수 있습니다."
            )
        ),
        "quota": quota,
    }


def _money(usd: float) -> str:
    """달러 값에 원화를 나란히 적어 준다(1달러=1,400원 기준)."""
    if usd < 0.01:
        return "몇 원 수준"
    return f"약 ${usd:.2f} (약 {round(usd * KRW_PER_USD):,}원, 1달러=1,400원 기준)"


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

    if dev_mode():
        # 개발 중에는 브라우저가 옛 화면을 붙들고 있으면 안 된다 — 그냥 새로고침으로 최신이 뜨게 한다.
        @app.middleware("http")
        async def no_browser_cache(request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    base = data_root()
    manager.base = base
    (base / "captures").mkdir(parents=True, exist_ok=True)
    (base / "report").mkdir(parents=True, exist_ok=True)
    app.mount("/captures", StaticFiles(directory=base / "captures"), name="captures")
    app.mount("/report", StaticFiles(directory=base / "report", html=True), name="report")
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # 표시용 글꼴은 /style.css 옆자리(/fonts/…)에서 부른다 — 스타일 한 장이 화면과
    # 보고서 양쪽에서 같은 상대 경로로 글꼴을 찾게 하려는 것이다.
    if (STATIC_DIR / "fonts").is_dir():
        app.mount("/fonts", StaticFiles(directory=STATIC_DIR / "fonts"), name="fonts")

    def stored_results() -> list[DomainResult]:
        """Union of this run, results.json and the cache — newest source wins.

        예전에는 셋 중 처음 걸리는 한 곳만 읽었다. 그래서 새 검사를 시작하는 순간
        지난 회차 결과가 화면에서 통째로 사라졌다 — 묶음을 나눠 검사하는 사람이
        먼저 검사한 것을 잃지 않도록, 세 곳을 도메인별로 합쳐서 돌려준다.
        """
        base_dir = data_root()
        cache_days = load_config().cache_days
        merged: dict[str, dict] = {}
        for domain in sorted(cache.cached_domains(base_dir)):
            found = cache.load(domain, base_dir)
            # 믿는 기간(cache_days)이 지난 저장분은 목록에 되살리지 않는다 —
            # 옛날에 시험 삼아 검사한 도메인이 슬그머니 다시 나타나면 안 된다.
            if found and not is_stale(found, cache_days, engine_counts=False):
                merged[str(found.get("domain", domain))] = found
        path = base_dir / "results.json"
        if path.exists():
            try:
                for payload in json.loads(path.read_text(encoding="utf-8")).get("results", []):
                    if payload.get("domain"):
                        merged[str(payload["domain"])] = payload
            except (OSError, ValueError):
                pass  # 깨진 파일이면 캐시 몫만 보여 준다
        for domain, payload in manager.results.items():
            merged[domain] = payload
        out = []
        for payload in merged.values():
            try:
                out.append(DomainResult.model_validate(payload))
            except ValueError:
                continue
        return out

    def one_result(domain: str) -> DomainResult:
        payload = manager.results.get(domain) or cache.load(domain, data_root())
        if not payload:
            raise HTTPException(status_code=404, detail="아직 분석하지 않은 도메인입니다.")
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
        # 앱을 켠 채로 모양 규칙을 고쳐도 새로고침만 하면 바로 보이도록 그때그때 읽는다
        return Response(report_html.current_css(), media_type="text/css")

    # ── 홈 화면에 앱으로 담기(PWA) ────────────────────────────────
    # 두 파일은 반드시 주소 맨 앞자리(/…)에서 내려야 한다. /static/ 밑에 두면
    # 심부름꾼이 맡는 범위가 그 아래 폴더로 좁아져서 앱 전체를 못 감싼다.
    @app.get("/manifest.webmanifest")
    async def manifest() -> Any:
        path = STATIC_DIR / "manifest.webmanifest"
        if not path.exists():
            raise HTTPException(status_code=404, detail="manifest.webmanifest 이 없습니다.")
        return FileResponse(path, media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker() -> Any:
        path = STATIC_DIR / "sw.js"
        if not path.exists():
            raise HTTPException(status_code=404, detail="sw.js 가 없습니다.")
        return FileResponse(
            path,
            media_type="text/javascript",
            # 심부름꾼 파일 자체가 캐시에 눌어붙으면 고쳐도 반영이 안 된다
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/favicon.ico")
    async def favicon() -> Any:
        path = STATIC_DIR / "icons" / "favicon-32.png"
        if not path.exists():
            raise HTTPException(status_code=404, detail="favicon 이 없습니다.")
        return FileResponse(path, media_type="image/png")

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
            raise HTTPException(status_code=400, detail="분석할 도메인이 없습니다.")
        # 이미 검사가 도는 중이면 거절하지 않고 대기줄에 세운다 — 지금 검사가
        # 끝나는 즉시 자동으로 이어서 돈다(사람이 폰을 다시 볼 필요가 없게).
        if manager.running:
            manager.pending.append(domains)
            return {
                "queued": True,
                "count": len(domains),
                "position": len(manager.pending),
            }
        manager.config_loader = load_config
        await manager.start(config, domains, request.use_cache)
        return {"started": True, "count": len(domains), "estimate": estimate(config, len(domains))}

    @app.post("/api/stop")
    async def stop() -> dict:
        manager.pending.clear()  # 멈추라는 말은 대기줄까지 포함한다
        manager.stop()
        return {"stopped": True, "note": "끝난 도메인은 저장돼 있어 '이어서 분석'으로 재개할 수 있습니다."}

    @app.post("/api/resume")
    async def resume() -> dict:
        manager.base = data_root()
        domains = manager.resume_domains()
        if not domains:
            raise HTTPException(status_code=400, detail="이어서 분석할 목록이 없습니다.")
        config = load_config()
        manager.config_loader = load_config
        await manager.start(config, domains, use_cache=True)
        return {"started": True, "count": len(domains)}

    @app.get("/api/status")
    async def status() -> dict:
        manager.base = data_root()
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

    @app.get("/api/planned")
    async def get_planned() -> dict:
        return {"domains": load_planned(data_root())}

    @app.post("/api/planned")
    async def set_planned(request: PlannedRequest) -> dict:
        domain = request.domain.strip().lower()
        if not domain:
            raise HTTPException(status_code=400, detail="도메인이 없습니다.")
        base_dir = data_root()
        domains = load_planned(base_dir)
        if request.planned and domain not in domains:
            domains.append(domain)
        elif not request.planned:
            domains = [d for d in domains if d != domain]
        save_planned(base_dir, domains)
        return {"domains": domains}

    @app.delete("/api/results")
    async def clear_results() -> dict:
        """저장된 검사 결과를 전부 지운다 — 목록·저장 파일·캐시·화면 사진까지."""
        if manager.running:
            raise HTTPException(
                status_code=409, detail="분석이 도는 동안에는 지울 수 없습니다. 먼저 중단해 주세요."
            )
        base_dir = data_root()
        (base_dir / "results.json").unlink(missing_ok=True)
        (base_dir / PLANNED_NAME).unlink(missing_ok=True)  # 근거가 사라지면 표시도 지운다
        clear_run_state(base_dir)
        cache_root = cache.cache_dir(base_dir)
        if cache_root.exists():
            for path in cache_root.glob("*.json"):
                path.unlink(missing_ok=True)
        captures_dir = base_dir / "captures"
        if captures_dir.exists():
            shutil.rmtree(captures_dir, ignore_errors=True)
            captures_dir.mkdir(parents=True, exist_ok=True)
        manager.history = []
        manager.domains = []
        manager.results = {}
        manager.pending.clear()
        return {"cleared": True}

    @app.post("/api/results/purge")
    async def purge_results(request: PurgeRequest) -> dict:
        """고른 도메인만 지운다 — 저장분·캐시·화면 사진까지 함께.

        전부 지우기(DELETE /api/results)와 같은 자리를 건드리되 범위만 좁힌다.
        한 곳만 지우면 다음에 켤 때 지운 줄이 되살아난다(stored_results 가
        results.json → 캐시 순으로 되읽기 때문).
        """
        if manager.running:
            raise HTTPException(
                status_code=409, detail="분석이 도는 동안에는 지울 수 없습니다. 먼저 중단해 주세요."
            )
        targets = {d.strip().lower() for d in request.domains if d.strip()}
        if not targets:
            raise HTTPException(status_code=400, detail="지울 도메인이 없습니다.")

        base_dir = data_root()
        for domain in targets:
            cache.cache_path(domain, base_dir).unlink(missing_ok=True)

        path = base_dir / "results.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                kept = [
                    r for r in payload.get("results", [])
                    if str(r.get("domain", "")).lower() not in targets
                ]
                payload["results"] = kept
                payload["count"] = len(kept)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                pass  # 파일이 깨져 있으면 캐시만 지운 것으로 둔다 — 여기서 더 부수지 않는다

        captures_dir = base_dir / "captures"
        if captures_dir.exists():
            for domain in targets:
                stem = cache.cache_path(domain, base_dir).stem
                for shot in captures_dir.glob(f"{stem}_*.png"):
                    shot.unlink(missing_ok=True)

        for domain in targets:
            manager.results.pop(domain, None)
        manager.domains = [d for d in manager.domains if d.lower() not in targets]

        # 매입 예정 표시에서도 뺀다 — 결과가 사라진 도메인이 예정 목록에 유령으로 남지 않게.
        save_planned(base_dir, [d for d in load_planned(base_dir) if d not in targets])

        # "이어서 검사" 목록에서도 빼야 한다 — 안 빼면 앱을 다시 켜고 이어서 검사를
        # 누르는 순간 방금 치운 줄이 그대로 되살아난다.
        left = [d for d in load_run_state(base_dir) if d.lower() not in targets]
        if left:
            save_run_state(base_dir, left)
        else:
            clear_run_state(base_dir)
        return {"removed": len(targets)}

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
            "enable_capture": config.enable_capture,
            "missing_keys": config.missing_required_keys(),
            "free_fallbacks": config.free_fallbacks(),
            "config_path": str(app.state.config_path or config_module.CONFIG_PATH),
        }

    @app.post("/api/config")
    async def set_config(request: ConfigRequest) -> dict:
        config = load_config()
        if request.openrouter:  # 빈 값이면 기존 키를 그대로 둔다
            config.keys.openrouter = request.openrouter.strip()
        # 지우기는 이름을 따로 담아 보낸다 — 빈 값과 '지워라'를 헷갈리지 않게
        if "openrouter" in (request.clear_keys or []):
            config.keys.openrouter = ""
        if request.model:
            config.model = request.model
        if request.speed_mode in ("adaptive", "safe"):
            config.speed_mode = request.speed_mode
        if request.enable_capture is not None:
            config.enable_capture = request.enable_capture
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


HOST = "127.0.0.1"  # 내 컴퓨터 전용 — 이 묶임 자체가 잠금이라 로그인 화면이 없다


def main() -> None:
    """Entry point used by 시작.command / 시작.bat."""
    from importlib.util import find_spec

    import uvicorn

    port = int(os.environ.get("DOMAINCHECKER_PORT", "8765"))
    reload = dev_mode()
    if reload and find_spec("watchfiles") is None:
        raise SystemExit("자동 재시작을 쓰려면 먼저 `uv sync --group dev` 를 한 번 실행해 주세요.")
    if reload:
        print("[개발 모드] 파일을 고치면 서버가 알아서 다시 켜집니다. 끄려면 DOMAINCHECKER_RELOAD=0")
    uvicorn.run(
        "domainchecker.server:app",
        host=HOST,
        port=port,
        log_level="info",
        reload=reload,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload else None,
    )


if __name__ == "__main__":
    main()

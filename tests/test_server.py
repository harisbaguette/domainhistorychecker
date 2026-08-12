"""서버 테스트 — 실 네트워크 호출 없음(파이프라인을 가짜로 바꿔치기)."""

import asyncio
import json
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from domainchecker import cache, server
from domainchecker import config as config_module
from domainchecker.analyze.scoring import judge
from domainchecker.config import Config
from domainchecker.pipeline import load_run_state, save_run_state
from domainchecker.server import create_app, estimate


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    config_module.save(Config(data_dir=str(tmp_path / "data")), path)
    return path


@pytest.fixture
def client(config_path):
    with TestClient(create_app(config_path)) as test_client:
        yield test_client


def test_ui_page_and_shared_style_are_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "낙장도메인 품질 체커" in page.text
    assert 'href="/style.css"' in page.text

    css = client.get("/style.css")
    assert css.status_code == 200
    # 색은 DW 토큰 한 곳에서만 나오고, 모양은 DW 부품 원본을 그대로 쓴다
    assert "--dw-success-ink" in css.text
    assert ".dw-badge" in css.text
    assert ".dw-button" in css.text
    assert ".app-years" in css.text  # 이 앱만의 레이아웃도 같이 실린다


def test_preview_normalizes_input_and_estimates(client):
    res = client.post(
        "/api/preview",
        json={"raw": "https://WWW.Example.com/path\nexample.com, foo.net\n엉뚱한값"},
    )
    data = res.json()

    assert res.status_code == 200
    assert data["domains"] == ["example.com", "foo.net"]
    assert data["duplicates"] == 1
    assert data["invalid"] == ["엉뚱한값"]
    assert "중복 1개 제거" in data["notice"]
    assert data["estimate"]["count"] == 2
    assert "분" in data["estimate"]["summary"]
    # 키가 없는 기본 상태에서는 "돈도 키도 안 드는 공개 자료"로 안내한다.
    assert any("공개 자료" in q for q in data["estimate"]["quota"])


def test_preview_explains_the_1000_limit(client):
    raw = "\n".join(f"d{i}.com" for i in range(1100))
    data = client.post("/api/preview", json={"raw": raw}).json()

    assert len(data["domains"]) == 1000
    assert data["truncated"] == 100
    assert "나눠서" in data["split_notice"]


def test_estimate_matches_the_plan_budget():
    config = Config()  # 분당 30건, 변경본 전부 + AI 선별 정독(평균 40장 가정), 캡쳐 켬
    numbers = estimate(config, 100)

    assert numbers["wayback_requests"] == 4500  # 목록 3 + 본문 평균 40 + 캡쳐 2
    assert numbers["minutes"] == pytest.approx(150.0)
    assert numbers["table_minutes"] == pytest.approx(143.3, abs=0.1)
    assert numbers["slow_minutes"] == pytest.approx(375.0)


def test_config_saves_keys_and_keeps_them_when_left_blank(client, config_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    saved = client.post(
        "/api/config",
        json={"openrouter": "sk-or-secret-1234", "speed_mode": "safe", "enable_capture": False},
    )
    assert saved.status_code == 200

    data = client.get("/api/config").json()
    assert data["has_key"]["openrouter"] is True
    assert data["keys_masked"]["openrouter"].endswith("1234")
    assert "sk-or-secret" not in json.dumps(data)  # 원문 키는 화면에 내려보내지 않는다
    assert data["speed_mode"] == "safe"
    assert data["enable_capture"] is False
    # 키는 전부 선택이 됐다 — 하나도 없어도 판정이 나온다.
    assert data["missing_keys"] == []

    # 빈 값으로 저장하면 기존 키가 살아 있어야 한다
    client.post("/api/config", json={"openrouter": "", "model": "deepseek/deepseek-v3.2"})
    again = client.get("/api/config").json()
    assert again["has_key"]["openrouter"] is True
    assert again["model"] == "deepseek/deepseek-v3.2"
    assert config_module.load(config_path).keys.openrouter == "sk-or-secret-1234"


def test_config_lists_what_runs_instead_when_the_ai_key_is_absent(client, monkeypatch):
    """키가 없어도 '무엇으로 대신 보는지'를 화면에 알려 줄 수 있어야 한다."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = client.get("/api/config").json()
    assert any("규칙 검사" in note for note in data["free_fallbacks"])


def test_run_requires_domains(client):
    assert client.post("/api/run", json={"raw": "   "}).status_code == 400


class FakePipeline:
    """진짜 파이프라인 대신 진행 이벤트만 흘려 주는 가짜."""

    payload: ClassVar[dict] = {}

    def __init__(self, config, on_event=None, **kwargs):
        self.on_event = on_event
        self.stopped = False

    def stop(self):
        self.stopped = True

    async def run(self, domains, use_cache=True):
        total = len(domains)
        self.on_event({"type": "start", "done": 0, "total": total, "domains": domains})
        for index, domain in enumerate(domains):
            await asyncio.sleep(0)
            self.on_event(
                {
                    "type": "domain_done",
                    "done": index,
                    "total": total,
                    "domain": domain,
                    "cached": False,
                    "result": {**self.payload, "domain": domain},
                }
            )
        self.on_event({"type": "finished", "done": total, "total": total, "stopped": False})
        return []


@pytest.fixture
def fake_run(monkeypatch, sample_result):
    FakePipeline.payload = judge(sample_result).model_dump(mode="json")
    monkeypatch.setattr(server, "Pipeline", FakePipeline)
    return FakePipeline


def read_sse(client) -> list[dict]:
    events = []
    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event["type"] in ("finished", "error"):
                break
    return events


def test_run_streams_progress_over_sse(client, fake_run):
    started = client.post("/api/run", json={"raw": "example.com\nfoo.net"})
    assert started.status_code == 200
    assert started.json()["count"] == 2

    events = read_sse(client)
    kinds = [e["type"] for e in events]

    assert kinds[0] == "snapshot"
    assert "domain_done" in kinds
    assert kinds[-1] == "finished"
    done = [e for e in events if e["type"] == "domain_done"]
    assert {e["domain"] for e in done} == {"example.com", "foo.net"}
    assert done[0]["result"]["verdict"]


def test_results_detail_and_report_after_a_run(client, fake_run, tmp_path):
    client.post("/api/run", json={"raw": "example.com"})
    read_sse(client)

    listed = client.get("/api/results").json()["results"]
    assert [r["domain"] for r in listed] == ["example.com"]

    detail = client.get("/api/detail/example.com")
    assert detail.status_code == 200
    assert "나이와 등록 정보" in detail.json()["html"]
    assert 'src="/captures/' in detail.json()["html"] or "저장된 캡쳐가 없습니다" in detail.json()["html"]

    built = client.post("/api/report")
    assert built.status_code == 200
    assert built.json()["url"] == "/report/index.html"
    assert (tmp_path / "data" / "report" / "index.html").exists()

    page = client.get("/report/index.html")
    assert page.status_code == 200
    assert "무위험 보증" in page.text


def test_detail_of_unknown_domain_is_404(client):
    assert client.get("/api/detail/never-checked.com").status_code == 404


def test_stop_and_resume(client, fake_run):
    client.post("/api/run", json={"raw": "example.com"})
    read_sse(client)

    stopped = client.post("/api/stop")
    assert stopped.status_code == 200
    assert "이어서" in stopped.json()["note"]

    resumed = client.post("/api/resume")
    assert resumed.status_code == 200
    assert resumed.json()["count"] == 1
    read_sse(client)


def test_resume_without_a_previous_list_is_rejected(client):
    assert client.post("/api/resume").status_code == 400


def test_capture_done_refreshes_the_stored_result(sample_result):
    """캡쳐가 붙은 최신본으로 갈아 끼워야 상세 화면에 사진이 보인다(심사 C1)."""
    manager = server.RunManager()
    before = judge(sample_result).model_dump(mode="json")
    manager.publish({"type": "domain_done", "domain": "example.com", "result": before})
    assert manager.results["example.com"]["captures"]["items"] == []

    after = {**before, "captures": {"check": {"status": "OK", "note": "1장 저장."}, "items": [
        {
            "label": "말기",
            "timestamp": "20240601000000",
            "url": "https://web.archive.org/web/20240601000000/http://example.com/",
            "file": "captures/example.com_20240601000000.png",
        }
    ]}}
    manager.publish({"type": "capture_done", "domain": "example.com", "shots": 1, "result": after})

    assert manager.results["example.com"]["captures"]["items"][0]["label"] == "말기"


def test_status_reports_resumable_from_the_saved_run_state(client, config_path):
    """앱을 껐다 켜도 중단된 목록이 남아 있으면 '이어서 검사'가 살아 있어야 한다(심사 C2)."""
    from domainchecker.pipeline import run_state_path

    assert client.get("/api/status").json()["resumable"] is False

    base = config_module.data_dir(config_module.load(config_path))
    run_state_path(base).write_text(
        json.dumps({"domains": ["example.com", "foo.net"]}, ensure_ascii=False), encoding="utf-8"
    )

    assert client.get("/api/status").json()["resumable"] is True


def test_resume_reads_the_saved_run_state_after_a_restart(client, config_path, fake_run):
    from domainchecker.pipeline import run_state_path

    base = config_module.data_dir(config_module.load(config_path))
    run_state_path(base).write_text(
        json.dumps({"domains": ["example.com", "foo.net"]}, ensure_ascii=False), encoding="utf-8"
    )

    resumed = client.post("/api/resume")  # 이 서버는 아직 한 번도 검사한 적이 없다
    assert resumed.status_code == 200
    assert resumed.json()["count"] == 2
    read_sse(client)


def test_damaged_run_state_is_ignored(client, config_path):
    from domainchecker.pipeline import run_state_path

    base = config_module.data_dir(config_module.load(config_path))
    run_state_path(base).write_text("{not json", encoding="utf-8")

    assert client.get("/api/status").json()["resumable"] is False
    assert client.post("/api/resume").status_code == 400


def test_external_access_requires_a_login(config_path, monkeypatch):
    """비밀번호를 정했을 때만 밖에서 접속할 수 있게 잠근다(키 노출 방지)."""
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", "열쇠말")
    with TestClient(create_app(config_path)) as guarded:
        blocked = guarded.get("/api/status")
        assert blocked.status_code == 401
        assert blocked.json()["login"] is True
        # 브라우저가 띄우는 회색 물음창이 다시 나오면 안 된다 — 그래서 이 머리글을 안 보낸다
        assert "WWW-Authenticate" not in blocked.headers
        wrong = guarded.post("/api/login", json={"user": "domainchecker", "password": "틀린값"})
        assert wrong.status_code == 401
        assert guarded.get("/api/status").status_code == 401
        right = guarded.post("/api/login", json={"user": "domainchecker", "password": "열쇠말"})
        assert right.status_code == 200
        assert guarded.get("/api/status").status_code == 200


def test_home_screen_app_files_are_served(client):
    """홈 화면에 담아 앱처럼 쓰려면 이 셋이 주소 맨 앞자리에서 나와야 한다."""
    page = client.get("/")
    assert 'rel="manifest" href="/manifest.webmanifest"' in page.text
    assert "serviceWorker.register" in page.text

    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    data = manifest.json()
    assert data["start_url"] == "/" and data["scope"] == "/"
    assert data["display"] == "standalone"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes  # 크롬이 설치 단추를 띄우는 최소 조건
    assert any("maskable" in (icon.get("purpose") or "") for icon in data["icons"])
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200

    worker = client.get("/sw.js")
    assert worker.status_code == 200
    # 범위가 좁아지면 앱 전체를 못 감싼다 / 심부름꾼이 캐시에 눌어붙으면 갱신이 막힌다
    assert worker.headers["Service-Worker-Allowed"] == "/"
    # 개발 중에는 위쪽 미들웨어가 no-store 로 한 번 더 덮는다 — 둘 다 "쟁여 두지 마라"다
    assert worker.headers["Cache-Control"] in {"no-cache", "no-store"}
    assert client.get("/favicon.ico").status_code == 200


def test_home_screen_app_files_stay_open_while_locked(config_path, monkeypatch):
    """잠가 둬도 표지(설명서·아이콘·심부름꾼)는 열려 있어야 설치 단추가 뜬다."""
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", "열쇠말")
    with TestClient(create_app(config_path)) as guarded:
        assert guarded.get("/api/status").status_code == 401  # 알맹이는 여전히 잠겨 있다
        for path in ("/manifest.webmanifest", "/sw.js", "/favicon.ico"):
            assert guarded.get(path).status_code == 200, path
        for icon in guarded.get("/manifest.webmanifest").json()["icons"]:
            assert guarded.get(icon["src"]).status_code == 200, icon["src"]


def test_serving_outside_localhost_without_a_password_refuses_to_start(monkeypatch):
    monkeypatch.setenv("DOMAINCHECKER_HOST", "0.0.0.0")
    monkeypatch.delenv("DOMAINCHECKER_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as raised:
        server.main()
    assert "접속 비밀번호" in str(raised.value)


def test_report_without_results_is_rejected(client):
    assert client.post("/api/report").status_code == 400


def test_report_and_detail_work_from_the_cache_alone(client, config_path, sample_result):
    """앱을 껐다 켠 뒤에도 예전에 검사해 둔 결과로 보고서를 만들 수 있어야 한다."""
    from domainchecker import cache

    base = config_module.data_dir(config_module.load(config_path))
    cache.save("example.com", judge(sample_result).model_dump(mode="json"), base)

    listed = client.get("/api/results").json()["results"]
    assert [r["domain"] for r in listed] == ["example.com"]

    assert client.get("/api/detail/example.com").status_code == 200
    built = client.post("/api/report")
    assert built.status_code == 200
    assert (base / "report" / "example.com.html").exists()


def test_run_queues_when_a_run_is_already_going(client, fake_run):
    """검사 중에 들어온 다음 묶음은 거절하지 않고 대기줄에 세운다."""
    manager = client.app.state.manager
    manager.running = True
    try:
        res = client.post("/api/run", json={"raw": "queued.com"})
        assert res.status_code == 200
        assert res.json()["queued"] is True
        assert manager.pending == [["queued.com"]]

        # 중단은 대기줄까지 함께 비운다 — 멈추라는 말은 전부 멈추라는 뜻이다
        client.post("/api/stop")
        assert manager.pending == []
    finally:
        manager.running = False


def test_queue_autostarts_the_next_batch(monkeypatch):
    """지금 검사가 끝나면 대기줄의 다음 묶음이 저절로 시작된다."""
    started: list[list[str]] = []

    class QuickPipeline:
        def __init__(self, config, on_event=None, **kwargs):
            self.on_event = on_event

        def stop(self):
            pass

        async def run(self, domains, use_cache=True):
            started.append(list(domains))
            self.on_event({"type": "finished", "done": len(domains), "total": len(domains), "stopped": False})
            return []

    monkeypatch.setattr(server, "Pipeline", QuickPipeline)

    async def scenario():
        manager = server.RunManager()
        manager.config_loader = Config
        await manager.start(Config(), ["a.com"], use_cache=True)
        first = manager.task
        manager.pending.append(["b.com"])
        await first
        # runner 의 뒷정리가 다음 묶음 task 를 만든다 — 그 task 가 잡힐 때까지 양보
        for _ in range(20):
            await asyncio.sleep(0)
            if manager.task is not first:
                break
        assert manager.task is not first
        await manager.task
        assert started == [["a.com"], ["b.com"]]
        assert manager.pending == []

    asyncio.run(scenario())


def test_clear_results_wipes_files_and_memory(client, fake_run, config_path):
    """지우기 단추 — 저장 파일·캐시·메모리가 다 비어야 하고, 검사 중에는 못 지운다."""
    client.post("/api/run", json={"raw": "example.com"})
    read_sse(client)
    assert client.get("/api/results").json()["results"]

    manager = client.app.state.manager
    manager.running = True
    assert client.delete("/api/results").status_code == 409
    manager.running = False

    res = client.delete("/api/results")
    assert res.status_code == 200
    assert res.json()["cleared"] is True
    assert client.get("/api/results").json()["results"] == []

    base = config_module.data_dir(config_module.load(config_path))
    assert not (base / "results.json").exists()
    assert not (base / "run_state.json").exists()


def test_purge_results_removes_only_the_named_domains(client, fake_run, sample_result, config_path):
    """고른 것만 지우기 — 남긴 도메인은 그대로 있고, 지운 것은 다시 켜도 안 살아난다."""
    client.post("/api/run", json={"raw": "example.com\nfoo.net"})
    read_sse(client)
    assert len(client.get("/api/results").json()["results"]) == 2

    # 검사가 끝나면 파이프라인이 남기는 두 자리(저장 파일·도메인별 캐시)를 그대로 깔아 둔다
    base = config_module.data_dir(config_module.load(config_path))
    payload = judge(sample_result).model_dump(mode="json")
    saved = [{**payload, "domain": "example.com"}, {**payload, "domain": "foo.net"}]
    (base / "results.json").write_text(
        json.dumps({"count": 2, "results": saved}, ensure_ascii=False), encoding="utf-8"
    )
    for one in saved:
        cache.save(one["domain"], one, base)
    (base / "captures").mkdir(parents=True, exist_ok=True)
    (base / "captures" / "foo.net_20200101.png").write_bytes(b"png")
    save_run_state(base, ["example.com", "foo.net"])

    manager = client.app.state.manager
    manager.running = True
    assert client.post("/api/results/purge", json={"domains": ["foo.net"]}).status_code == 409
    manager.running = False

    assert client.post("/api/results/purge", json={"domains": []}).status_code == 400

    res = client.post("/api/results/purge", json={"domains": ["foo.net"]})
    assert res.status_code == 200
    assert res.json()["removed"] == 1

    left = client.get("/api/results").json()["results"]
    assert [r["domain"] for r in left] == ["example.com"]

    # 저장 파일·캐시·화면 사진 세 자리에서 다 빠져야 앱을 다시 켰을 때도 안 돌아온다
    on_disk = json.loads((base / "results.json").read_text(encoding="utf-8"))
    assert [r["domain"] for r in on_disk["results"]] == ["example.com"]
    assert on_disk["count"] == 1
    assert not (base / "cache" / "foo.net.json").exists()
    assert (base / "cache" / "example.com.json").exists()
    assert not (base / "captures" / "foo.net_20200101.png").exists()
    # 이어서 검사 목록에도 남으면 안 된다 — 남으면 지운 줄이 다음 검사에 되살아난다
    assert load_run_state(base) == ["example.com"]


def test_clear_keys_removes_saved_keys(client, config_path, monkeypatch):
    """키 빼기 — 이름을 담아 보내면 그 키가 지워진다."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client.post("/api/config", json={"openrouter": "sk-or-secret-1234"})
    assert client.get("/api/config").json()["has_key"]["openrouter"] is True

    client.post("/api/config", json={"clear_keys": ["openrouter"]})
    data = client.get("/api/config").json()
    assert data["has_key"]["openrouter"] is False
    assert config_module.load(config_path).keys.openrouter == ""


def test_write_results_keeps_earlier_batches(config_path, sample_result):
    """묶음을 나눠 검사해도 results.json 이 지난 회차를 덮어쓰면 안 된다."""
    from domainchecker.pipeline import Pipeline

    config = config_module.load(config_path)
    pipe = Pipeline(config)
    first = judge(sample_result.model_copy(update={"domain": "first-batch.com"}))
    second = judge(sample_result.model_copy(update={"domain": "second-batch.com"}))
    pipe.write_results([first])
    pipe.write_results([second])

    data = json.loads(
        (config_module.data_dir(config) / "results.json").read_text(encoding="utf-8")
    )
    assert sorted(r["domain"] for r in data["results"]) == [
        "first-batch.com",
        "second-batch.com",
    ]
    assert data["count"] == 2


def test_results_list_unions_saved_file_and_cache(client, config_path, sample_result):
    """저장 파일과 캐시에 나뉘어 있어도 목록에는 둘 다 나와야 한다.

    예전에는 처음 걸리는 한 곳만 읽어서, 새 검사를 시작하는 순간
    지난 회차 결과가 화면에서 통째로 사라졌다.
    """
    base = config_module.data_dir(config_module.load(config_path))
    cached = judge(sample_result.model_copy(update={"domain": "cached-only.com"}))
    cache.save("cached-only.com", cached.model_dump(mode="json"), base)
    saved = judge(sample_result.model_copy(update={"domain": "saved-only.com"}))
    (base / "results.json").write_text(
        json.dumps({"results": [saved.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )

    listed = client.get("/api/results").json()["results"]
    assert sorted(r["domain"] for r in listed) == ["cached-only.com", "saved-only.com"]

"""서버 테스트 — 실 네트워크 호출 없음(파이프라인을 가짜로 바꿔치기)."""

import asyncio
import json
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from domainchecker import config as config_module
from domainchecker import server
from domainchecker.analyze.scoring import judge
from domainchecker.config import Config
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
    config = Config()  # 분당 30건, 스냅샷 6장, 캡쳐 켬
    numbers = estimate(config, 100)

    assert numbers["wayback_requests"] == 900  # CDX 1 + 본문 6 + 캡쳐 2
    assert numbers["minutes"] == pytest.approx(30.0)
    assert numbers["table_minutes"] == pytest.approx(23.3, abs=0.1)
    assert numbers["slow_minutes"] == pytest.approx(75.0)


def test_config_saves_keys_and_keeps_them_when_left_blank(client, config_path):
    saved = client.post(
        "/api/config",
        json={"serper": "serper-secret-1234", "speed_mode": "safe", "enable_capture": False},
    )
    assert saved.status_code == 200

    data = client.get("/api/config").json()
    assert data["has_key"]["serper"] is True
    assert data["keys_masked"]["serper"].endswith("1234")
    assert "serper-secret" not in json.dumps(data)  # 원문 키는 화면에 내려보내지 않는다
    assert data["speed_mode"] == "safe"
    assert data["enable_capture"] is False
    # Open PageRank 는 이제 선택 — ✅ 를 막는 것은 AI 키뿐이다.
    assert data["missing_keys"] == ["OpenRouter (AI 분석)"]

    # 빈 값으로 저장하면 기존 키가 살아 있어야 한다
    client.post("/api/config", json={"serper": "", "model": "deepseek/deepseek-v3.2"})
    again = client.get("/api/config").json()
    assert again["has_key"]["serper"] is True
    assert again["model"] == "deepseek/deepseek-v3.2"
    assert config_module.load(config_path).keys.serper == "serper-secret-1234"


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


def test_external_access_requires_a_password(config_path, monkeypatch):
    """비밀번호를 정했을 때만 밖에서 접속할 수 있게 잠근다(키 노출 방지)."""
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", "열쇠말")
    with TestClient(create_app(config_path)) as guarded:
        assert guarded.get("/api/status").status_code == 401
        allowed = guarded.get("/api/status", auth=("domainchecker", "열쇠말"))
        assert allowed.status_code == 200
        assert guarded.get("/api/status", auth=("domainchecker", "틀린값")).status_code == 401


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

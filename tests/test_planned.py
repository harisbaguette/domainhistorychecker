"""매입 예정 표시 — 저장·해제·지우기와의 얽힘을 시험한다."""

import pytest
from fastapi.testclient import TestClient

from domainchecker import cache
from domainchecker import config as config_module
from domainchecker.config import Config
from domainchecker.server import create_app


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    config_module.save(Config(data_dir=str(tmp_path / "data")), path)
    return path


@pytest.fixture
def client(config_path):
    with TestClient(create_app(config_path)) as test_client:
        yield test_client


def test_planned_starts_empty(client):
    assert client.get("/api/planned").json() == {"domains": []}


def test_planned_mark_and_unmark(client):
    res = client.post("/api/planned", json={"domain": "Good-One.com", "planned": True})
    assert res.status_code == 200
    assert res.json()["domains"] == ["good-one.com"]  # 소문자로 눕혀 저장

    # 같은 도메인을 두 번 넣어도 한 번만 산다
    client.post("/api/planned", json={"domain": "good-one.com", "planned": True})
    assert client.get("/api/planned").json()["domains"] == ["good-one.com"]

    client.post("/api/planned", json={"domain": "good-one.com", "planned": False})
    assert client.get("/api/planned").json()["domains"] == []


def test_planned_rejects_empty_domain(client):
    assert client.post("/api/planned", json={"domain": "  "}).status_code == 400


def test_planned_survives_restart(config_path, tmp_path):
    with TestClient(create_app(config_path)) as first:
        first.post("/api/planned", json={"domain": "keep.com", "planned": True})
    with TestClient(create_app(config_path)) as second:
        assert second.get("/api/planned").json()["domains"] == ["keep.com"]


def test_purge_removes_planned_mark(client, config_path, sample_result):
    """줄을 지우면 매입 예정 표시도 같이 사라진다 — 유령 표시 방지."""
    base = config_module.data_dir(config_module.load(config_path))
    cache.save("example.com", sample_result.model_dump(mode="json"), base)
    client.post("/api/planned", json={"domain": "example.com", "planned": True})

    res = client.post("/api/results/purge", json={"domains": ["example.com"]})
    assert res.status_code == 200
    assert client.get("/api/planned").json()["domains"] == []


def test_clear_results_clears_planned(client):
    client.post("/api/planned", json={"domain": "gone.com", "planned": True})
    assert client.delete("/api/results").status_code == 200
    assert client.get("/api/planned").json()["domains"] == []

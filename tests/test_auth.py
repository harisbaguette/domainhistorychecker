"""로그인 잠금 테스트 — 쪽지(쿠키) 발급·검증·만료·잠금 해제까지."""

import pytest
from fastapi.testclient import TestClient

from domainchecker import auth
from domainchecker import config as config_module
from domainchecker.config import Config
from domainchecker.server import create_app

ACCOUNT = {"user": "ghd12zxc", "password": "rudgh501"}


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    config_module.save(Config(data_dir=str(tmp_path / "data")), path)
    return path


@pytest.fixture
def locked(config_path, monkeypatch):
    monkeypatch.setenv("DOMAINCHECKER_USER", ACCOUNT["user"])
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", ACCOUNT["password"])
    with TestClient(create_app(config_path)) as client:
        yield client


def test_no_password_means_no_lock(config_path, monkeypatch):
    """내 컴퓨터에서 그냥 켤 때는 잠금이 없다 — 로그인 화면도 안 뜬다."""
    monkeypatch.delenv("DOMAINCHECKER_PASSWORD", raising=False)
    with TestClient(create_app(config_path)) as client:
        assert client.get("/api/status").status_code == 200
        landed = client.get("/login", follow_redirects=False)
        assert landed.status_code == 303
        assert landed.headers["location"] == "/"


def test_a_page_request_goes_to_the_login_screen(locked):
    blocked = locked.get("/api/config", headers={"accept": "text/html"}, follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/login?next=%2Fapi%2Fconfig"


def test_the_login_screen_and_its_look_are_open(locked):
    assert locked.get("/login").status_code == 200
    assert locked.get("/style.css").status_code == 200


def test_login_then_stay_logged_in(locked):
    done = locked.post("/api/login", json={**ACCOUNT, "remember": True})
    assert done.status_code == 200
    cookie = done.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert f"Max-Age={auth.REMEMBER_SECONDS}" in cookie
    assert locked.get("/api/status").status_code == 200
    assert locked.get("/api/config").json()["locked"] is True


def test_not_remembering_leaves_no_max_age(locked):
    """유지에 체크하지 않으면 창을 닫을 때 사라지는 쪽지를 준다."""
    done = locked.post("/api/login", json={**ACCOUNT, "remember": False})
    assert "Max-Age" not in done.headers["set-cookie"]
    assert locked.get("/api/status").status_code == 200


def test_a_wrong_user_name_is_refused(locked):
    assert locked.post("/api/login", json={"user": "남", "password": ACCOUNT["password"]}).status_code == 401
    assert locked.get("/api/status").status_code == 401


def test_logout_closes_the_door(locked):
    locked.post("/api/login", json=ACCOUNT)
    assert locked.get("/api/status").status_code == 200
    locked.post("/api/logout")
    assert locked.get("/api/status").status_code == 401


def test_a_forged_note_does_not_work(locked):
    locked.cookies.set(auth.COOKIE_NAME, "aaaa.bbbb")
    assert locked.get("/api/status").status_code == 401


def test_an_expired_note_does_not_work(locked, monkeypatch):
    monkeypatch.setenv("DOMAINCHECKER_USER", ACCOUNT["user"])
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", ACCOUNT["password"])
    locked.cookies.set(auth.COOKIE_NAME, auth.make_token(ACCOUNT["user"], -10))
    assert locked.get("/api/status").status_code == 401


def test_changing_the_password_kills_old_notes(locked, monkeypatch):
    locked.post("/api/login", json=ACCOUNT)
    assert locked.get("/api/status").status_code == 200
    monkeypatch.setenv("DOMAINCHECKER_PASSWORD", "새비밀번호")
    assert locked.get("/api/status").status_code == 401


def test_guessing_over_and_over_gets_blocked(locked):
    for _ in range(auth.Attempts.LIMIT):
        assert locked.post("/api/login", json={**ACCOUNT, "password": "틀림"}).status_code == 401
    # 잠긴 뒤에는 맞는 비밀번호를 넣어도 잠깐 안 받아 준다
    assert locked.post("/api/login", json=ACCOUNT).status_code == 429


def test_the_note_is_marked_secure_behind_https(locked):
    """인터넷(https)에 올린 곳에서는 쪽지가 암호화된 길로만 오가게 표시한다."""
    plain = locked.post("/api/login", json=ACCOUNT)
    assert "Secure" not in plain.headers["set-cookie"]  # 내 컴퓨터(http)에서는 붙이지 않는다
    behind_proxy = locked.post(
        "/api/login", json=ACCOUNT, headers={"x-forwarded-proto": "https"}
    )
    assert "Secure" in behind_proxy.headers["set-cookie"]

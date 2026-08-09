import json

from domainchecker import cache
from domainchecker.config import MODEL_CHAIN, ApiKeys, Config, load, save


def test_cache_roundtrip(tmp_path):
    assert cache.load("example.com", tmp_path) is None
    cache.save("example.com", {"domain": "example.com", "verdict": "BUY"}, tmp_path)

    assert cache.load("example.com", tmp_path)["verdict"] == "BUY"
    assert "example.com" in cache.cached_domains(tmp_path)


def test_damaged_cache_is_ignored(tmp_path):
    path = cache.cache_path("example.com", tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert cache.load("example.com", tmp_path) is None


def test_cache_filename_is_sanitised(tmp_path):
    path = cache.cache_path("../../evil.com", tmp_path)
    assert path.parent == cache.cache_dir(tmp_path)
    assert "/" not in path.name


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    path = tmp_path / "config.json"
    original = Config(keys=ApiKeys(openrouter="abc"), speed_mode="safe")
    save(original, path)

    loaded = load(path)
    assert loaded.keys.openrouter == "abc"
    assert loaded.start_rpm == 12
    assert json.loads(path.read_text(encoding="utf-8"))["speed_mode"] == "safe"


def test_missing_or_damaged_config_falls_back_to_defaults(tmp_path):
    assert load(tmp_path / "none.json").start_rpm == 30
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")
    assert load(broken).model == MODEL_CHAIN[0]


def test_model_chain_is_the_three_models_the_plan_fixed():
    """모델 이름이 조용히 바뀌면 실행 중 404로만 드러난다 — 여기서 못 박아 둔다."""
    assert MODEL_CHAIN == (
        "deepseek/deepseek-v4-flash-0731",
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v3.2",
    )


def test_model_chain_puts_the_chosen_model_first():
    config = Config(model=MODEL_CHAIN[2])
    chain = config.model_chain()
    assert chain[0] == MODEL_CHAIN[2]
    assert set(chain) == set(MODEL_CHAIN)


def test_key_comes_from_the_server_setting_when_the_file_has_none(tmp_path, monkeypatch):
    """배포한 서버는 설정 파일이 남지 않는다 — 환경변수에 넣어 둔 키를 써야 한다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    assert load(tmp_path / "none.json").keys.openrouter == "sk-from-env"


def test_a_key_typed_by_the_user_beats_the_server_setting(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    save(Config(keys=ApiKeys(openrouter="sk-typed")), path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    assert load(path).keys.openrouter == "sk-typed"


def test_the_server_setting_key_is_not_written_back_into_the_file(tmp_path, monkeypatch):
    """파일에 베껴 두면 나중에 키를 바꿔도 옛 키가 계속 이긴다 — 그래서 안 적는다."""
    path = tmp_path / "config.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    save(load(tmp_path / "none.json"), path)
    assert json.loads(path.read_text(encoding="utf-8"))["keys"]["openrouter"] == ""


def test_no_key_blocks_a_buy_verdict_any_more():
    """필수 검사는 전부 키 없이 돈다 — 키 하나 없다고 초록이 막히면 안 된다."""
    assert Config().missing_required_keys() == []


def test_free_fallbacks_are_listed_only_when_the_key_is_absent(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    notes = Config().free_fallbacks()
    assert len(notes) == 1
    assert "규칙 검사" in notes[0]
    assert Config(keys=ApiKeys(openrouter="c")).free_fallbacks() == []

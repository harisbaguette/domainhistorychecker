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


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    original = Config(keys=ApiKeys(serper="abc"), speed_mode="safe")
    save(original, path)

    loaded = load(path)
    assert loaded.keys.serper == "abc"
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


def test_no_key_blocks_a_buy_verdict_any_more():
    """필수 검사는 전부 키 없이 돈다 — 키 하나 없다고 초록이 막히면 안 된다."""
    assert Config().missing_required_keys() == []


def test_free_fallbacks_are_listed_only_when_the_key_is_absent():
    notes = Config().free_fallbacks()
    assert len(notes) == 3
    assert any("공개 크롤 기록" in n for n in notes)
    assert any("규칙 검사" in n for n in notes)
    assert Config(
        keys=ApiKeys(serper="a", openpagerank="b", openrouter="c")
    ).free_fallbacks() == []

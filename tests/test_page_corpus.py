"""실제 옛 화면 18장으로 재는 오탈락·놓침 시험 — 네트워크 없이 돈다.

자료는 전부 웨이백 머신에서 받아 온 원본이다(`tests/fixtures/pages/README.md` 에
주소와 정답표). 파일 이름 앞머리가 정답이다: `ok_` 는 멀쩡한 사이트, `spam_` 은
진짜 스팸 운영.

여기서 지키는 약속은 **오탈락 0** 이다. 이 도구의 목적은 살 만한 도메인을 놓치지
않는 것이라, 멀쩡한 도메인을 잘못 버리는 것이 가장 비싼 실패다.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

from domainchecker.analyze import rules
from domainchecker.analyze.ai import render_snapshot
from domainchecker.analyze.extract import SnapshotContent, parse_snapshot
from domainchecker.analyze.rules import distinct_lines

PAGES = Path(__file__).parent / "fixtures" / "pages"

# 링크를 어느 쪽으로 세는지가 달라지므로 파일마다 원래 살던 집을 알려 준다.
HOSTS = {
    "photodiary": "photodiary.co.kr",
    "naver": "naver.com",
    "yahoo": "yahoo.com",
    "dmoz": "dmoz.org",
    "slashdot": "slashdot.org",
    "hackernews": "news.ycombinator.com",
    "freeforall": "freeforall.net",
    "ffanet": "ffanet.com",
    "addfreeurl": "add-free-url.com",
    "linkomatic": "linkomatic.com",
    "viagra": "buy-cheap-viagra-online.com",
}


def load_page(name: str) -> SnapshotContent:
    """저장해 둔 원본 화면 한 장을 그대로 뜯어 준다(옛 한국 사이트는 euc-kr)."""
    path = PAGES / name
    raw = path.read_bytes()
    head = raw[:2000].lower()
    encoding = "euc-kr" if (b"euc-kr" in head or b"ks_c_5601" in head) else "utf-8"
    html = raw.decode(encoding, errors="replace")
    host = next((h for key, h in HOSTS.items() if key in name), "")
    stamp = "".join(c for c in name if c.isdigit())[:4] or "2001"
    return parse_snapshot(html, f"{stamp}0101000000", base_domain=host)


def all_pages() -> list[str]:
    return sorted(p.name for p in PAGES.glob("*.html"))


NORMAL = [n for n in all_pages() if n.startswith("ok_")]
SPAM = [n for n in all_pages() if n.startswith("spam_")]


def test_the_corpus_is_actually_there():
    """정상·스팸 각각 다섯 장 이상은 있어야 이 시험이 뜻을 갖는다."""
    assert len(NORMAL) >= 5, NORMAL
    assert len(SPAM) >= 5, SPAM


# ── 이 버그의 현장 ────────────────────────────────────────────────────────────


def test_photodiary_teacher_list_is_not_a_doorway():
    """2001년 '선생님께' 편지 목록 — 같은 링크 글귀가 28줄 쌓인 멀쩡한 목록 화면.

    예전에는 링크 글귀까지 본문에 섞어 한 줄로 만드는 바람에 '홍선생님께' 낱말이
    본문의 39%가 되어 도어웨이로 찍혔고, 같은 납작한 글을 받은 AI 도 같은 인용을
    들고 스팸이라 답해 최종 제외(REJECT)가 나왔다.
    """
    snap = load_page("ok_photodiary_2001_teacher_list.html")
    assert snap.link_texts.count("홍선생님께") >= 20, "시험 자료가 바뀌었다"
    assert rules.doorway_reason(snap) == ""
    assert rules.analyze([snap]).spam_operation is False


def test_photodiary_teacher_list_keeps_its_prose_and_its_list_apart():
    """본문에는 사람이 쓴 안내글이 남고, 링크로 걸린 글귀는 빠져야 한다."""
    snap = load_page("ok_photodiary_2001_teacher_list.html")
    assert "조용히 상담할수 있는 코너입니다" in snap.body_text, "사람이 쓴 안내글이 사라졌다"
    assert "홍선생님께" in snap.link_texts
    # 링크로 걸린 28줄은 본문에서 빠진다. 링크가 아닌 채로 남은 줄은 목록의 줄이라,
    # 도어웨이 검사가 같은 줄을 한 번만 세면서 걸러 낸다.
    assert snap.body_text.count("홍선생님께") < 60, "링크로 걸린 글귀가 본문에 그대로 남았다"


def test_repeated_rows_are_counted_once():
    """같은 줄이 서른 번 그려져도 낱말 편중은 한 줄만큼만 쳐야 한다."""
    snap = load_page("ok_photodiary_2001_teacher_list.html")
    rows = [line for line in snap.body_text.split("\n") if line]
    assert len(rows) > 3 * len(set(rows)), "시험 자료가 바뀌었다 — 같은 줄이 반복돼야 한다"
    words = re.findall(r"[가-힣A-Za-z]{2,}", distinct_lines(snap.body_text).lower())
    top = Counter(words).most_common(1)[0][1] / len(words)
    assert top < 0.15, f"줄을 접고도 낱말 편중이 {top:.0%}"


def test_the_ai_is_told_the_list_is_a_list():
    """AI 도 규칙 검사와 같은 사실을 봐야 한다 — 안 그러면 둘이 함께 속는다."""
    snap = load_page("ok_photodiary_2001_teacher_list.html")
    block = render_snapshot(snap, 3000)
    assert "[본문]" in block
    assert "링크 글귀" in block
    assert "홍선생님께×" in block, "같은 글귀가 몇 번 나왔는지로 접어서 보여 줘야 한다"


# ── 오탈락 0 — 멀쩡한 사이트를 스팸으로 찍지 않는가 ──────────────────────────


@pytest.mark.parametrize("name", NORMAL)
def test_a_normal_page_is_never_marked_as_spam_operation(name):
    snap = load_page(name)
    findings = rules.analyze([snap])
    assert findings.spam_operation is False, f"{name} 오탈락: {findings.evidence}"


@pytest.mark.parametrize("name", NORMAL)
def test_a_normal_page_is_never_called_parking(name):
    snap = load_page(name)
    assert snap.parking is False, f"{name} 를 파킹으로 오차단: {snap.parking_marks}"


# ── 놓침 — 진짜 스팸을 잡던 것은 계속 잡아야 한다 ────────────────────────────

# 지금 규칙 검사(세는 검사)가 잡아내는 스팸 화면. 나머지 스팸은 낱말을 세서는
# 멀쩡한 디렉터리(dmoz·야후·네이버)와 구분되지 않아 AI 의 뜻 읽기 몫이다 —
# 실측으로 확인한 사실이므로, 여기 적힌 것만은 절대 놓치면 안 된다.
CAUGHT_BY_RULES = [
    "spam_freeforall_1999_home.html",
    "spam_viagra_2009_en.html",
    "spam_viagra_2009_cialis.html",
]


@pytest.mark.parametrize("name", CAUGHT_BY_RULES)
def test_real_spam_is_still_caught(name):
    snap = load_page(name)
    findings = rules.analyze([snap])
    assert findings.spam_operation is True, f"{name} 을 놓쳤다"


def test_a_link_farm_has_almost_no_pages_of_its_own():
    """링크 농장은 남의 집으로만 내보낸다 — 제 사이트가 없다."""
    farm = load_page("spam_freeforall_1999_home.html")
    assert farm.external_links >= 50
    assert farm.internal_links < 5
    assert rules.analyze([farm]).link_farm is True


def test_a_busy_front_page_is_not_a_link_farm():
    """바깥으로 링크를 잔뜩 내보내도 제 페이지가 많으면 링크 농장이 아니다."""
    for name in ("ok_naver_2001_home.html", "ok_slashdot_2001_index.html"):
        snap = load_page(name)
        assert snap.external_links >= 50, f"{name} 시험 자료가 바뀌었다"
        assert rules.analyze([snap]).link_farm is False, name


def test_a_navigation_menu_is_not_a_keyword_list():
    """'뉴스 | 쇼핑 | 증권 | 취업' 같은 차림표는 키워드 나열이 아니다.

    예전 규칙은 바로 이 자리를 잡아 네이버 2001년 첫 화면과 해커뉴스 첫 화면을
    도어웨이로 찍었다 — 링크 차림표를 본문인 줄 알았기 때문이다.
    """
    for name in ("ok_naver_2001_home.html", "ok_hackernews_2007_news.html"):
        assert rules.doorway_reason(load_page(name)) == "", name


# ── 이웃 수리 — 같은 뿌리에서 생기던 다른 오판 ──────────────────────────────


def test_language_is_read_from_the_prose_not_from_the_menu():
    """차림표가 영어라고 영어 사이트로 찍으면 안 된다."""
    assert load_page("ok_naver_2001_home.html").lang == "ko"
    assert load_page("ok_photodiary_2001_teacher_list.html").lang == "ko"


def test_two_board_pages_are_not_called_autogenerated():
    """같은 게시판의 1쪽과 2쪽은 머리말·꼬리말이 같지만 자동 생성이 아니다."""
    first = load_page("ok_photodiary_2002_board_list.html")
    second = load_page("ok_photodiary_2002_board_list_p2.html")
    second.timestamp = "20050101000000"  # 해가 달라야 이 검사가 돈다
    assert rules.duplicate_pair([first, second]) is None


def test_really_autogenerated_pages_are_still_caught():
    """자동으로 찍어낸 목록 두 장은 본문까지 같으므로 그대로 잡힌다."""
    left = load_page("spam_addfreeurl_2009_list_a.html")
    right = load_page("spam_addfreeurl_2009_list_h.html")
    right.timestamp = "20120101000000"
    assert rules.duplicate_pair([left, right]) is not None

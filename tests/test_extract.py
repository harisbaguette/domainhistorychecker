from domainchecker.analyze.extract import (
    detect_language,
    extract_body_text,
    extract_text,
    is_parking,
    parse_snapshot,
)

PARKING_HTML = """
<html><head><title>example.com</title></head>
<body><h1>This domain is for sale</h1><p>Related searches</p></body></html>
"""

REAL_HTML = """
<html><head><title>동네 빵집 이야기</title></head>
<body>
  <script>var a=1;</script>
  <p>오늘은 통밀 식빵을 구웠습니다. 반죽은 하루 저온 숙성했고 버터는 넉넉히 넣었어요.</p>
  <a href="https://other.com/a">외부</a>
  <a href="/inside">내부</a>
</body></html>
"""


def test_extract_text_drops_scripts_and_collapses_space():
    text = extract_text(REAL_HTML)
    assert "var a=1" not in text
    assert "통밀 식빵" in text
    assert "  " not in text


def test_extract_text_respects_limit():
    html = "<html><body>" + ("가" * 9000) + "</body></html>"
    assert len(extract_text(html, limit=6000)) == 6000


def test_parking_page_is_detected():
    parked, marks = is_parking(PARKING_HTML, extract_text(PARKING_HTML))
    assert parked is True
    assert marks


def test_real_page_is_not_parking():
    parked, _ = is_parking(REAL_HTML, extract_text(REAL_HTML))
    assert parked is False


def test_language_detection():
    assert detect_language("오늘은 통밀 식빵을 구웠습니다. " * 5) == "ko"
    assert detect_language("これはテストです。日本語の文章をたくさん書きます。" * 3) == "ja"
    assert detect_language("This is an ordinary English sentence about baking bread. " * 3) == "en"
    assert detect_language("짧음") == "unknown"


def test_parse_snapshot_counts_links_against_the_real_domain():
    snap = parse_snapshot(
        REAL_HTML,
        "20180101120000",
        "https://web.archive.org/web/20180101120000id_/http://mysite.com/",
        base_domain="mysite.com",
    )
    assert snap.title == "동네 빵집 이야기"
    assert snap.lang == "ko"
    assert snap.external_links == 1  # other.com only; /inside 는 내부
    assert snap.internal_links == 1
    assert snap.parking is False
    assert snap.year == 2018


def test_hidden_text_marks_are_captured():
    html = '<html><body><div style="display:none">카지노 카지노 카지노</div>본문</body></html>'
    snap = parse_snapshot(html, "20150101000000", base_domain="x.com")
    assert snap.hidden_marks


# ── 오차단 시험 — 멀쩡한 사이트를 파킹으로 찍지 않는가 ────────────────────────
# 낱말 하나로 뜻을 넘겨짚던 옛 목록은 아래 세 가지를 전부 파킹으로 잘못 찍었다.

NAVER_BLOG_HTML = """
<html><body><h1>제주도 3박4일 여행기</h1>
<p>이번 여름 제주도를 다녀왔습니다. 성산일출봉과 우도를 돌았고 흑돼지도 먹었습니다.</p>
<div class="widget"><h3>관련 검색어</h3><a>제주도맛집</a><a>제주도숙소</a></div>
</body></html>
"""

ENGLISH_NEWS_HTML = """
<html><body><h1>City council approves new budget</h1>
<p>The council voted 7-2 on Tuesday to approve the annual budget.</p>
<aside><h4>Related searches</h4><a>budget 2019</a></aside></body></html>
"""

HOSTING_COMPANY_HTML = """
<html><body><h1>회사 소개</h1>
<p>저희는 웹호스팅 업체입니다. 고객사 중에는 afternic 같은 곳도 있습니다.</p>
</body></html>
"""

SPONSORED_PARKING_HTML = """
<html><body><h1>example.com</h1><p>Related Searches</p>
<ul><li>Insurance</li><li>Loans</li></ul><p>Sponsored Listings</p></body></html>
"""


def test_a_blog_with_a_related_searches_widget_is_not_parking():
    """네이버 블로그가 늘 다는 '관련 검색어' 위젯만 보고 파킹이라 하면 안 된다."""
    parked, marks = is_parking(NAVER_BLOG_HTML, extract_text(NAVER_BLOG_HTML))
    assert parked is False, f"멀쩡한 블로그를 파킹으로 오차단했다: {marks}"


def test_english_news_with_a_related_searches_widget_is_not_parking():
    parked, marks = is_parking(ENGLISH_NEWS_HTML, extract_text(ENGLISH_NEWS_HTML))
    assert parked is False, f"멀쩡한 뉴스를 파킹으로 오차단했다: {marks}"


def test_merely_naming_a_domain_seller_in_prose_is_not_parking():
    """업체 이름은 주소 자리에서만 본다 — 글에서 언급했다고 파킹이 아니다."""
    parked, marks = is_parking(HOSTING_COMPANY_HTML, extract_text(HOSTING_COMPANY_HTML))
    assert parked is False, f"업체 이름을 언급한 글을 파킹으로 오차단했다: {marks}"


def test_an_ad_only_screen_with_several_widget_labels_is_still_parking():
    """글은 없고 광고 딱지만 여러 개 겹친 화면은 그대로 잡아야 한다."""
    parked, marks = is_parking(SPONSORED_PARKING_HTML, extract_text(SPONSORED_PARKING_HTML))
    assert parked is True
    assert marks


def test_both_parking_checkers_agree():
    """옛 화면 판정과 살아 있는 페이지 판정이 같은 목록을 쓰는지 — 예전에는 갈라져 있었다."""
    from domainchecker.clients import freeindex

    for html in (PARKING_HTML, NAVER_BLOG_HTML, ENGLISH_NEWS_HTML, SPONSORED_PARKING_HTML):
        text = extract_text(html)
        assert is_parking(html, text)[0] == freeindex.is_parking_page(text), html[:40]


# ── 본문과 링크 글귀를 나눈 뒤에 달라지는 것들 ────────────────────────────────

LINK_HEAVY_PARKING_HTML = (
    "<html><body><h1>example.com</h1><p>Related Searches</p><p>Sponsored Listings</p><ul>"
    + "".join(
        f'<li><a href="http://ads.example/{i}">저렴한 자동차 보험 비교 견적 신청 바로가기 {i}</a></li>'
        for i in range(40)
    )
    + "</ul></body></html>"
)

KOREAN_PAGE_WITH_ENGLISH_MENU_HTML = (
    "<html><body><nav>"
    + "".join(f'<a href="/m{i}">Home Products Company Support Contact News Blog {i}</a>' for i in range(20))
    + "</nav><p>오늘은 통밀 식빵을 구웠습니다. 반죽은 하루 저온 숙성했고 버터는 넉넉히 넣었어요. "
    "아침 일찍 오신 손님들께 갓 구운 빵을 내어 드렸습니다.</p></body></html>"
)


def test_link_labels_leave_the_body_but_stay_in_the_link_list():
    snap = parse_snapshot(REAL_HTML, "20180101000000", base_domain="mysite.com")
    assert "통밀 식빵" in snap.body_text
    assert "외부" not in snap.body_text
    assert "외부" in snap.link_texts
    assert snap.link_targets == ["https://other.com/a", "/inside"]


def test_an_ad_screen_buried_under_link_labels_is_still_parking():
    """광고 링크 글귀가 길어도 '읽을 글이 없다'는 사실은 그대로다.

    예전에는 링크 글귀까지 합쳐 글 길이를 재는 바람에, 광고만 깔린 파킹 화면이
    "글이 400자를 넘는다"가 되어 이 검사를 통째로 빠져나갔다.
    """
    text = extract_text(LINK_HEAVY_PARKING_HTML)
    body = extract_body_text(LINK_HEAVY_PARKING_HTML)
    assert len(text) > 400, "시험 자료가 바뀌었다 — 링크 글귀로 글이 길어져야 한다"
    assert is_parking(LINK_HEAVY_PARKING_HTML, text)[0] is False, "옛 잣대로는 못 잡던 화면"
    assert is_parking(LINK_HEAVY_PARKING_HTML, text, body)[0] is True


def test_language_comes_from_the_prose_not_the_menu():
    """차림표가 영어라고 영어 사이트로 찍으면 안 된다."""
    snap = parse_snapshot(KOREAN_PAGE_WITH_ENGLISH_MENU_HTML, "20180101000000", base_domain="x.kr")
    assert detect_language(snap.text) == "en", "시험 자료가 바뀌었다 — 납작하게 보면 영어여야 한다"
    assert snap.lang == "ko"


def test_a_page_that_is_nothing_but_links_still_reaches_the_language_check():
    """본문이 아예 없으면 링크 글귀까지 합쳐 다시 본다 — '알 수 없음'으로 버리지 않는다."""
    html = "<html><body>" + "".join(
        f'<a href="/{i}">오늘의 날씨와 교통 정보를 확인하세요 {i}</a>' for i in range(10)
    ) + "</body></html>"
    snap = parse_snapshot(html, "20180101000000", base_domain="x.kr")
    assert snap.body_text == ""
    assert snap.lang == "ko"

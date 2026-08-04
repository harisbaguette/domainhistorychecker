from domainchecker.analyze.extract import (
    detect_language,
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

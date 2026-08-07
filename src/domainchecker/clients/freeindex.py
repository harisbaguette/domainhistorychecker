"""키 없이 도는 색인 검사 — 커먼크롤 공개 색인 + 도메인 현재 페이지.

Serper(유료 키)가 없을 때 이 검사가 대신 돈다. 공개 자료 두 가지를 합친다.
  1) 커먼크롤 색인 — 누구나 키 없이 조회할 수 있는 세계 크롤 기록. 이 도메인 밑에
     어떤 주소가 남아 있었는지 알려 준다(제목·요약은 없다).
  2) 도메인 현재 페이지 — 지금 실제로 열어 본다. 파킹(판매용 빈 페이지)인지,
     위험 업종 문구가 남아 있는지는 이쪽이 가장 정확하다.

둘 다 아무것도 못 건지면 "확인"이라고 말하지 않는다 — 못 본 것을 깨끗한 것으로
읽히게 두면, 이 검사를 통과했다는 이유로 초록 도장이 나가 버린다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

from ..analyze.extract import extract_text
from ..models import CheckState, CheckStatus, IndexInfo
from .serper import PARKING_PHRASES_KO, PARKING_TERMS, contamination_hits

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
COLLINFO_TTL = 24 * 3600  # 크롤 회차는 하루에 한 번만 다시 확인한다
CRAWL_LIMIT = 200  # 한 도메인에서 받아 올 최대 주소 수
BODY_LIMIT = 1_000_000  # 남의 서버에서 받아 올 최대 바이트(폭탄 응답 방어)
MAX_HOPS = 5  # 넘겨보내기(리다이렉트)를 몇 번까지 따라갈지
TEXT_LIMIT = 6_000

# 기본 User-Agent 로는 문 앞에서 막는 사이트가 많아 평범한 브라우저처럼 보이게 한다.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 도메인 판매·주차 업체. 현재 페이지가 이쪽으로 넘어가면 파킹으로 본다.
PARKING_HOSTS = (
    "sedo.com",
    "sedoparking.com",
    "afternic.com",
    "dan.com",
    "hugedomains.com",
    "bodis.com",
    "parkingcrew.net",
    "parklogic.com",
    "above.com",
    "undeveloped.com",
    "domainmarket.com",
    "buydomains.com",
    "namedrive.com",
    "skenzo.com",
    "voodoo.com",
)

# 살아 있는 페이지 본문(수천 자)에는 낱말 하나짜리 표지를 쓰지 않는다.
# "Where I parked my car" 같은 평범한 문장이 파킹으로 잡히기 때문이다.
# 업체 이름(sedo·afternic)은 PARKING_HOSTS 에서 주소로 잡으므로 여기서 뺀다.
_LIVE_PARKING_RE = re.compile(
    "|".join(
        rf"\b{re.escape(term)}\b" for term in PARKING_TERMS if " " in term or "." in term
    ),
    re.IGNORECASE,
)

# 사설망·내 컴퓨터를 가리키는 이름. 넘겨보내기를 타고 집 안 기기를 긁지 않게 막는다.
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".home", ".lan")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS = re.compile(r"\s+")

# 최신 크롤 회차 주소는 회차마다 바뀌므로 한 번 받아 두고 하루 동안 재사용한다.
_collection_cache: dict[str, object] = {}
_collection_lock = asyncio.Lock()


def reset_collection_cache() -> None:
    """테스트용 — 기억해 둔 크롤 회차 주소를 지운다."""
    _collection_cache.clear()


async def latest_collection(http: httpx.AsyncClient) -> str:
    """가장 최근 크롤 회차의 조회 주소. 못 받으면 빈 문자열."""
    async with _collection_lock:  # 검사 여러 개가 동시에 물어보지 않게
        cached = str(_collection_cache.get("api", ""))
        stamped = float(_collection_cache.get("at", 0) or 0)
        if cached and (time.time() - stamped) < COLLINFO_TTL:
            return cached
        try:
            response = await http.get(COLLINFO_URL, timeout=20.0)
        except httpx.HTTPError:
            return cached
        if response.status_code != 200:
            return cached
        try:
            rows = response.json()
        except ValueError:
            return cached
        if not isinstance(rows, list):
            return cached
        for row in rows:
            api = str(row.get("cdx-api", "")).strip() if isinstance(row, dict) else ""
            if api:
                _collection_cache.update({"api": api, "at": time.time()})
                return api
        return cached


async def crawl_urls(domain: str, http: httpx.AsyncClient) -> tuple[list[str], bool]:
    """커먼크롤에 남아 있는 이 도메인의 주소들. (주소 목록, 조회 성공 여부)."""
    api = await latest_collection(http)
    if not api:
        return [], False
    # `*` 를 그대로 보내면 400 이 떨어진다 — 반드시 %2A 로 적어야 한다.
    query = f"{api}?url={quote(domain, safe='')}%2F%2A&output=json&limit={CRAWL_LIMIT}"
    try:
        response = await http.get(query, timeout=40.0)
    except httpx.HTTPError:
        return [], False
    if response.status_code == 404:
        return [], True  # 색인에 없다는 정상 답 = 0건
    if response.status_code != 200:
        return [], False
    urls = []
    for line in response.text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        url = str(row.get("url", "")).strip() if isinstance(row, dict) else ""
        if url:
            urls.append(url)
    # 같은 주소가 여러 번 잡히면 "남은 페이지가 많다"로 부풀려 읽힌다.
    return list(dict.fromkeys(urls)), True


def host_is_reachable(url: str) -> bool:
    """공개 인터넷 주소인가. 내 컴퓨터·집 안 기기로 넘어가려 하면 막는다.

    simplify: 이름을 주소로 풀어 보지는 않는다(검사 한 건마다 이름 조회가 붙고,
    시험이 인터넷에 매이기 때문). 숫자 주소와 뻔한 집 안 이름만 막는다 — 이름이
    사설 주소를 가리키도록 꾸민 경우까지 막으려면 여기에 이름 조회를 더하면 된다.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # 보통의 도메인 이름
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def live_page(domain: str, http: httpx.AsyncClient) -> dict:
    """도메인 첫 페이지를 지금 열어 본다. 아예 못 붙으면 빈 dict.

    본문을 못 읽어도(오류 화면·봇 차단) 어디로 넘어갔는지는 그대로 쓴다 —
    파킹은 대개 판매 업체로 넘기는 것으로 드러나고, 그 업체 쪽이 사람 확인을
    요구해 막히는 일이 흔하기 때문이다.
    """
    fallback: dict = {}
    for scheme in ("https", "http"):
        page = await _fetch(f"{scheme}://{domain}/", http)
        if page.get("ok"):
            return page
        fallback = fallback or page
    return fallback


async def _fetch(url: str, http: httpx.AsyncClient) -> dict:
    """넘겨보내기를 한 칸씩 직접 따라간다(가기 전에 주소를 검사하기 위해서)."""
    for _ in range(MAX_HOPS):
        if not host_is_reachable(url):
            return {}
        try:
            async with http.stream(
                "GET",
                url,
                timeout=httpx.Timeout(15.0, connect=8.0),
                headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"},
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        return _failed_page(response.status_code, url)
                    url = str(response.url.join(location))
                    continue
                if response.status_code >= 400:
                    return _failed_page(response.status_code, url)
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) >= BODY_LIMIT:
                        break
                encoding = response.encoding or "utf-8"
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError, ValueError):
            return {}
        html = body.decode(encoding, errors="replace")
        found = _TITLE_RE.search(html)
        return {
            "ok": True,
            "status": 200,
            "final_url": url,
            "title": _WS.sub(" ", found.group(1)).strip()[:200] if found else "",
            "text": extract_text(html, TEXT_LIMIT),
        }
    return {}


def _failed_page(status: int, url: str) -> dict:
    return {"ok": False, "status": status, "final_url": url, "title": "", "text": ""}


def parking_host(url: str) -> str:
    """넘어간 주소가 도메인 판매 업체면 그 업체 이름을 돌려준다."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for seller in PARKING_HOSTS:
        if host == seller or host.endswith("." + seller):
            return seller
    return ""


def is_parking_page(text: str) -> bool:
    """살아 있는 페이지 본문이 '이 도메인 팝니다' 화면으로 읽히는가."""
    if _LIVE_PARKING_RE.search(text):
        return True
    return any(phrase in text for phrase in PARKING_PHRASES_KO)


def _path_text(urls: list[str]) -> str:
    """주소에서 도메인 이름을 뺀 경로 부분만. 도메인 이름 자체로 오폭하지 않게."""
    parts = []
    for url in urls:
        split = urlsplit(url)
        # %EC%B9%B4… 처럼 감싸인 한글은 풀어야 위험 업종 낱말이 보인다.
        raw = unquote(f"{split.path} {split.query}", errors="replace")
        parts.append(raw.replace("/", " ").replace("-", " ").replace("_", " "))
    return " ".join(parts)


async def check(domain: str, http: httpx.AsyncClient) -> IndexInfo:
    """Serper 대체 — 키 없이 색인·현재 상태를 확인한다."""
    result = IndexInfo()
    urls, crawl_ok = await crawl_urls(domain, http)
    page = await live_page(domain, http)
    seller = parking_host(page.get("final_url", "")) if page else ""

    # 커먼크롤도 못 보고 현재 페이지도 못 읽었으면 아는 게 없는 것이다.
    # 판매 업체로 넘어간 사실 하나만 알아도 그것은 훌륭한 답이므로 통과시킨다.
    if not crawl_ok and not page.get("ok") and not seller:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note="색인 검사를 못 했습니다 — 커먼크롤 조회도, 현재 페이지 열기도 실패했습니다.",
        )
        return result

    result.indexed_count = len(urls)
    live_text = ""
    if page.get("ok"):
        if page["title"]:
            result.titles = [page["title"]]
        live_text = f"{page['title']} {page['text']}"
    result.current_parking = bool(seller) or is_parking_page(live_text)

    # 본문을 오염 검사에서 빼는 것은 정말 판매 업체 사이트에 도착했을 때뿐이다.
    # "판매 중" 문구 한 줄로 본문 전체를 버리면, 그 아래 남은 위험 업종 문구를
    # 통째로 놓친다(Serper 쪽에서 이미 겪고 고친 구멍이다).
    haystack = "" if seller else live_text
    result.contamination_terms = contamination_hits(f"{haystack} {_path_text(urls)}")
    result.contaminated = bool(result.contamination_terms)

    notes = []
    if crawl_ok:
        more = "건 이상" if len(urls) >= CRAWL_LIMIT else "건"
        notes.append(f"커먼크롤에 남은 주소 {len(urls)}{more}")
    else:
        notes.append("커먼크롤 조회는 실패")
    if result.current_parking:
        notes.append(f"현재 파킹 페이지({seller})" if seller else "현재 파킹 페이지")
    elif page.get("ok"):
        notes.append("현재 페이지 정상")
    elif page:
        notes.append(f"현재 페이지가 오류({page['status']})로 응답")
    else:
        notes.append("현재 페이지는 열리지 않음")
    if result.contaminated:
        notes.append("위험 업종 문구 남아 있음")
    result.check = CheckState(
        status=CheckStatus.OK,
        note="구글 색인 대신 무료 공개 자료로 확인했습니다 — " + ", ".join(notes) + ".",
    )
    return result

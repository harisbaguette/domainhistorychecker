# 실제 옛 화면 시험 자료 (네트워크 없이 도는 회귀 시험용)

전부 **웨이백 머신에서 실제로 받아 온 원본 그대로**다(2026-08-23 수집, `id_` 원본
재생 주소). 손으로 지어낸 화면은 한 장도 없다. 파일 이름 앞머리가 정답표다 —
`ok_` = 멀쩡한 사이트, `spam_` = 진짜 스팸 운영.

인코딩은 원본 그대로 두었다(옛 한국 사이트는 euc-kr). 읽을 때는
`tests/test_page_corpus.py:load_page()` 가 문서에 적힌 인코딩을 보고 푼다.

| 파일 | 원본 주소 (웨이백 타임스탬프) | 무엇인가 |
| --- | --- | --- |
| ok_photodiary_2001_teacher_list.html | 20010503200035 / www.photodiary.co.kr/boards/tboards.phtml | **이 버그의 현장.** 초등학생 일기 서비스의 '선생님께' 편지 목록. `(3학년 1반) 홍선생님께` 링크가 28줄 |
| ok_photodiary_2002_board_list.html | 20020206022216 / www.photodiary.co.kr/board/free_list.php | 같은 사이트 자유게시판 목록 1쪽 |
| ok_photodiary_2002_board_list_p2.html | 20020206112523 / …/free_list.php?start=10 | 같은 게시판 2쪽 |
| ok_photodiary_2001_home.html | 20010501033653 / www.photodiary.co.kr/ | 첫 화면 |
| ok_photodiary_2001_aboutus.html | 20010503113220 / www.photodiary.co.kr/aboutus.phtml | 회사 소개 — 줄글이 있는 보통 페이지 |
| ok_naver_2001_home.html | 20010107183000 / naver.com | 네이버 첫 화면. 차림표 링크가 161개 |
| ok_yahoo_1999_home.html | 19990116224322 / yahoo.com | 야후 첫 화면 |
| ok_dmoz_2002_home.html | 20020123010656 / dmoz.org | 사람이 손으로 엮은 디렉터리 |
| ok_slashdot_2001_index.html | 20011002205626 / slashdot.org/index.pl?issue=20010911 | 뉴스 목록 + 줄글 |
| ok_hackernews_2007_news.html | 20070222081450 / news.ycombinator.com/news | 링크 목록 첫 화면 |
| spam_freeforall_1999_home.html | 19991127232156 / www.freeforall.net/ | FFA 링크 농장 — 바깥 링크 85개, 제 페이지는 1개 |
| spam_ffanet_1999_12level.html | 19990508122627 / www.ffanet.com/12level.htm | FFA 링크 농장의 다단계 수당표 |
| spam_addfreeurl_2007_home.html | 20070625170540 / www.add-free-url.com/ | 무료 링크 등록 디렉터리(SEO 링크 장사) |
| spam_addfreeurl_2009_list_a.html | 20090316225512 / …/?s=A&c=209 | 같은 곳의 자동 생성 목록(A) |
| spam_addfreeurl_2009_list_h.html | 20090316225516 / …/?s=H&c=209 | 같은 곳의 자동 생성 목록(H) |
| spam_linkomatic_2002_offer.html | 20020401152517 / www.linkomatic.com/4webhits/offer.html | 링크 대량 등록 판매 |
| spam_viagra_2009_en.html | 20091028124744 / buy-cheap-viagra-online.com/?lang=en | 불법 약품 판매 + 숨긴 글자 |
| spam_viagra_2009_cialis.html | 20091028061846 / …/?product=cialis | 같은 곳의 다른 상품 화면 |

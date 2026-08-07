# 글꼴 출처

## Gaegu (개구체) — 이 앱이 함께 배포하는 손글씨 글꼴

- 파일: `Gaegu-Bold.woff2` (207KB)
- 만든 곳: JIKJI SOFT · 라이선스: **SIL Open Font License 1.1** (`OFL.txt` 원문 동봉)
- 받은 곳: <https://github.com/google/fonts/tree/main/ofl/gaegu> (`Gaegu-Bold.ttf`)
- 손댄 것: 웹에서 빨리 받아지도록 `woff2` 로 바꾸고, 라틴 + 한글 음절 전 범위
  (`U+0020-007E, U+00A0-00FF, U+2000-206F, U+3000-303F, U+AC00-D7A3`)만 남겼다.
  글자 모양은 하나도 고치지 않았다. OFL 에 예약 글꼴 이름(Reserved Font Name) 조항이 없어
  이름을 그대로 `Gaegu` 로 둔다. OFL 은 이런 변형·재배포를 허용하며, 라이선스 원문을
  함께 배포할 것만 요구한다(그래서 `OFL.txt` 를 같이 둔다).
- 쓰는 자리: 디스플레이 전용(`--dw-font-display`) — 화면 제목 줄, 빈 화면 제목. 본문에는 쓰지 않는다.

## MemomentKkukkukk (메모먼트 꾹꾹체) — 파일을 넣지 않는다

DW 정본의 시그니처 글꼴이지만 **배포 파일에 넣지 않는다.**
배포처(눈누)의 허용 범위표에서 **임베딩(웹사이트·앱에 글꼴 파일을 탑재하는 것)이 "금지"**로 적혀 있다
(2026-08-07 확인, <https://noonnu.cc/font_page/1663> · 라이선스 원문
<https://mem0ment.notion.site/38ea7af66521805699d1e29efed3c920>).
대신 `static/app.css` 가 이 글꼴을 `local()` 로만 부른다 — 쓰는 사람 컴퓨터에 이미 깔려 있으면
그것이 나오고, 없으면 조용히 Gaegu 로 넘어간다. 파일을 받으러 가지 않으므로 404 도 나지 않는다.

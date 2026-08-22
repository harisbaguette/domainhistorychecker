# 글꼴 출처

## Paperlogy (페이퍼로지)

- 파일: `Paperlogy-4Regular.woff2` (428KB · 보통 굵기 400) · `Paperlogy-7Bold.woff2` (430KB · 굵게 700)
  두 벌 다 글자 14,098자가 들어 있다(한글 음절 전부 + 라틴 + 기호).
- 만든 곳: 페이퍼로지(발표 자료를 다루는 한국 유튜브 채널) — G마켓 산스의 한글과 Montserrat 의
  영문을 이어 붙여 만든 글꼴. 굵기는 얇은 것부터 아주 굵은 것까지 아홉 벌이 나와 있다.
- 받은 곳(공식): <https://freesentation.blog/paperlogy> 가 안내하는 공식 저장소
  <https://github.com/Freesentation/paperlogy> 의 `woff2/` 폴더에서 그대로 내려받았다
  (2026-08-22 확인). 손댄 것은 하나도 없다 — 글자를 덜어내지도, 다시 압축하지도 않은 원본 그대로다.
- 두 벌만 싣는 이유: 화면이 쓰는 굵기는 400·500·600·700·900 인데, 500·600 은 브라우저가 400 에서,
  900 은 700 에서 알아서 만들어 준다. 아홉 벌을 다 실으면 받을 것만 4MB 가까이 무거워진다.
- 쓰는 자리: 앱 전체(제목·본문 모두)와 보고서. `static/app.css` 의 `@font-face` 두 덩이가
  `Paperlogy` 라는 이름으로 등록하고, `--dw-font-display` · `--dw-font-body` 가 그 이름을 가리킨다.

### 라이선스 — 웹에 실어 보내도 된다

**SIL 오픈폰트라이선스(OFL) 1.1** 이다. 공식 저장소의 라이선스 파일
(<https://github.com/Freesentation/paperlogy/blob/main/OFL%20license.txt> · 2026-08-22 확인) 첫 줄과
OFL 원문에 이렇게 적혀 있다.

> Copyright 2024 The PAPERLOGY Authors (https://freesentation.blog/paperlogy)
> This Font Software is licensed under the SIL Open Font License, Version 1.1.

> The fonts, including any derivative works, can be bundled, embedded,
> redistributed and/or sold with any software provided that any reserved
> names are not used by derivative works.
> (글꼴을 소프트웨어에 함께 담고, 심고, 다시 나눠 주는 것이 모두 된다는 뜻)

배포처인 눈누(<https://noonnu.cc/font_page/1456> · 2026-08-22 확인)의 허용 범위표에도 같은 내용이
한국어로 적혀 있다.

> 웹사이트 및 프로그램 서버 내 폰트 탑재, E-book 제작 (허용)

> SIL 오픈폰트라이선스(OFL)에 따라 글꼴 단독 판매 또는 글꼴 라이선스 변경을 제외한
> 모든 상업적 행위 및 수정, 재배포가 가능합니다.

지키면 되는 것은 두 가지뿐이다. ① 글꼴 파일만 따로 떼어 파는 것은 안 된다(프로그램에 실어
보내는 것은 된다). ② 글자 모양을 고쳐 다시 내놓을 때 `Paperlogy` 라는 이름을 쓰면 안 된다.
이 프로젝트는 원본을 고치지 않고 그대로 싣기만 하므로 둘 다 걸리지 않는다.

### 예전에 쓰던 글꼴 (2026-08-22 퇴출)

메모먼트 꾹꾹체(`MemomentKkukkukk.woff2`)를 쓰다가 지웠다. 배포처(눈누) 허용 범위표에
**임베딩(앱·웹사이트에 글꼴 파일을 실어 보내는 것) 금지**로 적혀 있었기 때문이다
(<https://noonnu.cc/font_page/1663>). 운영자 지시로 페이퍼로지로 갈아탔다.

### 손대야 할 때

- 다른 글꼴로 갈아타려면: 새 woff2 를 이 폴더에 넣고, `static/app.css` 의 `@font-face` 두 덩이가
  가리키는 파일 이름과 `Paperlogy` 라는 이름, 그리고 `--dw-font-*` 두 줄을 함께 고친다.
  `static/sw.js` 의 미리 받아 두는 목록과 `report/html.py` 의 복사 목록에도 같은 파일 이름이 적혀 있다.
- 아예 빼려면: `@font-face` 두 덩이와 이 폴더의 woff2 를 지우면 Pretendard 계열 본문 글꼴로 돌아간다.

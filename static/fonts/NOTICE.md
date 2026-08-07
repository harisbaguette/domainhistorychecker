# 글꼴 출처

## MemomentKkukkukk (메모먼트 꾹꾹체)

- 파일: `MemomentKkukkukk.woff2` (1.9MB · 한글 음절 11,172자 + 라틴 95자 전부 들어 있다)
- 가져온 곳: `Z:\Doweek\design-system\assets\fonts\MemomentKkukkukk-subset.woff`
  (doweek 본체 앱이 `public/fonts/` 에서 쓰는 것과 같은 파일)
- 손댄 것: 글자 모양은 하나도 고치지 않았고, 웹에서 빨리 받아지도록 `woff2` 로 다시 압축만 했다
  (2.6MB → 1.9MB).
- 쓰는 자리: 앱 전체. doweek 본체(`src/index.css` 의 `--font-app`)가 제목만이 아니라 본문까지
  이 글꼴로 깔기 때문에, 이 도구도 같은 방식으로 쓴다.

### 라이선스에 대해 알고 있는 것

배포처(눈누)의 허용 범위표에는 **임베딩(앱·웹사이트에 글꼴 파일을 실어 보내는 것) 금지**로
적혀 있다(2026-08-07 확인 · <https://noonnu.cc/font_page/1663> ·
원문 <https://mem0ment.notion.site/38ea7af66521805699d1e29efed3c920>).

이 사실을 운영자에게 알린 뒤, **운영자가 "손글씨 폰트도 꼭 써야 함"이라고 지시하여 그대로 싣는다**
(2026-08-07). 밖으로 배포할 때는 원 배포처에 사용 허락을 따로 확인하는 것이 안전하다.
빼야 할 일이 생기면 `static/app.css` 의 `@font-face` 한 덩이와 이 폴더의 woff2 만 지우면 되고,
그러면 자동으로 Pretendard 계열 본문 글꼴로 돌아간다.

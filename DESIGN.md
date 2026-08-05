# 낙장도메인 품질 체커 — 디자인 정본 (DW 디자인 시스템 적용)

> 정본 상위 문서: `Z:\Doweek\design-system\DESIGN.md` (DW 디자인 시스템).
> 이 파일은 DW 토큰을 이 도구에 맞게 확정한 프로젝트 결정이다. 코드는 여기 적힌 토큰만 쓴다.
> 작성일: 2026-08-04

## 1. 핵심 사용자 · 목적 (R1)

- **사용자 1명**: 도메인을 대량으로 사서 사이트를 만들려는 비개발자(주부·초보 포함). 설명서 없이 써야 한다.
- **목적 1개**: 도메인 목록을 붙여 넣고 → "사도 되는 것"을 초록으로 골라 받는다.
- 화면당 기능 1개: ① 검사(입력→진행→결과 표) ② 상세(근거 읽기) ③ 설정(키 넣기). 이미 분리돼 있음 — 유지.

## 2. 컨셉 — 종이 수첩 위의 검수 도장

DW의 종이 수첩 컨셉을 그대로 잇는다: 따뜻한 종이 배경 위 흰 카드, 만년필 잉크 파랑.
**시그니처 순간 1개**: 판정 열의 "도장" 배지 — 검수원이 종이 서류에 도장을 찍듯,
판정(매입 후보/검토 필요/제외)이 틴트 면+진한 잉크 글자의 둥근 도장으로 찍힌다.
`--dw-shadow-glass`(떠 있는 종이 그림자)를 카드에 써서 DW 두 번째 시그니처도 잇는다.

## 3. 정본 준수 방식 — DW 부품 CSS를 원본 그대로 가져다 쓴다

값을 베끼지 않는다. DW 정본 파일을 **한 글자도 고치지 않고 복사**해 `static/dw/` 에 두고 그대로 불러 쓴다.
DW가 갱신되면 같은 자리에 다시 덮어쓰면 끝이다(복사 방법은 `static/dw/README.md`).

```
static/dw/tokens.css      ← Z:\Doweek\design-system\tokens\tokens.css   (편집 금지)
static/dw/base.css        ← Z:\Doweek\design-system\styles\base.css     (편집 금지)
static/dw/ui/<이름>.css   ← Z:\Doweek\design-system\ui\<이름>\<이름>.css (편집 금지, 이 앱이 쓰는 15종만)
static/app.css            ← 이 앱만의 레이아웃(DW 부품이 덮지 못하는 것만). 이 프로젝트 소유
```

`src/domainchecker/report/html.py` 가 위 파일들을 `tokens → base → ui/*(이름순) → app.css` 순서로 읽어
하나로 잇고(설명 주석은 빼고), 서버는 그것을 `/style.css` 로 내려준다. 화면과 보고서가 같은 CSS를 쓴다.
보고서 HTML은 같은 내용을 `<style>` 로 통째로 끼워 넣는다 — zip을 풀어 파일 하나만 열어도 모양이 나와야 하고,
한 장만 떼어 남에게 보내는 일도 있기 때문이다.

**규칙**: 마크업은 DW 부품 클래스(`dw-*`)와 `data-*` 속성으로만 쓰고, 색·모서리·그림자·간격은 `var(--dw-*)` 로만
쓴다. hex 직접 사용 0. 토큰 이름도 DW 원본 그대로다(`--dw-ring`, `--dw-font-body`, `--dw-space-*` …).

가져다 쓴 DW 부품 15종:
`alert` `badge` `button` `card` `checkbox` `collapsible` `empty-state` `field` `input` `label`
`native-select` `progress` `table` `tabs` `textarea`. 안 쓰는 45종은 zip 무게 때문에 복사하지 않았다.

## 4. 타이포

- 본문: `--dw-font-body`(Pretendard → Apple SD Gothic Neo → Malgun Gothic → system-ui) — 16px, 줄간격 1.5, `word-break: keep-all`.
- 크기 3단(1.25): 16 / 20 / 25px. 위계는 크기보다 **웨이트**(400/700/900). 뱃지·메타 전용 14px(문장 금지).
- 손글씨 디스플레이 폰트(MemomentKkukkukk)는 **넣지 않는다** — 재배포 라이선스 미확인(DW `assets/fonts/NOTICE.md`).
  `base.css` 의 `@font-face` 는 원본 그대로 두되, `static/app.css` 에서 `--dw-font-display: var(--dw-font-body)` 로
  덮어썼다. 그래서 어떤 요소도 그 글꼴을 요구하지 않고, 없는 woff 를 받으러 가지도 않는다(실측: `document.fonts`
  에서 `MemomentKkukkukk: unloaded`, 404 0건). `simplify:` 라이선스 확인되면 이 한 줄만 지우면 살아난다.

## 5. DW에서 달리 정한 것 (근거 명시)

| 항목 | DW | 이 프로젝트 | 근거 |
|---|---|---|---|
| 최대 폭 | 440px(모바일 앱) | 표 화면 1100px, 상세 글 860px | 수백 행 표 도구 — 440px에 표를 우겨넣으면 정보가 죽는다. 375px 반응형(카드형 행 변환)은 유지 |
| 부품 쓰는 법 | React 컴포넌트(jsx) | 같은 CSS + 손으로 쓴 HTML | 빌드 도구(node·vite) 없이 파이썬만으로 zip 배포한다. DW 부품 CSS는 클래스+`data-*` 방식의 순수 CSS라 React 없이 그대로 붙는다. jsx는 마크업 구조를 읽는 참고용으로만 열었다 |
| 상태 있는 부품 | React 상태 | 20줄 안쪽 바닐라 JS | `tabs`(고른 칸 표시·화살표 이동·`--dw-tabs-index`)와 `collapsible`(펴짐 표시·`inert`)만 손으로 배선했다. 나머지는 CSS만으로 동작 |
| 아이콘 | `lucide-react` | lucide 원본 path를 인라인 SVG로 | 번들러가 없다. 임의 창작이 아니라 lucide(ISC) 원본 path 그대로. 쓰는 것: 경고·안내(alert), 체크(checkbox), 아래꺾쇠(collapsible·native-select), 받은편지함(empty-state) |
| 이모지 | 금지 | 금지 | DW·ECC 공통 규칙 |
| 다크 테마 | 라이트+다크 | 라이트만 나온다 | `tokens.css` 를 통째로 복사해 다크 값도 함께 실려 있지만, 이 앱은 `data-theme` 을 설정하지 않아 항상 라이트다. 굳이 지우지 않는다 — 지우면 원본과 달라진다 |

## 6. 화면 요소와 DW 부품 대응

| 화면 요소 | DW 부품 | 쓰는 법 |
|---|---|---|
| 상단 화면 이동(검사·상세·설정) | `tabs` | `data-variant="segmented"` — 고른 칸으로 알약이 미끄러진다 |
| 안내·경고 상자 | `alert` | 키 없음·면책 = `warning`, 입력 미리보기 = `info` |
| 판정 도장(시그니처) | `badge` | 채운 배지. 매입 후보=`success` / 검토 필요·이력 없음=`warning` / 제외=`error` |
| "지금 살 수 있나" | `badge` | 도장보다 한 단계 조용하게 — `data-variant="outline"`(모르는 것은 `ghost`) |
| 입력 카드·표 종이·요약 타일 | `card` | `data-elevation="float"`(시그니처 그림자). 표를 담을 때만 `data-table-card` 로 안쪽 여백을 없앤다 |
| 결과 표·점수 내역 | `table` | `data-wrap`(문장 열은 접힘) + 숫자 열 `data-align="end"`. 640px 이하는 한 줄씩 카드로 접힌다 |
| 도메인 붙여넣기 칸 | `textarea` | |
| API 키 칸 | `input` + `field` + `label` | |
| 모델·속도 고르기 | `native-select` | 폰에서 기본 휠이 뜨도록 브라우저 것을 그대로 |
| 선택 검사 3개 | `checkbox` | |
| 진행률 | `progress` | `role="progressbar"` + 진행 문구를 `aria-labelledby` 로 가리킨다 |
| 결과 없음 | `empty-state` | |
| 그림으로 보는 발급 순서 | `collapsible` | 접힌 동안 `inert` — 눈에도 안 보이고 Tab 으로도 안 들어간다 |
| 주요 버튼 1개/화면 | `button` | 검사 시작·설정 저장만 `data-variant="primary" data-size="lg"`. 나머지는 `secondary`, 표 안은 `data-size="sm"` |

DW 부품이 덮지 못해 `static/app.css` 에 남긴 것: 페이지 폭·프로즈 여백, 표 카드의 여백 제거와 640px 카드 접기,
저장 이력 타임라인 막대(`--dw-chart-1` 한 색), AI 인용 문장, 웨이백 사진 배치, 요약 타일 격자.
`static/index.html` 의 `<style>` 에 남긴 것: 건너뛰기 링크, 위 띠, 화면 전환, 버튼 줄, 파일 고르기 칸, 발급 순서 삽화.

- **접근성 바닥**: 본문 대비 4.5:1+, 터치 44px+(주요 버튼 52px), 가로 스크롤 0, `:focus-visible` 링 유지.

## 7. 카피 규칙

- 초등학생이 읽어도 되는 낱말만. 부제·장식 문장 금지. 버튼 라벨=행동("검사 시작"→끝나면 "보고서 보기").
- 안내는 필요할 때만 1문장. "필수"는 글자로 표기(별표 금지).

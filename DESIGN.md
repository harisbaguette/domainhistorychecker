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

## 3. 토큰 (DW 값 그대로 — hex 직접 사용 금지, 전부 CSS 변수로)

DW `tokens.css`의 라이트 값을 복사해 쓴다(이 도구는 로컬 단독 실행이라 Z: 참조 불가, 값 복사가 정본 준수 방식).

- 표면: `--dw-bg #F5F2EE` / `--dw-surface #FFFFFF` / `--dw-surface-sunken #F0ECE6` / `--dw-surface-raised #FAF8F5`
- 경계: `--dw-border rgba(36,30,25,0.07)` / `--dw-border-strong #E4DFD9`
- 글자: `--dw-text #241E19` / `--dw-text-muted-aa #6A645D`(보조 문장) / `--dw-text-muted #88837C`(메타만, 문장 금지) / `--dw-text-faint #B6B1AA`
- 액센트(잉크 파랑): `--dw-accent #3D79C0` / `--dw-accent-strong #155DA1`(버튼 바탕·흰 글자) / `--dw-accent-pressed #004981` / `--dw-accent-bg #ECF5FF` / `--dw-on-accent #FFFFFF`
- 상태(원색은 면·아이콘만, 글자는 반드시 -ink):
  - success `#4BB86A` / bg `#E9FAED` / ink `#1A6D3B`
  - warning `#EAA13B` / bg `#FFF5E2` / ink `#93550F`
  - error `#DF4B46` / bg `#FFF1EF` / ink `#A42D2B`
  - info = 액센트와 동일(파랑 하나만 채도색)
- 타임라인 막대: `--dw-chart-1 #2D71B7` 한 색만(계열이 하나뿐이므로).
- 그림자: `--dw-shadow-1 0 2px 8px rgba(0,0,0,0.04)` / `--dw-shadow-2 0 8px 16px rgba(0,0,0,0.06)` / 시그니처 `--dw-shadow-glass 0 8px 32px rgba(0,0,0,0.04)`
- 모서리: control 12 / card 16 / pill 999 (DW 값 계승 — 종이 수첩 컨셉의 근거 있는 라운드)
- 간격: 4/8/12/16/24/32/48
- 모션: `--dw-ease cubic-bezier(0.23,1,0.32,1)`, 150/200/300ms. `ease-in`·`linear`·scale(0) 등장 금지.
- 포커스: `0 0 0 3px rgba(61,121,192,0.34)` — 절대 제거 금지.

## 4. 타이포

- 본문: `Pretendard, Apple SD Gothic Neo, Malgun Gothic, system-ui` — 16px, 줄간격 1.5, `word-break: keep-all`.
- 크기 3단(1.25): 16 / 20 / 25px. 위계는 크기보다 **웨이트**(400/700/900). 뱃지·메타 전용 14px(문장 금지).
- 손글씨 디스플레이 폰트(MemomentKkukkukk)는 **넣지 않는다** — 재배포 라이선스가 미확인(DW NOTICE.md)이고
  이 도구는 zip으로 배포된다. 라이선스 확정 전까지 앱 제목은 Pretendard 900으로 쓴다. `simplify:` 라이선스 확인 후 추가 검토.

## 5. DW에서 달리 정한 것 (근거 명시)

| 항목 | DW | 이 프로젝트 | 근거 |
|---|---|---|---|
| 최대 폭 | 440px(모바일 앱) | 표 화면 1100px, 상세 글 860px | 수백 행 표 도구 — 440px에 표를 우겨넣으면 정보가 죽는다. 375px 반응형(카드형 행 변환)은 유지 |
| 아이콘 | lucide-react | 아이콘 없음(텍스트+색 배지) | React 없는 정적 HTML. 임의 SVG 창작 금지 원칙 준수를 위해 아이콘 자체를 쓰지 않는 쪽 선택 |
| 이모지 | 금지 | 금지(기존 ✅⚠️❌🖼️ 전부 제거) | DW·ECC 공통 규칙 |
| 토큰 이름 | `--dw-ring` / `--dw-font-body` | `--dw-focus` / `--dw-font` | 값은 DW와 동일. 1층 primitive 없이 semantic만 복사한 단층 세트라 짧은 별칭 사용 |
| 다크 테마 | 라이트+다크 | 라이트만 | 로컬 단독 실행 도구, 화면 3개. 다크 값 복사는 유지 부담만 늘어 미채택 |

## 6. 컴포넌트 규격

- **판정 도장(시그니처)**: pill, 틴트 배경 + `-ink` 글자 + 같은 계열 1px 테두리. 매입 후보=success / 검토 필요·이력 없음=warning / 제외=error. 14px bold.
- **"지금 살 수 있나" 배지**: free=success 틴트 / soon·auction=warning 틴트 / taken=sunken 면+muted-aa 글자 / unknown=muted-aa 글자. 표의 독립 열 — 이 도구의 존재 이유(살 수 있는 도메인 찾기)라 판정 바로 옆.
- **주요 버튼 1개/화면**: 검사 시작(accent-strong 바탕+흰 글자, 48px 높이). 나머지는 테두리 버튼(surface)·ghost.
- **진행률**: accent 채움, sunken 바탕. 검사 끝나면 "보고서 보기" 버튼이 자동으로 나타난다(report_ready 이벤트).
- **표**: 흰 카드 위, 헤더 raised 면, 행 경계 `--dw-border`. 640px 이하에서 카드형 행.
- **접근성 바닥**: 본문 대비 4.5:1+, 터치 44px+, 가로 스크롤 0, `:focus-visible` 링 유지.

## 7. 카피 규칙

- 초등학생이 읽어도 되는 낱말만. 부제·장식 문장 금지. 버튼 라벨=행동("검사 시작"→끝나면 "보고서 보기").
- 안내는 필요할 때만 1문장. "필수"는 글자로 표기(별표 금지).

# static/dw — DW 디자인 시스템 원본 복사본

**편집 금지.** 이 폴더의 `.css` 파일은 DW 정본에서 그대로 복사한 것이다.
고칠 일이 생기면 이 파일이 아니라 DW 정본을 고치고, 여기로 다시 복사한다.
이 앱만의 레이아웃 규칙은 `static/app.css`(이 폴더 바깥)에 둔다.

- 원본 위치: `Z:\Doweek\design-system`
- 복사일: 2026-08-06 (모바일 뼈대 이식)

## 복사한 파일

| 이 폴더 | 원본 경로 |
|---|---|
| `tokens.css` | `tokens/tokens.css` |
| `base.css` | `styles/base.css` |
| `typography.css` | `styles/typography.css` |
| `ui/alert.css` | `ui/alert/alert.css` |
| `ui/badge.css` | `ui/badge/badge.css` |
| `ui/button.css` | `ui/button/button.css` |
| `ui/card.css` | `ui/card/card.css` |
| `ui/collapsible.css` | `ui/collapsible/collapsible.css` |
| `ui/empty-state.css` | `ui/empty-state/empty-state.css` |
| `ui/field.css` | `ui/field/field.css` |
| `ui/input.css` | `ui/input/input.css` |
| `ui/label.css` | `ui/label/label.css` |
| `ui/list-item.css` | `ui/list-item/list-item.css` |
| `ui/progress.css` | `ui/progress/progress.css` |
| `ui/textarea.css` | `ui/textarea/textarea.css` |
| `blocks/app-shell-mobile.css` | `blocks/app-shell-mobile/app-shell-mobile.css` |
| `blocks/settings.css` | `blocks/settings/settings.css` |

스타일 3장(토큰·기본·프로즈) + 부품 12종 + 블록(화면 뼈대) 2종.
`toggle-group` 은 판정 필터를 doweek 본체의 알약 탭으로 갈아 끼우면서 쓰는 데가 없어져 지웠다(DESIGN.md 3-2절).
`button-group` `checkbox` `native-select` 도 같은 이유로 지웠다(2026-08-22) — 마크업에 쓰는 데가 한 곳도 없다.
이 폴더는 통째로 보고서 style.css 에 합쳐지므로, 안 쓰는 한 장이 보고서마다 따라다닌다.
이 앱이 쓰지 않는 나머지는 zip 배포 무게 때문에 복사하지 않았다.

`typography.css`(`.dw-prose`)는 DW 가 "읽어 내려가는 글에만 쓰고 UI 껍데기에는 쓰지 말라"고 못 박은
조판 정본이다. 상세 근거와 보고서 본문이 정확히 그 '읽는 글'이라 그대로 가져다 씌운다
(예전에는 앱이 제목·문단·인용·코드 규칙을 따로 만들어 두어 DW 와 모양이 갈라져 있었다).

**표(`ui/table.css`)와 상단 탭(`ui/tabs.css`)은 뺐다.** 폰 폭(440px)에는 열이 여섯인 표가 들어가지
않는다 — DW가 목록에 내놓는 답은 `list-item`(목록 한 줄)이라 그쪽으로 옮겼다. 화면 이동과 판정 필터는
DW 부품이 아니라 **doweek 본체가 실제로 쓰는 모양**(떠 있는 도크 · 미끄러지는 알약 탭)을 옮겨 왔다
— `static/app.css` 안에 있고, 근거는 `DESIGN.md` 3-2절.

## 다시 복사하는 법

```sh
cp "Z:/Doweek/design-system/tokens/tokens.css"       static/dw/tokens.css
cp "Z:/Doweek/design-system/styles/base.css"         static/dw/base.css
cp "Z:/Doweek/design-system/styles/typography.css"   static/dw/typography.css
for c in alert badge button card collapsible empty-state field input \
         label list-item progress textarea; do
  cp "Z:/Doweek/design-system/ui/$c/$c.css" "static/dw/ui/$c.css"
done
for b in app-shell-mobile settings; do
  cp "Z:/Doweek/design-system/blocks/$b/$b.css" "static/dw/blocks/$b.css"
done
```

## 원본을 그대로 쓰기 위해 프로젝트가 감수하는 것

- `base.css`의 `@font-face`는 손글씨 폰트(`MemomentKkukkukk`) woff를 자기 폴더 기준 상대경로로 가리킨다.
  이 앱은 글꼴을 `static/fonts/` 에 두므로 그 경로가 맞지 않는다. `base.css` 자체는 한 글자도 고치지 않고,
  `static/app.css` 가 **다른 이름**(`DoweekHand`)으로 같은 글꼴을 다시 선언해 그 자리를 대신한다 —
  같은 이름을 쓰면 브라우저가 `base.css` 의 없는 경로까지 받으러 가 404가 난다(실측 404 0건).
- `tokens.css`의 다크 팔레트(`[data-theme="dark"]`)도 그대로 뒀다. 이 앱은
  `data-theme`을 설정하지 않으므로 항상 라이트로 나온다.
- `app-shell-mobile.css`는 `--dw-block-screen-height`가 없으면 `100dvh`를 쓴다. 이 앱은
  그 변수를 주지 않으므로 화면 높이를 그대로 쓴다(제목 줄·탭 줄 고정, 가운데만 구름).

## CSS를 합쳐 내려주는 곳

`src/domainchecker/report/html.py`가 이 폴더의 파일을
`tokens → base → typography → ui/*(이름순) → blocks/*(이름순) → static/app.css` 순서로 읽어 하나로 합친다.
블록은 부품을 짜 맞춘 것이라 부품 뒤에 온다. 서버는 그것을 `/style.css`로 내려주고,
보고서를 만들 때는 같은 내용을 보고서 폴더 안에 `style.css` 한 장으로 써 두고 모든 페이지가 그것을 가리킨다.
(예전에는 페이지마다 통째로 끼워 넣었는데, 도메인 1,000개면 페이지 1,001장 × 50KB = 약 78MB 가 되어
메일에 붙지 않았다. 보고서는 언제나 폴더 한 벌로 나가므로 한 장만 두면 된다.)

# static/dw — DW 디자인 시스템 원본 복사본

**편집 금지.** 이 폴더의 `.css` 파일은 DW 정본에서 그대로 복사한 것이다.
고칠 일이 생기면 이 파일이 아니라 DW 정본을 고치고, 여기로 다시 복사한다.
이 앱만의 레이아웃 규칙은 `static/app.css`(이 폴더 바깥)에 둔다.

- 원본 위치: `Z:\Doweek\design-system`
- 복사일: 2026-08-05

## 복사한 파일

| 이 폴더 | 원본 경로 |
|---|---|
| `tokens.css` | `tokens/tokens.css` |
| `base.css` | `styles/base.css` |
| `ui/alert.css` | `ui/alert/alert.css` |
| `ui/badge.css` | `ui/badge/badge.css` |
| `ui/button.css` | `ui/button/button.css` |
| `ui/card.css` | `ui/card/card.css` |
| `ui/checkbox.css` | `ui/checkbox/checkbox.css` |
| `ui/collapsible.css` | `ui/collapsible/collapsible.css` |
| `ui/empty-state.css` | `ui/empty-state/empty-state.css` |
| `ui/field.css` | `ui/field/field.css` |
| `ui/input.css` | `ui/input/input.css` |
| `ui/label.css` | `ui/label/label.css` |
| `ui/native-select.css` | `ui/native-select/native-select.css` |
| `ui/progress.css` | `ui/progress/progress.css` |
| `ui/table.css` | `ui/table/table.css` |
| `ui/tabs.css` | `ui/tabs/tabs.css` |
| `ui/textarea.css` | `ui/textarea/textarea.css` |

이 앱이 쓰지 않는 부품(sidebar·dialog·carousel 등 45종)은 zip 배포 무게 때문에 복사하지 않았다.

## 다시 복사하는 법

```sh
cp "Z:/Doweek/design-system/tokens/tokens.css" static/dw/tokens.css
cp "Z:/Doweek/design-system/styles/base.css"   static/dw/base.css
for c in alert badge button card checkbox collapsible empty-state field input label \
         native-select progress table tabs textarea; do
  cp "Z:/Doweek/design-system/ui/$c/$c.css" "static/dw/ui/$c.css"
done
```

## 원본을 그대로 쓰기 위해 프로젝트가 감수하는 것

- `base.css`의 `@font-face`는 손글씨 폰트(`MemomentKkukkukk`) woff를 상대경로로 가리킨다.
  그 폰트는 재배포 라이선스가 확인되지 않아 **가져오지 않았다**(DW `assets/fonts/NOTICE.md`).
  대신 `static/app.css`에서 `--dw-font-display`를 본문 폰트로 덮어써서, 어떤 요소도
  그 폰트를 요구하지 않게 했다 — 그래서 없는 woff를 받으러 가지 않고 404도 나지 않는다.
  `base.css` 자체는 한 글자도 고치지 않았다.
- `tokens.css`의 다크 팔레트(`[data-theme="dark"]`)도 그대로 뒀다. 이 앱은
  `data-theme`을 설정하지 않으므로 항상 라이트로 나온다.

## CSS를 합쳐 내려주는 곳

`src/domainchecker/report/html.py`가 이 폴더의 파일을 `tokens → base → ui/*(이름순) → static/app.css`
순서로 읽어 하나로 합친다. 서버는 그것을 `/style.css`로 내려주고, 보고서 HTML은
같은 내용을 `<style>`로 끼워 넣는다(zip을 풀어 파일로 열어도 모양이 나오도록).

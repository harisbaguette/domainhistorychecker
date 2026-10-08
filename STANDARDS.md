# 이 프로젝트가 지키는 표준

산출물: CLI 도구, 라이브러리, 패키지.
국제 표준의 기준 규칙을 바탕으로 이 프로젝트에 맞게 고쳐 쓰는 규칙이다. 작업을 시작할 때마다 읽고 지켜서 만든다.

- '기준 규칙'은 project start(`readiness.py start`)를 다시 돌리면 갱신된다. 손으로 고치지 않는다.
- '이 프로젝트 맞춤'은 AI가 프로젝트 성격에 맞게 쓴다. 더한 규칙, 바꾼 규칙(원래 → 바꾼 것, 까닭), 이 표준을 지킬 계획을 적는다. 맞춤이 기준 규칙보다 앞선다.
- 법이 정한 의무(개인정보, 결제, 등급과 확률 표시, 아동 보호)는 맞춤으로 빼지 않는다.
- 규칙을 어기는 코드나 파일을 만들지 않고, 자동 검사는 시험 명령에 넣어 바뀔 때마다 돌린다.

<!-- standard:iso-25010 -->
## ISO/IEC 25010:2023 제품 품질 모델

소프트웨어 품질을 9가지 특성으로 나눈 국제 표준.

### 기준 규칙

- 기능 적합성, 성능 효율, 호환성, 상호작용 능력, 신뢰성, 보안, 유지보수성, 유연성, 안전 가운데 이 제품에 해당하는 특성의 목표를 요구사항에 적는다.
- 목표마다 확인 방법(시험, 측정, 실제 사용)을 정한다.

원문: `python3 ~/.agents/work-methods/readiness.py source iso-25010`

### 이 프로젝트 맞춤

<!-- custom:iso-25010 -->
적용 대상은 프로젝트 전용 스킬과 오프라인 검증 명령이다. 도메인과 브랜드 요청을 구분하고 함께 필요한 경우 연결한다. 품질 목표는 조회 실패를 성공으로 오인하지 않는 판단, 구매를 사용자에게 넘기는 경계, 올바른 참조 연결이다. 구조는 자동 검사로, 판단은 대표 사례를 직접 적용해 확인한다.
<!-- /custom:iso-25010 -->
<!-- /standard:iso-25010 -->

<!-- standard:data-formats -->
## 데이터 형식 표준 (UTF-8, JSON, ISO 8601/RFC 3339)

문자, 날짜, 데이터 교환의 기본 표준.

### 기준 규칙

- 글자는 UTF-8로 저장하고 주고받는다.
- 날짜와 시간은 RFC 3339(ISO 8601) 형식과 시간대를 함께 저장한다. 화면에는 사용자 지역 형식으로 보인다.
- 데이터 교환은 JSON(RFC 8259)이나 표준 형식을 쓰고, 금액은 정수 최소 단위와 통화 코드(ISO 4217)로 다룬다.
- 미래 일정과 반복 일정은 UTC 오프셋과 함께 IANA 시간대 이름(Asia/Seoul)을 저장한다. 문자열로 주고받을 때는 RFC 9557 형식(2026-10-08T09:00:00+09:00[Asia/Seoul])을 쓴다.
- 나라는 ISO 3166-1 두 글자 코드(KR), 전화번호는 E.164 국제 형식(+82로 시작)으로 저장한다.
- 사용자가 입력한 글자는 유니코드 NFC로 정규화해 저장하고 비교한다. 그래야 자모가 풀린 한글과 합쳐진 한글이 다른 글자로 취급되지 않는다.

원문: `python3 ~/.agents/work-methods/readiness.py source rfc3339 rfc8259 rfc9557 iana-tz iso-3166 itu-e164 w3c-charmod-norm`

### 이 프로젝트 맞춤

<!-- custom:data-formats -->
스킬·안내문은 UTF-8 Markdown, 호출 정보는 YAML로 저장한다. 도메인 표시는 유니코드 이름과 필요한 ASCII 표기를 구분한다. 조사 근거의 시각에는 시간대를 붙이고, 비교 금액에는 통화와 등록 기간을 적는다. 프로젝트 지도는 공용 CLI를 통해서만 갱신한다.

모든 입력을 NFC로 덮어쓴다는 기준은 원문 보존과 용도별 비교로 바꾼다. 표시 문자열, 도메인의 IDNA 처리, URL·서명·인증값은 같은 정규화 규칙을 쓰지 않는다. W3C의 문자열 식별·매칭 지침에 맞게 원문과 비교용 값을 구분한다.
조사 시각은 일반 RFC 3339를 사용한다. 미래 반복 일정을 다룰 때만 IANA 지역 시간대를 함께 보존하며 RFC 9557 문자열은 소비자가 지원할 때 사용한다. 국가·전화번호는 해당 데이터가 실제 필요한 경우에만 처리한다.
<!-- /custom:data-formats -->
<!-- /standard:data-formats -->

<!-- standard:licensing -->
## 라이선스 표기 (SPDX, REUSE)

코드, 글꼴, 그림, 소리를 쓸 권리를 밝히는 표준.

### 기준 규칙

- 가져다 쓴 코드와 에셋의 라이선스를 확인하고 출처를 ASSETS.md나 고지 파일에 남긴다.
- 내 프로젝트의 라이선스를 SPDX 식별자로 LICENSE에 밝힌다.
- 상업 이용, 변형, 재배포 조건이 맞지 않는 에셋은 쓰지 않는다.

자동 검사: reuse lint; license-checker나 pip-licenses로 의존성 라이선스 목록 만들기

원문: `python3 ~/.agents/work-methods/readiness.py source spdx-licenses reuse cc`

### 이 프로젝트 맞춤

<!-- custom:licensing -->
공식 자료의 본문을 복사하지 않고 필요한 판단 기준을 작성한다. 실제 브랜딩 에셋을 제작할 때 사용 조건을 확인하고 ASSETS.md에 기록한다. 개인용 스킬에 제3자 코드나 에셋을 포함할 경우 권리 고지를 함께 둔다. 외부 공개 라이선스는 권리자가 정한다.
<!-- /custom:licensing -->
<!-- /standard:licensing -->

<!-- standard:cli -->
## 명령줄 관례 (POSIX Utility Syntax, OpenSSF Best Practices)

명령줄 도구와 오픈소스 공개의 기본 관례.

### 기준 규칙

- --help와 --version을 두고, 성공은 0, 실패는 0이 아닌 종료 코드로 끝낸다.
- 결과는 stdout, 오류는 stderr로 보낸다.
- 공개 저장소면 README, LICENSE, 보안 신고 방법을 둔다.
- NO_COLOR 환경 변수가 비어 있지 않으면 색을 끄고, 출력이 터미널이 아니면 색과 진행 표시를 끈다.
- 리눅스에서 설정과 캐시 파일은 XDG Base Directory 규칙($XDG_CONFIG_HOME, 비어 있으면 ~/.config) 아래에 둔다.

원문: `python3 ~/.agents/work-methods/readiness.py source posix-utility openssf-badge no-color clig xdg-basedir`

### 이 프로젝트 맞춤

<!-- custom:cli -->
검증 명령은 `python3 scripts/check_skills.py`이며 `--root`, `--help`, `--version`을 제공한다. 성공은 stdout과 종료 코드 0, 실패는 stderr와 0이 아닌 코드로 알린다. 색을 출력하지 않고 네트워크·설정·검사 결과를 변경하지 않는다. `make check-skills`에서 실행하고 `make check`는 이후 기존 pytest를 실행한다.

POSIX의 옵션·피연산자 규칙과 GNU식 긴 옵션, CLIG·NO_COLOR의 공동체 관례를 구분한다. 모든 조합을 하나의 국제 표준 의무로 설명하지 않는다. 이 검증기는 로컬 스킬을 읽을 뿐 별도 설정·캐시 디렉터리를 만들지 않는다.
<!-- /custom:cli -->
<!-- /standard:cli -->

## 이 프로젝트만의 규칙과 계획

<!-- custom:project -->
이번 산출물은 도메인 검토와 브랜딩을 돕는 프로젝트 전용 스킬이다. 낙장·중고 도메인 실사, 신규 도메인 이름·등록·비용 검토, 보유 관리, 브랜드 전략·네이밍·언어·시각·출시 적용을 다룬다.

도메인 등록·입찰·백오더·구매·결제는 사용자가 직접 한다. 도메인과 브랜드에 대한 조사는 실제 후보와 사업 조건을 받아 수행한다. 원본 자료, 조회 실패와 미확인 사항을 숨기지 않는다. 구체적인 브랜드 제작에는 필요한 에셋을 구하고 렌더해 확인한다.

스킬 이름 `$kh-skill-domain`, `$kh-skill-branding`과 문서 진입점을 사용 계약으로 유지한다. 이름을 바꾸면 AGENTS.md, 호출 정보, 안내문과 교차 참조를 함께 갱신한다. 검증 명령의 버전은 0.1.0이며 검사 실패 원인과 종료 코드로 자동 실행에서도 실패를 구별할 수 있어야 한다.
<!-- /custom:project -->

## 해당 없음으로 뺀 표준

<!-- custom:skipped -->
- Core Web Vitals (LCP 2.5초, INP 200ms, CLS 0.1): Jev 판정: 해당 없음(해당 확률 0.08)
- Web App Manifest: Jev 판정: 해당 없음(해당 확률 0.11)
- 데스크톱 배포 기준 (macOS 공증, Windows 코드 서명, Microsoft Store 정책): Jev 판정: 해당 없음(해당 확률 0.18)
- 개인정보 보호 (한국 개인정보보호법, GDPR): Jev 판정: 해당 없음(해당 확률 0.17)
- OAuth 2.0 보안 모범 사례와 OpenID Connect (RFC 9700, RFC 7636 PKCE, OIDC Core = ISO/IEC 26131:2024): Jev 판정: 해당 없음(해당 확률 0.03)
- 로그와 요청 추적 (W3C Trace Context, OpenTelemetry, OWASP Logging): Jev 판정: 해당 없음(해당 확률 0.09)
- QR 코드 (ISO/IEC 18004:2024): Jev 판정: 해당 없음(해당 확률 0.09)
- 아동 대상 설계 (UK Age Appropriate Design Code, IEEE 2089-2021): Jev 판정: 해당 없음(해당 확률 0.06)
- 공급망 보안 (NIST SSDF, SLSA, SBOM): 외부 전달 배포물이 없고 검증 명령은 Python 표준 라이브러리만 사용한다
- 버전 규칙 (Semantic Versioning 2.0, PEP 440): 스킬 호출 이름과 출력 계약을 기록하되 별도 외부 패키지 버전은 발행하지 않는다
<!-- /custom:skipped -->

## 다 됐는지 확인

계획 지도의 '완성 기준' 작업이 이 표준을 포함한 점검 목록이다. 모두 증거와 함께 끝나야 완성이다.

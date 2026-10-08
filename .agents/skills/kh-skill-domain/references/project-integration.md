# 기존 검사 앱 연결

이 프로젝트 루트에서 실행한다. 아래 경로는 현재 연결 지점이며 바꾸기 전에 실제 파일과 호출부를 확인한다.

| 사용할 자료 | 위치 |
|---|---|
| 저장 결과와 개별 캐시 | `data/results.json`, `data/cache/` |
| HTML 보고서 | `data/report/index.html` |
| 판정 상태와 데이터 구조 | `src/domainchecker/models.py` |
| 검사 순서 | `src/domainchecker/pipeline.py` |
| 종합 판정 | `src/domainchecker/analyze/scoring.py` |
| AI 분석 | `src/domainchecker/analyze/ai.py` |
| 현재 등록 가능 조회 | `src/domainchecker/clients/gabia.py` |
| 크롤 기록·현재 페이지 | `src/domainchecker/clients/freeindex.py` |

이미 있는 결과를 먼저 읽고 검사 시각, 개별 `check.status`, `optional`, 수집 범위를 확인한다.
앱의 `BUY`는 매입 완료가 아니다. `optional`로 생략한 항목은 따로 미확인으로 다룬다.
저장된 취득 상태와 가격을 현재 사실로 재사용하지 않는다.

앱을 켜 달라는 요청에는 `uv run domainchecker`를 사용한다. 기본 주소는 `http://127.0.0.1:8765/`다.
실행 중이면 서버를 중복 시작하지 않는다. 읽기 경로는 `GET /api/status`, `GET /api/results`, `GET /api/detail/{domain}`이며 실제 계약은 `src/domainchecker/server.py`를 확인한다.
검사 실행은 사용자의 도메인 검사 요청 범위에서 한다. 환경 점검만 요청받았으면 검사를 시작하지 않는다.
유료 분석 키를 사용하거나 대량 수집할 때는 기존 예산·권한 범위를 따른다. 설정 조회 응답에 키가 포함될 수 있으므로 전체를 출력하지 않는다.

## 결과를 해석할 때의 한계

- 앱의 Common Crawl 자료는 Google 색인 상태나 백링크 분석 자료가 아니다.
- 백링크·참조 도메인·앵커텍스트를 정밀 검토하려면 별도 내보내기 자료나 허용된 조회 수단이 필요하다.
- 현재 판정 구현은 AI가 없으면 `BUY`를 주지 않는다. README·주석과 어긋날 때 실제 코드와 시험을 확인한다.
- 등록 상태는 현재 가비아 응답에 의존한다. 다른 판매처의 지원 여부, 프리미엄 가격, 예약·자격 조건을 함께 판단해야 한다.
- 과거 이력의 표본·미열람 구간과 명단 조회 실패를 최종 의견에서 감추지 않는다.
- 평판 조회의 응답 코드는 [이력과 매입 실사](due-diligence.md)에 맞게 다시 해석한다. 앱의 상태 이름만으로 공식 서비스의 이용 조건이나 API 지원 여부까지 검증됐다고 보지 않는다.

일반 결과 문서와 원본 근거는 `data/advisory/<작업명>/`에 두면 기존 캐시와 구별할 수 있다.
파일 저장이 필요할 때만 만들며, 사용자의 기존 검사 결과나 매입 예정 목록을 덮어쓰지 않는다.

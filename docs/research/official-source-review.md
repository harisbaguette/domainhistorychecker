# 도메인·브랜딩 근거 점검

점검일: 2026-10-08. 범위는 두 스킬의 기존 외부 진입점 12개, STANDARDS.md에서 선택한 표준·관례의 근거 16개, 보강에 사용한 추가 자료다. 각 자료의 관련 조항과 적용 범위를 대조했다. 발행 기관의 모든 문서나 유료 전문을 읽었다는 뜻은 아니다.

본문 확인, 검색 서비스의 진입 화면 확인, 공개 요약만 확인한 경우를 구분한다. 검색 홈페이지 확인은 특정 도메인·상표의 조회 완료가 아니다. HTTP 성공만으로 본문 확인으로 세지 않았다.

## 기존 도메인 자료 12개

| 자료 | 확인 범위 | 반영 |
|---|---|---|
| [ICANN Lookup](https://lookup.icann.org/) | 동적 조회 진입점; 텍스트 도구로 본문 추출 안 됨 | 실조회는 IANA가 지정한 RDAP 서버로 검증 |
| [2016 RDAP Profile](https://www.icann.org/resources/pages/rdap-operational-profile-2016-07-26-en) | 2016년 운영 프로파일임을 확인 | 스킬의 기본 안내를 현재 RDAP 사용자 안내로 교체 |
| [ICANN ERRP](https://www.icann.org/resources/pages/errp-2013-02-28-en) | 만료 후 갱신, 삭제 후 복구, 비용 고지 조항 | 만료·삭제·복구·신규 취득 구분; ccTLD에 일괄 적용 금지 |
| [IANA Root Zone Database](https://www.iana.org/domains/root/db) | TLD 종류·운영기관 목록 | 국가 도메인의 실제 관리기관을 찾는 출발점 |
| [IANA Reserved Domains](https://www.iana.org/domains/reserved) | 예시 도메인의 예약·취득 제한 | example.com을 구매 후보에서 제외 |
| [Wayback](https://web.archive.org/) | 아카이브 검색 진입점 | 검색 화면과 과거 페이지의 실제 열람 구분 |
| [Common Crawl Index](https://index.commoncrawl.org/) | 크롤 묶음별 URL 색인·API 안내 | Google 색인·백링크 품질로 오인하지 않기 |
| [Spamhaus Checker](https://check.spamhaus.org/) | 도메인·IP 평판 조회 진입점 | 사이트 접속만으로 후보 미등재를 주장하지 않기 |
| [Google Safe Browsing 상태](https://transparencyreport.google.com/safe-browsing/search) | 동적 공개 상태 화면 | 공식 API와 화면 내부 요청을 구분; 개별 조회는 별도 검증 |
| [Google 스팸 정책](https://developers.google.com/search/docs/essentials/spam-policies) | 만료 도메인 악용, 링크 스팸 | 낙장 재사용 전체를 위반으로 간주하지 않고 목적·내용 평가 |
| [KIPRIS](https://www.kipris.or.kr/khome/main.do) | 한국 상표 검색 진입 화면 | 검색 조건·결과를 확보해야 개별 후보 확인으로 인정 |
| [WIPO Global Brand Database](https://www.wipo.int/en/web/global-brand-database) | 수록 자료와 국가·지역 검색 보완 안내 | WIPO 미발견을 전 세계 상표 충돌 없음으로 해석하지 않기 |

## 선택한 표준·관례 16개

| 자료 | 확인 범위 | 적용 |
|---|---|---|
| [ISO/IEC 25010:2023](https://committee.iso.org/standard/78176.html) | 공개 제품 설명·9가지 품질 특성; 유료 전문 미열람 | 목표·확인 방법을 정하는 틀로만 사용; 인증·전문 준수 주장 없음 |
| [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) | 인터넷 시각 표현의 구문 | 조사 시각과 UTC 오프셋 보존 |
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) | JSON 및 UTF-8 상호운용 항목 | 근거 파일의 JSON·UTF-8 |
| [RFC 9557](https://www.rfc-editor.org/rfc/rfc9557) | RFC 3339 확장과 지역 시간대 정보 | 지원하는 소비자에만 확장 문자열 사용 |
| [IANA Time Zones](https://www.iana.org/time-zones) | 지역 시간대 데이터의 관리·갱신 | 미래 반복 일정의 지역 시간대 보존 |
| [ISO 3166](https://www.iso.org/iso-3166-country-codes.html) | 공개 국가 코드 설명 | 국가 필드가 필요할 때 코드 사용; 유료 표준 전문 미열람 |
| [ITU E.164](https://www.itu.int/rec/T-REC-E.164) | 권고 목록과 국제 전화번호 계획의 범위 | 전화번호 데이터가 없는 이번 산출물에 추가 처리 강요 안 함; 전문 미열람 |
| [W3C Character Model](https://www.w3.org/TR/charmod-norm/) | 문자열 식별·매칭, 정규화 기본값 | 모든 입력 NFC 덮어쓰기를 원문 보존·용도별 처리로 수정 |
| [SPDX License List](https://spdx.org/licenses/) | 라이선스 식별자와 본문 연결 | 실제 포함한 에셋·코드의 라이선스 식별 |
| [REUSE 3.3](https://reuse.software/spec-3.3/) | 파일별 저작권·라이선스 표기 | 제3자 파일을 포함할 때 고지 확인; 인증 주장 없음 |
| [Creative Commons](https://creativecommons.org/cc-licenses/) | 각 라이선스의 이용 조건 개요 | 무료 다운로드와 상업·변형·재배포 권리를 구분 |
| [POSIX Issue 8 §12](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap12.html) | 12.1·12.2 옵션·피연산자, `--` 규칙 | 웹 도구 403 후 같은 공개 문서를 일반 HTTP로 읽음; 긴 옵션은 별도 관례로 구분 |
| [OpenSSF Best Practices](https://www.bestpractices.dev/en/criteria/0) | passing 기준의 문서·변경·품질·보안 항목 | 적합한 로컬 검증만 적용; 배지 취득 주장 없음 |
| [NO_COLOR](https://no-color.org/) | 비어 있지 않은 환경 변수의 색 억제 | 발행자의 비공식 공동체 관례로 분류 |
| [Command Line Interface Guidelines](https://clig.dev/) | help, 오류, 출력, 종료 코드 | 저자들의 실무 지침이며 국제 표준은 아님 |
| [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/) | 설정·캐시 경로의 기본값 | 별도 캐시를 만들지 않는 검사기에 불필요한 경로 추가 안 함 |

## 보강 자료

| 자료 | 대조한 내용 | 반영 위치 |
|---|---|---|
| [ICANN RDAP 사용자 안내](https://www.icann.org/en/contracted-parties/registry-operators/registration-data-access-protocol/information-for-rdap-users-31-08-2018-en) | gTLD RDAP 제공, WHOIS와의 관계, 공식 클라이언트 | sources, due-diligence |
| [IANA RDAP 부트스트랩](https://data.iana.org/rdap/dns.json) | TLD별 권한 있는 RDAP 서버 | com·org 실제 등록 조회 |
| [ICANN EPP 상태](https://www.icann.org/resources/pages/epp-status-codes-2014-06-16-en) | redemptionPeriod, pendingDelete와 복합 상태, 잠금 | due-diligence |
| [ICANN Transfer Policy](https://www.icann.org/en/contracted-parties/accredited-registrars/resources/domain-name-transfers/policy) | 기관 간 이전 제한과 등록자 변경 잠금의 차이 | portfolio |
| [KRNIC 관리준칙](https://krnic.or.kr/jsp/infoboard/law/domManRule.jsp) | 제3~5조 적용 범위·주소지·단계별 등록 조건, 2026년 개정 포함 | new-domain |
| [Wayback Availability API](https://archive.org/help/wayback_api.php) | 최근접 스냅샷 하나, 빈 응답에 접근 불가 포함 | due-diligence, evidence |
| [Spamhaus DNSBL 이용](https://www.spamhaus.org/faqs/dnsbl-usage/) | 리졸버 제한, 오류 응답, 이용 조건 | due-diligence |
| [Spamhaus DQS 오류](https://docs.spamhaus.com/datasets/docs/source/10-data-type-documentation/datasets/040-zones.html) | 키·오타 등 오류 코드 | 등재와 조회 실패 분리 |
| [Spamhaus DBL](https://www.spamhaus.org/blocklists/domain-blocklist/) | 도메인 대상, IP 오질의 응답 | IP 목록과 도메인 목록 분리 |
| [Safe Browsing API](https://developers.google.com/safe-browsing/) | 비상업용 조건, 상업용 Web Risk 안내 | 공개 상태 화면과 API 지원 범위 구분 |
| [Porkbun 요금표](https://porkbun.com/products/domains) | USD, 비프리미엄 연간 등록·갱신·이전, 별도 복구 비용 | 일반 가격표와 후보 견적 구분, 비용 실계산 |
| [Cloudflare 신규 등록](https://developers.cloudflare.com/registrar/get-started/register-domain/) | 네임서버 사용 의무, IDN 지원 제약 | 가격 외 운영 적합성 확인 |
| [USPTO Strong trademarks](https://www.uspto.gov/trademarks/basics/strong-trademarks) | 설명·일반 명칭과 조어·연상형의 식별력 | strategy-and-naming; 미국 적용 범위 명시 |
| [USPTO Likelihood of confusion](https://www.uspto.gov/trademarks/search/likelihood-confusion) | 소리·외관·의미와 관련 상품·서비스 | 이름·분류 번호만으로 권리 판단 금지 |
| [WCAG 2.2](https://www.w3.org/TR/WCAG22/) | 1.4.1, 1.4.3, 1.4.11, 큰 글자·로고 예외 | identity-and-launch |
| [OFL FAQ](https://openfontlicense.org/ofl-faq/) | 1.1 디자인 사용, 파일 배포·임베딩·수정·예약 이름 | 글꼴로 만든 결과물과 폰트 파일 권리 구분 |
| [Google Fonts FAQ](https://fonts.google.com/faq) | 동적 화면을 브라우저로 열어 상업 이용·로고·라이선스 종류를 확인 | 모든 무료 글꼴이 같은 라이선스라는 가정 금지 |
| [Google 사이트 이름](https://developers.google.com/search/docs/appearance/site-names) | WebSite 필수·선택 속성, 검색 표시의 비보장 | identity-and-launch |
| [Google 사이트 이전](https://developers.google.com/search/docs/crawling-indexing/site-move-with-url-changes) | URL 대응·영구 리디렉션·유지 기간·주소 변경 | portfolio, identity-and-launch |

상표 검색과 개인정보가 비공개인 자료, 판매처 계정·결제 화면은 일반 문서 조사로 대체할 수 없다. 개별 구매 업무에서는 후보별로 다시 확인한다. 이번 스킬 준비의 완료와 특정 후보의 구매 가능·상표 안전 판정은 별개다.

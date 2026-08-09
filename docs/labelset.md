# 시험지(정답 라벨 세트) 후보 목록 — 사람 최종 승인 전

> 조사일: 2026-08-04 · 판정 정확도 시험용 정답 라벨 조달 조사. 검색 15회(WebSearch 9 + WebFetch 4 + 보완 1) + whois 로컬 조회로 확인.
> **중요**: 이 표는 후보 초안이다. 실제 파이프라인 시험지로 쓰기 전에 사람이 Wayback으로 육안 재확인 후 최종 승인해야 한다.
> 실제 악성 사이트 접속은 하지 않았음 — 전부 검색·아카이브·whois 조회로만 확인.

## 확보 현황 요약

| 라벨 | 목표 | 확보(만료 확정) | 확보(등록됨·이력검증용) | 합계 |
|---|---|---|---|---|
| 위험 | 10~15 | 2 | 2(비만료, 참고용) | 4 |
| 우량 | 10~15 | 5 | 8 | 13 |
| 경계 | 5~10 | 0 | 4 | 4 |

**위험 라벨은 목표(10~15개)에 크게 못 미침 — 4개만 확보.** 이유: URLhaus·PhishTank는 최근 활성 위협 위주로 설계돼 있어 "과거 등재 + 현재 만료"로 걸러 웹검색만으로 찾기 어려웠고(둘 다 검색 API 직접 조회가 필요해 웹검색으로는 개별 사례를 못 찾음), BlackHatWorld 등 SEO 커뮤니티 글은 스팸 사례를 설명은 해도 실제 도메인명을 가리는(비공개) 경우가 대부분이었음. 대신 DOJ/FBI의 "Operation In Our Sites" 위조약국 단속 보도자료에서 실명이 공개된 도메인 2개를 확보(whois로 미등록 확정)했고, 관련 사기 사건에서 나온 도메인 2개는 재등록된 상태라 참고용으로만 남김. 경계 라벨도 4개로 목표 하단(5개) 미달.

---

## 1. 위험 라벨 후보

| 도메인 | 라벨 | 근거 URL | 만료 상태 | 메모 |
|---|---|---|---|---|
| walgreens-store.com | 위험 | https://justice.gov/opa/pr/federal-courts-order-seizure-150-website-domains-involved-selling-counterfeit-goods-part-doj (2011 DOJ·FDA "가짜 캐나다 약국" 단속 보도자료에 브랜드 사칭 사례로 명시) | **미등록(만료) 확정** — whois "No match for domain" | FDA가 2012년경 1,677개 가짜 약국 사이트 중 하나로 지목, 압류 경고 배너 게시 이력. Walgreens 브랜드 사칭 타이포스쿼팅. 현재 whois로 미등록 확인, Wayback으로 압류 배너 스냅샷 육안 재확인 권장 |
| c-v-s-pharmacy.com | 위험 | 위와 동일 (justice.gov 동일 보도자료) | **미등록(만료) 확정** — whois "No match for domain" | CVS 브랜드 사칭, 같은 단속 건. whois 미등록 확인 |
| sderclub.com | 위험(참고) | https://www.justice.gov/archive/usao/md/news/2012/GovernmentSeizesTwoWebsitesOfferingFraudulentStoreandRewardsCouponsandRelatedPayPalAccounts.html (2012 메릴랜드 검찰청 — 가짜 쿠폰·PayPal 연계 사기 압류 보도자료) | **등록됨(비만료)** — Registry Expiry 2026-09-30, 등록대행사 Xin Net(중국) | 2012년 압류 후 현재는 제3자가 재등록한 것으로 보임(등록대행사 상이). 압류 당시 이력은 문서로 확정되나 "만료 도메인" 조건은 현재 미충족 — 시험지 채택 시 "과거 이력만 검증용"으로 별도 표기 필요 |
| ccccpn.com | 위험(참고) | 위와 동일 | **등록됨(비만료)** — Registry Expiry 2027-04-15, 등록대행사 Xin Net(중국) | sderclub.com과 동일 사건의 공범 도메인. 재등록 상태, 참고용 |

**추가 조달 경로(다음 조사자용 메모)**: URLhaus는 `urlhaus.abuse.ch/api/`로 호스트명 기준 과거 등재 이력을 직접 질의해야 "현재 미등록" 필터링이 가능(웹검색으로는 브라우즈 페이지가 최근 48시간 활성 위협 위주로만 노출됨). PhishTank는 `data.phishtank.com`의 CSV 아카이브를 내려받아 로컬에서 만료 여부를 whois로 일괄 대조하는 방식이 필요. Spamhaus ROKSO(Register Of Known Spam Operations)도 실명 도메인이 많아 유망하나 이번 조사에선 시간 내 접근 못 함.

---

## 2. 우량 라벨 후보

| 도메인 | 라벨 | 근거 URL | 만료 상태 | 메모 |
|---|---|---|---|---|
| freechal.com | 우량 | https://www.khan.co.kr/article/201301171410191 (경향신문, "프리챌 커뮤니티 서비스 종료…13년만에 추억 속으로") / https://namu.wiki/w/프리챌 | **확인 불가** — whois 조회가 반복 시간초과(Verisign 서버 연결 지연). 운영사(주식회사 프리챌)는 2012년 폐업, 서비스는 2013-02-19 완전 종료가 언론 확인됨 | 1999년 설립, 2002년 전성기 일일 방문자 180만명·회원 1000만명의 한국 1세대 커뮤니티 포털. 가장 강력한 "유명 폐업 서비스" 사례. whois는 사람이 브라우저나 다른 whois 도구로 재확인 필요 |
| sitemeter.com | 우량 | https://whoapi.com/blog/5-all-time-domain-expirations-in-internets-history/ | **등록됨(비만료)** — Registry Expiry 2027-04-03 | 한때 Alexa 상위 1,000위 웹 방문자 분석 서비스, 2017-07-01 서비스 종료. 도메인 자체는 갱신돼 있어 "이력 검증용"으로만 사용 |
| protocol.com | 우량 | https://news.hada.io/topic?id=7831 (Politico 산하 테크 뉴스매체 Protocol 서비스 종료 보도) | **등록됨(비만료)** — Registry Expiry 2031-01-25 | 2020년 창간, 2022-11 서비스 종료된 테크 저널리즘 매체. 도메인은 모기업이 보유 유지 중, 이력 검증용 |
| davesphotorestoration.com | 우량 | https://goodbye.domains/ (2012–2020 운영된 사진 복원업 웹사이트, 정상 폐업) | **미등록(만료) 확정** — whois "No match for domain" | 스팸·PBN 이력 없는 정상 소규모 사업 종료 사례. 만료 확정 |
| homeyoke.com | 우량 | https://goodbye.domains/ (2014–2017, 홈요가 강습 판매 플랫폼) | **미등록(만료) 확정** — whois "No match for domain" | 정상 스타트업 종료, 만료 확정 |
| discontinuedcereals.com | 우량 | https://goodbye.domains/ (2013–2014, 단종 시리얼 재판매 컨셉) | **미등록(만료) 확정** — whois "No match for domain" | 만료 확정 |
| imakebeeer.com | 우량 | https://goodbye.domains/ (2008–2020, 맥주 양조 Q&A) | **미등록(만료) 확정** — whois "No match for domain" | 만료 확정 |
| badrenters.ca | 우량 | https://goodbye.domains/ (2019–2021, 임대인 정보 공유 도구, DB 장애로 종료) | **미등록(만료) 확정** — whois "Not found" | .ca 도메인, 만료 확정 |
| kicksartre.com | 우량 | https://goodbye.domains/ (2011–2012, 철학 탐구 크라우드펀딩 컨셉) | **등록됨(비만료)** — Registry Expiry 2026-12-30 | 제3자 재등록 상태로 보임, 이력 검증용 |
| cyber-assistant.com | 우량 | https://goodbye.domains/ (1994–2004, 기업용 가상 컨시어지 서비스) | **등록됨(비만료)** — Registry Expiry 2027-01-29 | 이력 검증용 |
| ourcade.com | 우량 | https://goodbye.domains/ (2009–2014, 오락실 위치 기반 가이드) | **등록됨(비만료)** — Registry Expiry 2027-01-16 | 이력 검증용 |
| didit.site | 우량 | https://goodbye.domains/ (2018–2019, 개인 성취 기록용 Mastodon 인스턴스) | **등록됨(비만료)** | 이력 검증용, 신뢰도 낮음(마이크로 프로젝트) |
| patently.ai | 우량 | https://goodbye.domains/ (2015–2016, 특허 검색 엔진) | **등록됨(비만료)** — Registry Expiry 2027-10-15 | 이력 검증용 |

**메모**: goodbye.domains 출처 항목들은 전부 "정상 종료(스팸 없음)"로 그 사이트 자체가 명시한 것이라 근거 신뢰도는 사이트 신뢰성에 의존함 — 채택 전 Wayback으로 실제 콘텐츠가 사이트 설명과 일치하는지 1차 확인 권장. freechal.com(한국 사례)이 언론 보도 기반이라 근거가 가장 탄탄함.

---

## 3. 경계 라벨 후보

| 도메인 | 라벨 | 근거 URL | 만료 상태 | 메모 |
|---|---|---|---|---|
| openload.co | 경계 | https://en.wikipedia.org/wiki/Openload | **등록됨(비만료)** — Registry Expiry 2028-07-29 | 파일 공유 서비스, 저작권 단체(ACE) 법적 조치로 2019년 폐쇄. 정상 콘텐츠와 저작권 침해 콘텐츠가 혼재했던 경계 사례. 도메인은 등록 유지 중(파킹 여부 미확인) |
| friendster.com | 경계 | (공개적으로 널리 알려진 이력 — 소셜네트워크 2002~2011 → 2011년 이후 소셜게임 플랫폼으로 전면 전환 → 2015년 서비스 완전 종료) | **등록됨(비만료)** — Registry Expiry 2030-03-22 | 주제가 "소셜네트워크"에서 "게임 플랫폼"으로 크게 바뀐 뒤 폐쇄된 대표 사례. 현재 도메인 상태(파킹/리다이렉트 여부)는 직접 접속 확인 필요(이번 조사에서 미실시) |
| orkut.com | 경계 | (Google 소유, 2004~2014 서비스, 2014-09 완전 종료 — 공개적으로 널리 알려짐) | **등록됨(비만료)** — Registry Expiry 2026-12-08 | Google이 방어적으로 보유 중인 것으로 추정. 장기간 서비스 없이 도메인만 유지된 사례, 접속 상태 미확인 |
| delicious.com | 경계 | (2011년 Yahoo→AVOS 매각, 2014년 재매각, 2017년 또 매각 — 소유권·운영방침이 여러 차례 바뀐 이력이 널리 보도됨) | **등록됨(비만료)** — Registry Expiry 2034-11-19 | 북마킹 서비스에서 여러 차례 리브랜딩·재런칭을 거친 "주제 유지되나 운영주체·품질 급변" 경계 사례 |

**부족분 메모**: 목표(5~10개) 대비 4개로 하단 미달. 장기 파킹 이력이 뚜렷한 사례(수년간 광고 파킹 페이지로만 존재하다 재활용된 도메인)는 이번 검색에서 개별 사례를 못 찾음 — expireddomains.net이나 SpamZilla 같은 유료/가입형 도구에서 "파킹 이력 5년+" 필터로 직접 뽑는 편이 웹검색보다 효율적일 것으로 보임(다음 조사자 메모).

---

## 사람 확인 필요 항목 체크리스트

- [ ] 위험 4건 — Wayback으로 압류 배너·사기 콘텐츠 스냅샷 육안 재확인
- [ ] freechal.com whois 재조회(다른 도구·시간대에 재시도)
- [ ] 경계 4건(openload.co / friendster.com / orkut.com / delicious.com) 실제 현재 접속 상태(파킹 페이지 여부) 확인 — 이번 조사는 whois만 실시, 실접속은 지침상 금지된 항목 아니나 시간 제약으로 미실시
- [ ] 위험·경계 라벨 부족분(각 6개, 3개 이상) 추가 조달 — URLhaus API 직접 질의, PhishTank CSV 아카이브, expireddomains.net 파킹 필터 권장

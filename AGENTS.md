<!-- project-map:start -->
이 프로젝트는 Project Map을 사용한다. 공용 상태는 .project-map/state.json이다.
작업 시작·계획 변경·마일스톤 완료·막힘·인계 시 아래 명령으로 상태를 읽고 바뀐 항목만 갱신한다.
`node "$HOME/.local/share/project-map/cli.cjs" status --root .`
갱신 명령과 스키마: 같은 CLI의 `help`. 사용자 변경을 먼저 반영하고 완료 근거를 적는다.
'완성 기준' 작업은 노트의 확인 방법을 실제로 해 본 결과를 근거로 적어야 완료다. 제품인데 '완성 기준'이 없으면 ~/.agents/work-methods/completeness/done.md대로 넣는다.
STANDARDS.md가 있으면 작업을 시작할 때 읽고 그 표준의 규칙을 지켜서 만든다.
지도는 기존 요청의 진행 기록이며 새 작업의 허가가 아니다. HTML은 자동 생성된다.
<!-- project-map:end -->

## 도메인과 브랜딩

이 프로젝트에서 도메인 검토 요청은 [kh-skill-domain](.agents/skills/kh-skill-domain/SKILL.md), 브랜드 전략·이름·표현 요청은 [kh-skill-branding](.agents/skills/kh-skill-branding/SKILL.md)을 읽고 수행한다. 신규 브랜드와 도메인을 함께 고르면 두 스킬을 필요한 범위에서 연결한다.

도메인 등록·입찰·백오더·구매·결제는 사용자가 직접 한다. 등록 가능 여부·상표·과거 이력·비용을 구분하며 조회하지 못한 사실은 미확인으로 남긴다.

스킬과 참조 문서를 고치면 `make check-skills`로 연결을 확인한다. 기존 앱 코드까지 바꾸면 `make check`로 관련 회귀도 확인한다. 사용 예시는 [도메인과 브랜딩 도움받기](docs/domain-branding.md)에 있다.

## 기업명 선정의 지속 상태

이 사용자의 기업명·그룹명·신규 도메인을 다룰 때는 [기업명 선정 기준](branding/BRIEF.md)과 [후보 기록](branding/candidates.json)을 먼저 읽는다. 이름의 매력을 먼저 판단하고 확보 조건을 검사한다. 후보 생성·조회·사용자 평가 때 원장을 갱신하고, 거절·철회된 이름을 재추천하지 않는다. 사용자 확정 전에는 선정 완료로 기록하지 않는다.

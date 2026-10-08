<!-- project-map:start -->
이 프로젝트는 Project Map을 사용한다. 공용 상태는 .project-map/state.json이다.
작업 시작·계획 변경·마일스톤 완료·막힘·인계 시 아래 명령으로 상태를 읽고 바뀐 항목만 갱신한다.
`node "$HOME/.local/share/project-map/cli.cjs" status --root .`
갱신 명령과 스키마: 같은 CLI의 `help`. 사용자 변경을 먼저 반영하고 완료 근거를 적는다.
'완성 기준' 작업은 노트의 확인 방법을 실제로 해 본 결과를 근거로 적어야 완료다. 제품인데 '완성 기준'이 없으면 ~/.agents/work-methods/completeness/done.md대로 넣는다.
STANDARDS.md가 있으면 작업을 시작할 때 읽고 그 표준의 규칙을 지켜서 만든다.
지도는 기존 요청의 진행 기록이며 새 작업의 허가가 아니다. HTML은 자동 생성된다.
<!-- project-map:end -->

도메인과 브랜딩 업무의 공통 진입점·구매 경계·검증 명령은 [AGENTS.md](AGENTS.md)의 ‘도메인과 브랜딩’을 따른다.

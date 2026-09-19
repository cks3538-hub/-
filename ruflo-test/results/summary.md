# ruflo 샘플 테스트 요약

- 실행 시각: 2026-09-19 15:49:44
- ruflo 버전: ruflo v3.42.4
- Node: v22.22.2
- 결과: **PASS 27 / FAIL 0 / 총 27**

| # | 단계 | 결과 | 소요 |
|---|------|------|------|
| 01 | 버전 확인 | PASS | 318ms |
| 02 | 시스템 진단 (doctor) | PASS | 1345ms |
| 03 | 초기화 상태 확인 | PASS | 540ms |
| 04 | 스웜 초기화 (hierarchical, 최대 5) | PASS | 551ms |
| 05 | 에이전트 생성: researcher | PASS | 608ms |
| 06 | 에이전트 생성: coder | PASS | 601ms |
| 07 | 에이전트 생성: tester | PASS | 597ms |
| 08 | 에이전트 목록 | PASS | 599ms |
| 09 | 작업 생성: 리서치 | PASS | 586ms |
| 10 | 작업 생성: 구현 | PASS | 573ms |
| 11 | 작업 생성: 테스트 | PASS | 581ms |
| 12 | 작업 목록 (전체) | PASS | 576ms |
| 13 | 작업 라우팅 (Q-Learning) | PASS | 563ms |
| 14 | 라우팅 가능 에이전트 목록 | PASS | 572ms |
| 15 | 메모리 저장 1 | PASS | 731ms |
| 16 | 메모리 저장 2 | PASS | 743ms |
| 17 | 메모리 저장 3 | PASS | 720ms |
| 18 | 메모리 의미 검색: 손익 | PASS | 685ms |
| 19 | 메모리 목록 | PASS | 712ms |
| 20 | 훅 pre-task | PASS | 584ms |
| 21 | 훅 목록 | PASS | 578ms |
| 22 | 코드 분석: 심볼 추출 | PASS | 564ms |
| 23 | 코드 분석: 복잡도 | PASS | 593ms |
| 24 | 샘플 단위 테스트 (node --test) | PASS | 140ms |
| 25 | 워크플로 목록 | PASS | 573ms |
| 26 | 시스템 상태 | PASS | 789ms |
| 27 | 스웜 상태 | PASS | 551ms |

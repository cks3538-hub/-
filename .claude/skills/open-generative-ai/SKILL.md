---
name: open-generative-ai
description: "Open Generative AI(muapi 게이트웨이)로 이미지·영상·음악·립싱크를 생성하는 절차. '이미지 만들어줘', '사진 생성', '썸네일 만들어줘', '영상 만들어줘', '동영상 생성', '립싱크', '배경 제거', '누끼 따줘', '업스케일', '화질 개선', '얼굴 합성', '음악 만들어줘', '로고 만들어줘', 'flux', 'kling', 'veo', 'sora', 'midjourney', 'nano banana', 'seedance' 요청 시 사용. MCP·CLI·데스크톱 앱 세 가지 경로를 구분해서 안내한다. 생성마다 크레딧이 소모되므로 대량 생성 전 반드시 확인받는다."
---

# Open Generative AI (muapi)

400개 이상의 이미지·영상·오디오 모델을 하나의 게이트웨이(muapi.ai)로 호출하는 오픈소스 스택이다.
본체는 [Anil-matcha/Open-Generative-AI](https://github.com/Anil-matcha/Open-Generative-AI) (MIT).

설치·키 발급 절차는 `docs/OPEN-GENERATIVE-AI.md`에 있다. 사용자가 "설치하고 싶다"고 하면 그 문서를 안내한다.

## 0. 가장 먼저 확인할 것 — 비용

**muapi는 생성 1건마다 크레딧(유료)이 소모된다.** 앱과 코드는 무료(MIT)지만 모델 호출은 종량제다.

| 상황 | 행동 |
|---|---|
| 1~3건 생성 | 바로 진행 |
| 4건 이상, 또는 영상 생성 | **먼저 예상 건수를 알리고 승인받는다** |
| 반복 루프·배치 스크립트 | **반드시 승인받는다.** 무한 루프 금지 |
| 잔액이 궁금할 때 | `muapi_account_balance` 호출 |

`muapi_account_topup`(충전), `muapi_keys_create` / `muapi_keys_delete`(키 발급·삭제)는
**사용자가 명시적으로 요청할 때만** 호출한다. 절대 자동 실행하지 않는다.

## 1. 경로 3가지 — 상황에 맞게 고른다

| 경로 | 쓰는 때 | 진입점 |
|---|---|---|
| **MCP** (기본) | Claude Code 대화 중에 바로 생성 | `.mcp.json`의 `muapi` 서버 → `muapi_*` 툴 |
| **CLI** | 스크립트·배치·파일 저장이 필요할 때 | `muapi` 명령 (`npm install -g muapi-cli`) |
| **데스크톱 앱** | 사람이 직접 눈으로 고르며 작업할 때 | Open Generative AI 앱 (GUI) |

이 저장소는 **MCP 경로가 기본**이다. `MUAPI_API_KEY` 환경변수가 설정돼 있어야 동작한다.

## 2. MCP 툴 목록

| 분류 | 툴 |
|---|---|
| 모델 탐색 | `search_models` |
| 이미지 | `muapi_image_generate`, `muapi_image_edit` |
| 영상 | `muapi_video_generate`, `muapi_video_from_image` |
| 오디오 | `muapi_audio_create`, `muapi_audio_from_text` |
| 보정 | `muapi_enhance_upscale`, `muapi_enhance_bg_remove`, `muapi_enhance_face_swap`, `muapi_enhance_ghibli` |
| 영상 편집 | `muapi_edit_lipsync`, `muapi_edit_clipping` |
| 비동기 조회 | `muapi_predict_result` |
| 계정 | `muapi_account_balance`, `muapi_account_topup`, `muapi_keys_list`, `muapi_keys_create`, `muapi_keys_delete` |

## 3. 표준 작업 순서

1. **모델 고르기** — 사용자가 모델명을 말하지 않았으면 `search_models`로 후보를 찾는다. 아래 치트시트로 바로 골라도 된다.
2. **프롬프트 다듬기** — 한글 요청은 영어 프롬프트로 옮긴다. 피사체 → 동작 → 배경 → 조명 → 화풍 순으로 구체적으로 쓴다.
3. **생성 호출** — 이미지는 대체로 즉시, 영상은 비동기다.
4. **비동기 폴링** — `request_id`를 받으면 `muapi_predict_result`로 `completed`가 될 때까지 조회한다. 영상은 수십 초~수 분 걸린다. 조회 간격을 두고, 무한 재시도하지 않는다.
5. **결과 전달** — 반환된 URL을 사용자에게 전달한다. 로컬 저장이 필요하면 CLI의 `--download` 또는 `curl`을 쓴다.

## 4. 모델 치트시트

| 목적 | 1순위 모델 | 비고 |
|---|---|---|
| 범용 이미지 | `flux-dev` | 품질·속도 균형이 가장 좋다 |
| 사진 같은 결과물 | Seedream 5.0, Ideogram v3 | Ideogram은 글자 렌더링에 강하다 |
| 일러스트·아트 | Midjourney v7 | |
| 이미지 부분 수정 | Nano Banana 2 Edit, Flux Kontext Pro | 참조 이미지 최대 14장 |
| 범용 영상 | `kling-master` / Kling v3 | |
| 최고 품질 영상 | Sora 2, Veo 3 | 크레딧 소모가 크다 |
| 사진 → 영상 | Kling v2.1 I2V, Veo3 I2V | `muapi_video_from_image` |
| 립싱크 | Infinite Talk, LatentSync | `muapi_edit_lipsync` |
| 음악·효과음 | Lyria 3 | `muapi_audio_create` |

## 5. CLI 경로

```bash
muapi image generate "a serene mountain lake at sunrise" --model flux-dev --download ./outputs
muapi video generate "a dog running on a beach" --model kling-v3.0-pro --duration 10
muapi audio create "upbeat lo-fi hip hop for studying"
```

에이전트가 결과를 파싱해야 하면 `--output-json`을 붙인다. `| jq '.data.url'`로 URL만 뽑을 수 있다.

## 6. 절대 하지 말 것

- **API 키를 저장소에 커밋하지 않는다.** `.mcp.json`은 `${MUAPI_API_KEY}` 치환만 쓴다. 키 원문을 파일·커밋 메시지·PR 본문·로그에 남기지 않는다.
- 사용자가 준 키를 대화에 그대로 되풀이해 출력하지 않는다.
- 승인 없이 충전(`muapi_account_topup`)이나 키 삭제(`muapi_keys_delete`)를 실행하지 않는다.
- 이 스택에는 콘텐츠 필터가 없다. 실존 인물 사칭, 타인의 얼굴을 동의 없이 합성(`face_swap`), 허위 사실을 진짜처럼 보이게 만드는 생성물은 만들지 않는다.

## 7. 로컬 모델 (키 없이 무료)

데스크톱 앱 한정으로 sd.cpp 엔진이 내장돼 있어 **API 키 없이 오프라인 이미지 생성**이 가능하다.
앱에서 **Settings → Local Models** → 엔진 설치 → 모델 다운로드 → Image Studio의 **⚡ Local** 토글.

| 모델 | 크기 | 비고 |
|---|---|---|
| Dreamshaper 8 (SD 1.5) | 2.1 GB | 맥에서 가장 가볍다. 첫 테스트용으로 추천 |
| Realistic Vision v5.1 | 2.1 GB | 사진풍 |
| Anything v5 | 2.1 GB | 애니메·일러스트 |
| SDXL Base 1.0 | 6.9 GB | 고해상도 |
| Z-Image Turbo / Base | 2.5~3.5 GB + 보조 3.1 GB | 메모리를 많이 쓴다 |

영상 로컬 생성은 별도 GPU 서버(Wan2GP)가 필요하다. NVIDIA/AMD GPU가 없으면 권하지 않는다.

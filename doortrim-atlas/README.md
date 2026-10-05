# doortrim-atlas — 도어트림 디자인 스킨 자동 부품분할 · 사이즈/중량 예측 (완전 오프라인)

디자인팀이 배포한 **통짜 도어트림 스킨(STL)** 1건을 넣으면,

1. 기존 CATIA 분석 DB(과거 양산 차종의 Body별 STL + 표준부품코드)를 **아틀라스**로 만들어 두고
2. 그 아틀라스들을 신규 스킨에 정렬한 뒤 **라벨을 옮겨 붙여** 스킨을 부품 단위로 나누고
3. 부품별 **면적·치수·예측중량·재질(·원가)** 와 **스킨에 안 보이는 부품의 중량**까지 돌려줍니다.

GPU, 딥러닝 가중치 다운로드, 외부 네트워크가 **전혀 필요 없습니다.** numpy / scipy / scikit-learn / trimesh / matplotlib 만 씁니다.

> 설치는 [INSTALL_OFFLINE.md](INSTALL_OFFLINE.md), 왜 이 방식인지와 사내 보고서의 4개 질문에 대한 답은
> [docs/분석보고서.md](docs/분석보고서.md), 사내 AI(알렉스)가 기존 파이프라인에 붙이는 방법은
> [docs/알렉스_연동가이드.md](docs/알렉스_연동가이드.md) 를 보세요.

---

## 1. 왜 지금까지 안 됐고, 이 방식은 왜 되는가 (요약)

지금까지의 시도(SAM2 50.5%, 밝기 Region-Growing 41.5%, 메쉬 연결성/곡률 15% 미만, CATIA 설계이력 추적 실패)는
전부 **"스킨 안에서 부품 경계를 찾는다"** 는 같은 문제를 풀고 있었습니다. 그런데 매끈하게 이어진 트림 패널에는
경계 정보가 **입력에 존재하지 않습니다.** 사진에도, 메쉬에도, (양산 데이터에는) 설계 이력에도 없습니다.
없는 정보를 찾는 방법은 어떤 모델을 가져와도 상한이 낮습니다.

이 도구는 문제를 바꿉니다. **"과거 36개 차종에서 이 자리는 어느 부품이었나?"** — 도어트림은 차종이 달라도
부품 배치가 거의 같습니다(암레스트는 중앙, 맵포켓은 하단 전방, 어퍼트림은 벨트라인 아래 …). 이미 라벨이 붙어 있는
과거 차종(아틀라스)을 신규 스킨에 정렬하고, 점마다 가장 가까운 아틀라스 점의 라벨을 투표로 가져옵니다
(의료영상에서 장기를 나눌 때 쓰는 **multi-atlas segmentation** 과 같은 원리). 여기에 "이 위치의 이런 형상은
보통 어떤 부품"을 배운 점 분류기를 섞어 작은 부품을 보강합니다.

```
  과거 차종 Body STL (라벨 있음)                신규 디자인 스킨 STL (라벨 없음)
  ────────────────────────────                  ─────────────────────────────
  조립 상태로 합침 → A면(실내에서 보이는 면)만     표면 점 샘플링 → 보이는 면만 남김
  → 공통 좌표계(u 앞뒤, v 상하, w 실내쪽)로 정규화   → 8가지 자세 가설 중 아틀라스와 가장 잘 맞는 것 선택
  → 점구름 + 라벨 + 기하특징 저장  = 아틀라스        → 유사 아틀라스 상위 k개 선택
                                                  → 아틀라스별 정렬(ICP) + 부품별 국소 이동 보정
                                                  → 이웃 라벨 투표(확률) ⊗ 점 분류기 확률
                                                  → 평활·작은 조각 정리 → 점/면 라벨
                                                  → 부품별 면적·bbox → 중량(최근접+면적밀도+회귀 중앙값)
                                                  → 숨은 부품은 유사 차종 BOM 가중평균
```

## 2. 검증 결과 (합성 데이터 — 실데이터 검증은 사내에서 `evaluate` 로)

사내 데이터는 반출할 수 없으므로, 부품 배치·치수·곡률·좌표계·좌우(거울상)가 조금씩 다른 **합성 도어트림**을 만들어
파이프라인 전체를 leave-one-vehicle-out 으로 검증했습니다(한 차종을 빼고 아틀라스를 만든 뒤, 뺀 차종을 임의 회전·
이동시켜 신규 스킨처럼 넣고 정답과 비교).

| 조건 | 점 단위 정확도 | 부품 IoU 평균 | 비고 |
|---|---:|---:|---|
| 합성 14대, 13폴드 | **94.1 %** | **0.775** | 아래 코드별 표 |
| 합성 8대(아틀라스 7개뿐), 다른 시드 | 95.0 % | 0.805 | 아틀라스가 적어도 유지 |
| (참고) 사내 SAM2 box-prompt, 26부품 | – | 0.505 | 사내 보고서 3-1 |

코드별(14대 13폴드): MAP_POCKET 0.99 · PULL_HANDLE 0.96 · UPR_TRIM 0.90 · ARMREST 0.90 · MAIN_TRIM 0.89 ·
HANDLE_BEZEL 0.74 · SPEAKER_GRILLE 0.59 · SAIL_GARNISH 0.54 · SWITCH_BEZEL 0.46

* **매끈하게 이어진 MAIN_TRIM / UPR_TRIM 경계**(사내 미해결 핵심 과제)가 IoU 0.89~0.90 으로 나뉩니다. 경계를 찾은 것이
  아니라 "과거 차종들이 이 높이에서 나눴다"는 배치를 옮겨 온 것입니다.
* 약한 곳은 **거의 평평하게 붙은 작은 부품**(스위치 베젤, 세일 가니시: 돌출 2~3 mm)입니다. 위치 편차가 크면 놓칠 수 있습니다.
* 보류 차종 1대 중량 예측: 8개 부품 **오차 중앙값 13 %** (사내 기존 형상유사도 매칭 17~28 %, 그것도 부품유형을 안다는 전제).
* 처리 시간(노트북 CPU): 스킨 1건 분할+예측 10~15초, 아틀라스 1대 구축 1~2초(합성; 실제 CATIA STL은 크기에 따라 더 걸림).

**이 숫자는 합성 데이터 기준입니다.** 실제 수치는 사내 PC에서 `evaluate` 명령(leave-one-vehicle-out)으로 바로 재현할 수 있고,
그 숫자가 사내 SAM2 50.5% 와 같은 기준(부품별 IoU)으로 비교 가능한 진짜 성적입니다.

## 3. 사용 순서 (사내 PC)

설치가 끝났다면(→ INSTALL_OFFLINE.md) `doortrim-atlas` 폴더에서 명령 프롬프트를 열고 순서대로 실행합니다.

```bat
:: 0) 설치 확인 — 합성 데이터로 전체 파이프라인이 끝까지 도는지 (2~3분)
python -m doortrim_atlas demo --out demo_out

:: 1) 사내 DB 스키마 확인 + manifest 용 SQL 자동 추측
python -m doortrim_atlas inspect-db --db "D:\도어트림_형상유사도검색\catia_parts.db"

:: 2) DB → manifest.csv  (추측 SQL 이 맞으면 그대로, 틀리면 --sql my.sql 로 직접 지정)
python -m doortrim_atlas make-manifest --db "D:\...\catia_parts.db" --out manifest.csv --stl-root "D:\도어트림_형상유사도검색\카티아부품DB"

:: 3) 아틀라스 라이브러리 구축 (70개 완제품 = 70 아틀라스; 중간 저장되므로 끊겨도 --append 로 이어감)
python -m doortrim_atlas build-atlas --manifest manifest.csv --out library --preview 6
::    → library\previews\*.png 를 열어 "암레스트가 보이고, 맵포켓이 아래, 실내쪽(w)이 +" 인지 눈으로 확인

:: 4) 점 분류기 학습 (1~2분)
python -m doortrim_atlas train --library library

:: 5) 정확도 평가 (차종 하나씩 빼고 재현; 70폴드면 10~20분, --max-folds 10 으로 먼저 확인)
python -m doortrim_atlas evaluate --library library --max-folds 10
::    → library\evaluation\evaluation_summary.json, evaluation.csv, fold_*.png(예측 vs 정답 그림)

:: 6) 신규 디자인 스킨 분할 + 예측
python -m doortrim_atlas segment --library library --skin "D:\신규\GL3_PE2_skin.stl" --out result_GL3_PE2
::    스킨을 Geometrical Set 별로 따로 STL 로 내보냈다면 폴더를 넘기면 됨:  --skin "D:\신규\GL3_PE2_pieces"
```

### 신규 스킨 STL 만들기 (CATIA)

디자인 1차 모델은 Body 가 아니라 HybridBody(Geometrical Set) 8개 안에 서피스로 들어 있었습니다. 이 도구는 **면 조각이
몇 개로 쪼개져 있든, 어떤 작업 서피스가 섞여 있든 상관없이** "표면 위의 점"만 보므로 별도 정리 없이 STL 로 내보내면 됩니다.

* 가장 간단: CATIA 에서 작업용(숨겨도 되는) 서피스는 Hide, 최종 외관면만 Show 한 상태에서 **File > Save As > 파일 형식 stl**.
* 자동화: `scripts/catia_export_skin.py` (pywin32) — 전체를 STL 1개로, 또는 `--per-set` 으로 Geometrical Set 별 STL 폴더로.
  폴더로 넘기면 "한 파일은 대부분 한 부품"이라는 조각 단위 다수결이 추가로 적용됩니다.
* STL 해상도: Tools > Options > General > Display > Performance > 3D Accuracy 를 **0.2 mm(fixed)** 정도로.
* 숨은 작업 서피스가 섞여 들어가도 가시성 필터가 실내에서 안 보이는 면을 자동으로 버립니다(`skin_visibility`).

## 4. 출력물

`segment --out result_X` 폴더:

| 파일 | 내용 |
|---|---|
| `preview.png` | 정면(u-v)과 깊이(u-w) 산점도, 부품별 색 |
| `skin_labeled.ply` | 면마다 부품 색을 입힌 메쉬 — Windows 3D 뷰어, MeshLab 등에서 열림 |
| `parts/<코드>.stl` | 부품별로 잘라낸 메쉬 (CATIA 로 다시 가져가 확인 가능) |
| `parts.csv` | 코드, 보이는 면적, 전체 면적, L×H×D(공통 좌표축 기준 mm), 신뢰도, 면 수 |
| `segmentation.json` | 좌표계(frame), 자세 가설 점수, 유사 차종 순위, 아틀라스별 정렬 정보, 부품별 통계(원좌표 bbox·중심 포함) |
| `prediction.csv` / `prediction.json` / `prediction_summary.md` | 부품별 예측중량(최근접·면적밀도·회귀와 그 중앙값), 재질, 참조한 과거 부품, 숨은 부품 추정, 합계 |
| `points_labeled.npz` | 샘플 점(mm/정규화)과 라벨·확률 — 후속 분석용 |
| `log.txt` | 실행 로그 |

## 5. 설정 바꾸기

`--config my.json` 으로 아무 항목이나 덮어쓸 수 있습니다(전체 목록과 설명은 `doortrim_atlas/config.py`).

```json
{ "k_atlas": 10, "alpha_atlas": 0.5, "smooth_iters": 1, "cleanup_size_coef": 0.1, "cabin_dir": "auto" }
```

| 증상 | 조정 |
|---|---|
| `build-atlas` 미리보기에서 리브가 있는 뒷면이 보임(실내쪽이 뒤집힘) | 기준(첫) 아틀라스가 문제: `"cabin_dir": "-y"` 처럼 차량 좌표계에서 실내 방향을 직접 지정하고 다시 구축 |
| 작은 부품이 자꾸 큰 부품에 먹힘 | `alpha_atlas` 0.4 로(분류기 비중↑), `cleanup_size_coef` 0.05, `smooth_iters` 0 |
| 결과가 얼룩덜룩(노이즈) | `smooth_iters` 2~3, `k_nn` 15 |
| 차종 간 배치 차이가 큼 | `nonrigid_search` 8~10, `k_atlas` 12 |
| 메모리/시간 부족 | `raw_samples` 150000, `atlas_points` 20000, `skin_points` 25000 |
| 좌표계가 차량 좌표계가 아님 | 상관없음(8가지 자세 가설을 아틀라스와 비교해 자동 선택). 단 첫 아틀라스는 `cabin_dir` 로 확인 |

## 6. 한계와 다음 단계

* 정확도는 **"과거 차종들이 그 자리에 무엇을 뒀는가"** 의 일관성에 비례합니다. 완전히 새로운 배치(예: 암레스트를 없앤 컨셉)는
  가장 비슷한 과거 차종의 배치로 "근사"됩니다 — 요구하신 "정확하지 않아도 유사한 결과"에 해당합니다.
* 스킨에 **안 보이는 부품**(브래킷, 클립, 뒷면 리브)은 기하로 알 수 없으므로 유사 차종 BOM 평균으로만 채웁니다.
* 두께 기반 성능 합부 판정은 스킨에 두께가 없으므로, 예측 재질/유사 차종의 두께를 넘겨 기존 규칙 모듈이 판정하게 하는
  것을 권장합니다(알렉스 가이드 참고).
* 다음 단계 제안: (1) 사내 DB 70개로 `evaluate` 실행 → 실제 IoU 확인, (2) 코드별 IoU 가 낮은 부품은 `library/previews` 와
  `fold_*.png` 로 원인 확인(라벨 오류/배치 편차/가려짐), (3) 디자인팀에 "Geometrical Set 이름에 부품명 또는 최소한 1 Set = 1 부품"
  을 요청하면 조각 단위 다수결로 경계가 거의 정확해집니다.

## 7. 폴더 구조

```
doortrim-atlas/
├─ README.md, INSTALL_OFFLINE.md, requirements.txt, pyproject.toml
├─ docs/분석보고서.md            왜 이 방식인가, 4개 질문 답변, 실데이터 적용 시 주의
├─ docs/알렉스_연동가이드.md     사내 AI 가 기존 파이프라인에 붙이는 법(API·JSON 스키마)
├─ scripts/catia_export_skin.py  CATIA → STL (pywin32)
├─ tests/test_pipeline.py        회귀 테스트 (python -m pytest tests)
└─ doortrim_atlas/
   ├─ cli.py        명령줄 (inspect-db, make-manifest, build-atlas, train, evaluate, segment, synth, demo)
   ├─ config.py     설정값
   ├─ dbtools.py    사내 SQLite → manifest.csv
   ├─ geometry.py   메쉬 읽기, 표면 샘플링, 가시성(z-buffer) 필터, 솔리드/서피스 판정
   ├─ pose.py       PCA 자세 정규화, 8가지 부호 가설, 공통 좌표계(Frame)
   ├─ features.py   점 기하특징(위치, 법선, 돌출, 거칠기, 평면성, 외곽거리)
   ├─ atlas.py      아틀라스 구축/저장/불러오기, 자세 선택
   ├─ transfer.py   챔퍼 유사도, ICP, 부품별 국소 정렬, 라벨 전이
   ├─ classifier.py 점 분류기(ExtraTrees)
   ├─ fuse.py       확률 융합(로그선형), 평활, 크기 기준 조각 정리
   ├─ segment.py    신규 스킨 분할(점→면), 부품 통계
   ├─ predict.py    중량·재질·원가·숨은 부품 예측
   ├─ evaluate.py   leave-one-vehicle-out 평가
   ├─ visualize.py  PNG/PLY/부품별 STL
   └─ synthetic.py  합성 도어트림 생성기(테스트/데모)
```

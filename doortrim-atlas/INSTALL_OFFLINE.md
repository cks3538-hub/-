# 오프라인 설치 안내 (집에서 받아서 → 사내 PC로 옮겨서 설치)

사내 PC는 GitHub·pip 서버에 접속할 수 없으므로, **집 PC에서 필요한 파일을 전부 내려받아 압축한 뒤** 사내 반입 절차로
옮겨 설치합니다. 아래 순서대로 하시면 됩니다. (총 소요: 집 10분, 사내 10분)

---

## 0단계. 사내 PC의 파이썬 버전 확인 (1분)

1. 사내 PC에서 키보드의 **Windows 키**를 누르고 `cmd` 라고 입력한 뒤 **Enter** → 검은 창(명령 프롬프트)이 열립니다.
2. 아래 두 줄을 한 줄씩 복사해 붙여넣고 Enter:
   ```bat
   python --version
   python -c "import struct; print(struct.calcsize('P')*8, 'bit')"
   ```
3. 나온 결과를 메모합니다. 예: `Python 3.11.4` / `64 bit`.
   * `python`이 없다고 나오면 사내 AI(알렉스)가 쓰는 파이썬 경로를 확인해 그 경로의 `python.exe` 로 대신 실행하세요
     (SAM2 를 돌렸다면 파이썬은 이미 있습니다).
4. 이미 설치된 패키지도 확인(있으면 그만큼 덜 받아도 됩니다):
   ```bat
   python -c "import numpy, scipy, sklearn, matplotlib; print('numpy', numpy.__version__, 'scipy', scipy.__version__, 'sklearn', sklearn.__version__, 'mpl', matplotlib.__version__)"
   python -c "import trimesh; print('trimesh', trimesh.__version__)"
   ```

## 1단계. 집 PC에서 도구 내려받기 (2분)

**방법 A — 브라우저에서 ZIP 받기(권장)**

1. 브라우저에서 다음 주소를 엽니다: <https://github.com/cks3538-hub/-/tree/claude/modest-franklin-cki8ia/doortrim-atlas>
2. 화면 왼쪽 위 브랜치 이름이 `claude/modest-franklin-cki8ia` 인지 확인합니다.
3. 전체 ZIP 은 이 주소로 바로 받습니다: <https://github.com/cks3538-hub/-/archive/refs/heads/claude/modest-franklin-cki8ia.zip>
4. 받은 ZIP 을 풀면 `--claude-modest-franklin-cki8ia\doortrim-atlas` 폴더가 있습니다. 이 `doortrim-atlas` 폴더만 쓰면 됩니다.
   (저장소 main 에 합쳐진 뒤에는 <https://github.com/cks3538-hub/-> 의 초록색 **Code ▾ → Download ZIP** 으로도 됩니다.)

**방법 B — git 으로 받기**
```bat
git clone -b claude/modest-franklin-cki8ia https://github.com/cks3538-hub/-.git
```

## 2단계. 집 PC에서 파이썬 패키지(휠) 내려받기 (5분)

집 PC 에 파이썬(아무 버전)과 인터넷이 있으면 됩니다.

1. 1단계에서 푼 `doortrim-atlas` 폴더를 탐색기에서 엽니다.
2. 폴더 안 빈 곳에서 **Shift + 마우스 오른쪽 클릭 → "여기에 PowerShell 창 열기"** (또는 주소창에 `cmd` 입력 후 Enter).
3. 아래 명령을 **0단계에서 메모한 사내 파이썬 버전**에 맞게 숫자(`3.11`)만 바꿔 붙여넣고 Enter:
   ```bat
   python -m pip download -r requirements.txt -d wheels --platform win_amd64 --python-version 3.11 --only-binary=:all:
   ```
   * `wheels` 폴더가 생기고 `.whl` 파일 십여 개가 들어갑니다(numpy, scipy, scikit-learn, trimesh, matplotlib 과 그 의존성).
   * 사내가 32bit 였다면 `win_amd64` 대신 `win32` 로 바꿉니다.
   * CATIA 자동 내보내기 스크립트까지 쓰려면 한 줄 더:
     ```bat
     python -m pip download pywin32 -d wheels --platform win_amd64 --python-version 3.11 --only-binary=:all:
     ```
   * 혹시 "No matching distribution" 이 나오면 `--python-version` 숫자가 사내 버전과 다른 것입니다. 다시 확인하세요.
4. `doortrim-atlas` 폴더 전체(안에 `wheels` 포함)를 마우스 오른쪽 클릭 → **압축(ZIP)**. 이 ZIP 하나만 반입하면 됩니다.

## 3단계. 사내 PC로 옮기기

회사 반입 절차(USB, 반입 승인 시스템 등)로 2단계 ZIP 을 옮긴 뒤, 예를 들어 `D:\도구\doortrim-atlas` 에 풀어 둡니다.

## 4단계. 사내 PC에 설치 (2분)

1. 탐색기에서 `D:\도구\doortrim-atlas` 를 열고 주소창에 `cmd` 입력 → Enter.
2. 아래를 붙여넣고 Enter (인터넷 없이 `wheels` 폴더에서만 설치합니다):
   ```bat
   python -m pip install --no-index --find-links wheels -r requirements.txt
   ```
   * 이미 설치된 패키지는 건너뜁니다. 사내 보안 정책상 `pip` 가 막혀 있으면 알렉스/전산팀에 "오프라인 휠 설치" 로 요청하세요.
3. 확인:
   ```bat
   python -c "import numpy, scipy, sklearn, trimesh, matplotlib; print('OK')"
   ```
   `OK` 가 나오면 끝입니다.

## 5단계. 동작 확인 — 데모 실행 (3분)

같은 창에서:
```bat
python -m doortrim_atlas demo --out demo_out
```
합성 차종 12대를 만들어 아틀라스 구축 → 분류기 학습 → 평가 → 신규 스킨 분할·예측까지 돌고, 마지막에
"점 정확도 0.9x, 부품 IoU 평균 0.7x" 와 부품별 예측/정답 표가 나옵니다. `demo_out\result_SYN11\preview.png` 를
더블클릭해 그림을 확인하세요(암레스트 주황, 맵포켓 연두, 어퍼트림 하늘색).

여기까지 되면 README.md 의 "3. 사용 순서" 로 넘어가 실제 DB 로 진행합니다.

---

## 자주 겪는 문제

| 증상 | 해결 |
|---|---|
| `python` 을 찾을 수 없음 | 알렉스가 쓰는 파이썬의 전체 경로로 실행: `"C:\...\python.exe" -m doortrim_atlas ...` |
| 설치 중 `No matching distribution found` | 2단계에서 `--python-version`/플랫폼이 사내와 다름. 0단계 다시 확인 후 재다운로드 |
| 그림(PNG)의 한글이 네모로 나옴 | 한글 글꼴(맑은 고딕)이 없는 PC. 자동으로 영문 제목으로 바뀌므로 기능에는 문제 없음 |
| `MemoryError` | `--config` 로 `raw_samples`, `skin_samples` 를 절반으로 |
| STL 을 읽을 수 없음 | CATIA 에서 바이너리/ASCII STL 둘 다 됨. 파일이 0 바이트인지, 경로에 특수문자가 있는지 확인 |
| 매우 큰 STL(수백 MB) | 3D Accuracy 를 0.5 mm 로 올려 다시 내보내기. 정확도에는 거의 영향 없음 |

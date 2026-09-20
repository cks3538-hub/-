# Open Generative AI 설치·설정 가이드

이미지·영상·음악을 AI로 만드는 무료 오픈소스 스택이다. 모델 400개 이상을 한 곳에서 쓴다.

- 본체: [github.com/Anil-matcha/Open-Generative-AI](https://github.com/Anil-matcha/Open-Generative-AI) (MIT 라이선스)
- 모델 게이트웨이: [muapi.ai](https://muapi.ai)

> **비용 안내 (먼저 읽어주세요)**
> 앱과 소스코드는 **무료**입니다. 하지만 클라우드 모델을 호출할 때마다 **muapi 크레딧이 소모**됩니다(종량제).
> 돈을 한 푼도 쓰고 싶지 않다면 → **5단계(로컬 모델)** 로 바로 가세요. 키 없이 무료로 이미지 생성이 됩니다.

---

## 어떤 걸 설치할지 고르기

세 가지 중 **필요한 것만** 하면 됩니다. 셋 다 할 필요 없습니다.

| 나에게 맞는 것 | 하는 일 | 할 단계 |
|---|---|---|
| **채팅으로 만들고 싶다** | Claude Code에게 "고양이 사진 만들어줘"라고 말하면 바로 생성 | 1 → 2 |
| **눈으로 고르며 만들고 싶다** | 앱을 켜고 마우스로 모델 고르고 버튼 클릭 | 1 → 4 |
| **공짜로만 쓰고 싶다** | 내 컴퓨터에서 직접 이미지 생성 (인터넷·키 불필요) | 4 → 5 |
| **터미널에서 자동화하고 싶다** | 스크립트로 대량 생성 | 1 → 3 |

---

## 1단계. muapi API 키 발급받기

클라우드 모델을 쓰려면 키가 필요합니다. (로컬 모델만 쓸 거면 이 단계를 건너뛰고 **4단계**로 가세요.)

1. 👉 **[https://muapi.ai](https://muapi.ai)** 에 접속합니다.
2. 오른쪽 위 **Sign up** 버튼을 눌러 회원가입합니다. (구글 계정으로 가입하면 가장 빠릅니다)
3. 로그인되면 👉 **[https://muapi.ai/access-keys](https://muapi.ai/access-keys)** 로 이동합니다.
4. **Create key** (또는 **+ New key**) 버튼을 클릭합니다.
5. 이름은 아무거나 적습니다. 예: `claude-code`
6. **키 값이 화면에 딱 한 번 표시됩니다.** 옆의 **복사 아이콘(📋)** 을 눌러 복사하세요.

> ⚠️ **중요**
> - 복사할 것은 **키 값**입니다. 키 **이름**(`claude-code`)이 아닙니다.
> - 이 창을 닫으면 키 값을 다시 볼 수 없습니다. 메모장에 잠깐 붙여넣어 두세요.
> - 이 키는 **절대 GitHub에 올리거나 채팅에 붙여넣지 마세요.** 아래 2단계 방식대로만 저장합니다.

---

## 2단계. Claude Code에 연결하기 (MCP)

이 저장소에는 이미 연결 설정이 들어 있습니다(`.mcp.json`). **키를 환경변수로 넣어주는 작업만** 하면 됩니다.

**어디서 Claude Code를 쓰는지에 따라 방법이 완전히 다릅니다.** 해당하는 것 하나만 하세요.

---

### 🅰️ 웹(claude.ai/code)이나 모바일 앱에서 쓰는 경우

클라우드에서 실행되므로 **내 컴퓨터의 파일이나 환경변수는 전혀 읽지 않습니다.** 웹 화면에서 설정해야 합니다.

여기서는 **두 가지를 같이** 해야 합니다. 클라우드 환경은 기본값이 "허용된 사이트만 접속"이라
`api.muapi.ai` 가 **차단**되어 있기 때문입니다. 키만 넣으면 연결이 안 됩니다.

1. 👉 **[https://claude.ai/code](https://claude.ai/code)** 에 접속합니다.
2. 메시지 입력창 **바로 위쪽 줄**에 **☁️ 구름 아이콘**이 있고 그 옆에 환경 이름(**`Default`**)이 적혀 있습니다. 그걸 클릭합니다.
   *(설정 페이지나 직접 들어가는 주소는 없습니다. 반드시 이 구름 아이콘으로 들어가야 합니다.)*
3. 메뉴가 열리면 **`Default`** 위에 마우스를 올립니다. 오른쪽에 **⚙️ 톱니바퀴 아이콘**이 나타납니다. 그걸 클릭합니다.
4. **Update cloud environment** 창이 열립니다. 여기서 아래 두 가지를 합니다.

**① 네트워크 허용** — `Network access` 항목

- 선택지 중 **`Custom`** 을 고릅니다.
- 아래에 나타나는 **`Allowed domains`** 칸에 아래 한 줄을 붙여넣습니다.

```text
api.muapi.ai
```

- 바로 아래 **`Also include default list of common package managers`** 체크박스를 **반드시 체크**합니다.
  (체크를 안 하면 GitHub·npm 등 기존에 되던 것들이 끊깁니다.)

**② 키 입력** — `Environment variables` 항목

- 여기는 **이름 칸/값 칸이 따로 없습니다.** 큼직한 **네모난 입력 상자 하나**입니다.
- 그 안에 아래 형식으로 **한 줄** 붙여넣습니다. `여기에_키` 부분만 발급받은 키로 바꾸세요.

```text
MUAPI_API_KEY=여기에_키
```

> ⚠️ 자주 하는 실수
> - 따옴표 `" "` 붙이지 마세요. 그냥 `MUAPI_API_KEY=mu_abc123...` 이렇게만 씁니다.
> - `=` 앞뒤에 띄어쓰기를 넣지 마세요.
> - JSON 형식(`{ }`)이 아닙니다. `이름=값` 한 줄입니다.

5. 창 아래 **Save changes**(변경사항 저장) 버튼을 클릭합니다.
6. **⚠️ 가장 중요 — 지금 대화방에서는 적용되지 않습니다.**
   이미 돌아가고 있는 세션은 시작할 때 값을 한 번만 읽어갑니다.
   왼쪽 위 **새 세션(New session)** 을 눌러 **새 대화를 시작**해야 적용됩니다.

#### 더 안전한 방법 (Pro·Max 요금제 전용)

키를 환경변수에 넣으면 그 환경을 쓰는 사람은 값을 볼 수 있습니다. **API credentials** 기능을 쓰면
키가 세션 안으로 아예 들어오지 않고, 네트워크 허용도 자동으로 됩니다(위 ① 단계 불필요).

같은 **Update cloud environment** 창에서 `Environment variables` **아래쪽**에 있는
**API credentials** → **Add credential** 을 누르고 이렇게 채웁니다.

| 칸 | 넣을 값 |
|---|---|
| Credential type | `Bearer` (기본값 그대로) |
| Name | `muapi` |
| Allowed websites | `api.muapi.ai` |
| Custom headers → Name | `Authorization` (기본값 그대로) |
| Custom headers → Prefix | `Bearer` (기본값 그대로) |
| Custom headers → Value | 발급받은 키 |

**Connect** 를 누르면 저장됩니다. (저장 후에는 값을 다시 볼 수 없습니다.)
이 방법을 쓰실 거면 `.mcp.json`의 `Authorization` 헤더를 빼야 하므로 저에게 말씀해주세요.

---

### 🅱️ 내 컴퓨터(터미널 / 데스크톱 앱)에서 쓰는 경우

이때는 내 컴퓨터의 환경변수를 읽습니다. 네트워크 제한도 없습니다.

#### 맥(macOS)

1. **Spotlight**(키보드에서 `Cmd` + `스페이스바`)를 누르고 `터미널`이라고 입력한 뒤 `Enter`를 칩니다.
2. 아래 한 줄을 복사해서 터미널에 붙여넣습니다. `여기에_복사한_키_붙여넣기` 부분만 1단계에서 복사한 키로 바꾸세요.

```bash
echo 'export MUAPI_API_KEY="여기에_복사한_키_붙여넣기"' >> ~/.zshrc
```

3. `Enter`를 칩니다.
4. 아래를 복사해 붙여넣고 `Enter`를 칩니다. (설정을 지금 바로 적용하는 명령입니다)

```bash
source ~/.zshrc
```

5. 잘 들어갔는지 확인합니다. 아래를 붙여넣고 `Enter`:

```bash
echo ${MUAPI_API_KEY:0:8}...
```

키의 앞 8글자가 보이면 성공입니다. 아무것도 안 보이면 2번부터 다시 하세요.

#### 윈도우(Windows)

1. 키보드에서 **윈도우 키**를 누르고 `powershell`이라고 입력한 뒤 `Enter`를 칩니다.
2. 아래 한 줄을 복사해서 붙여넣습니다. `여기에_복사한_키_붙여넣기`만 바꾸세요.

```powershell
[Environment]::SetEnvironmentVariable("MUAPI_API_KEY", "여기에_복사한_키_붙여넣기", "User")
```

3. `Enter`를 칩니다.
4. **PowerShell 창을 완전히 닫았다가 다시 엽니다.** (안 닫으면 적용이 안 됩니다)
5. 확인합니다. 아래를 붙여넣고 `Enter`:

```powershell
$env:MUAPI_API_KEY.Substring(0,8)
```

키의 앞 8글자가 보이면 성공입니다.

#### 파일로 넣고 싶다면

터미널 명령 대신 파일로 해도 됩니다. 저장소 폴더 안에 아래 경로로 파일을 만드세요.

```
(저장소 폴더)/.claude/settings.local.json
```

내용은 이렇게 3줄입니다.

```json
{
  "env": {
    "MUAPI_API_KEY": "여기에_키를_붙여넣으세요"
  }
}
```

> 이 파일은 `.gitignore`에 등록돼 있어 **GitHub에 절대 올라가지 않습니다.**
> 단, 이 방법은 **🅱️ 내 컴퓨터에서 쓸 때만** 동작합니다. 웹 세션은 이 파일을 받지 않습니다.

---

### 확인하기

**Claude Code를 완전히 껐다가 다시 켭니다**(웹이면 새 세션 시작). 그다음 아래처럼 입력하세요.

```
/mcp
```

목록에 **`muapi`** 가 `connected`(연결됨)로 보이면 끝입니다.

이제 Claude Code에 이렇게 말하면 됩니다.

```
노을 지는 산속 호수 사진 하나 만들어줘
```

---

## 3단계. 터미널에서 쓰기 (CLI) — 선택 사항

스크립트로 자동화하거나 파일로 바로 저장하고 싶을 때만 하세요.

1. 터미널(또는 PowerShell)을 엽니다.
2. 아래를 붙여넣고 `Enter`:

```bash
npm install -g muapi-cli
```

3. 키를 등록합니다. `여기에_키` 부분만 바꾸세요.

```bash
muapi auth configure --api-key "여기에_키"
```

4. 잘 되는지 시험해 봅니다.

```bash
muapi image generate "a serene mountain lake at sunrise" --model flux-dev --download ./outputs
```

`./outputs` 폴더에 이미지가 저장됩니다.

자주 쓰는 명령:

```bash
# 영상 만들기
muapi video generate "a dog running on a beach" --model kling-v3.0-pro --duration 10

# 음악 만들기
muapi audio create "upbeat lo-fi hip hop for studying"

# 결과를 JSON으로 받기 (자동화용)
muapi image generate "a red sports car" --model flux-dev --output-json
```

---

## 4단계. 데스크톱 앱 설치 — 마우스로 쓰고 싶을 때

터미널을 쓰지 않고 창에서 클릭으로 작업하는 방법입니다.

1. 👉 **[https://github.com/Anil-matcha/Open-Generative-AI/releases](https://github.com/Anil-matcha/Open-Generative-AI/releases)** 에 접속합니다.
2. **맨 위에 있는 것이 최신 버전**입니다. 그 안의 **Assets** 를 클릭해 펼칩니다.
3. 내 컴퓨터에 맞는 파일을 클릭해 내려받습니다.

| 내 컴퓨터 | 받을 파일 |
|---|---|
| 맥 (M1/M2/M3/M4) | 이름에 **`arm64.dmg`** 가 들어간 파일 |
| 맥 (인텔) | 이름이 **`.dmg`** 로 끝나되 `arm64`가 **없는** 파일 |
| 윈도우 | 이름이 **`Setup ... .exe`** 인 파일 |
| 리눅스 (우분투) | **`.AppImage`** 또는 **`.deb`** |

> 내 맥이 M칩인지 인텔인지 모르겠다면: 화면 왼쪽 위 **🍎 애플 로고** → **이 Mac에 관하여** 를 클릭하세요.
> `Apple M1`/`M2`/`M3`/`M4` 라고 쓰여 있으면 arm64, `Intel`이라고 쓰여 있으면 인텔입니다.

### 맥에서 "열 수 없습니다" 경고가 뜰 때

애플 공증(notarize)을 받지 않은 앱이라 처음에 한 번 막힙니다. **한 번만** 아래를 하면 됩니다.

1. 받은 `.dmg` 파일을 더블클릭해 엽니다.
2. 나타난 앱 아이콘을 **Applications(응용 프로그램) 폴더로 드래그**합니다.
3. 터미널을 열고 아래를 붙여넣은 뒤 `Enter`:

```bash
xattr -cr "/Applications/Open Generative AI.app"
```

4. **응용 프로그램** 폴더에서 앱을 **마우스 오른쪽 버튼으로 클릭** → **열기** → 뜨는 창에서 다시 **열기** 를 클릭합니다.

터미널을 쓰고 싶지 않다면: 앱을 그냥 한 번 실행해서 차단당한 뒤,
**시스템 설정 → 개인정보 보호 및 보안** 으로 가서 아래로 스크롤하면
_"Open Generative AI이(가) 차단되었습니다"_ 옆에 **그래도 열기** 버튼이 있습니다. 그걸 누르세요.

### 윈도우에서 파란 경고창이 뜰 때

코드 서명이 없어서 SmartScreen이 경고합니다.

1. 경고창에서 **추가 정보** 를 클릭합니다.
2. 아래에 나타나는 **실행** 버튼을 클릭합니다.

### 앱을 처음 켰을 때

muapi API 키를 입력하라는 창이 뜹니다. 1단계에서 복사한 **키 값**을 붙여넣으세요.
(로컬 모델만 쓸 거면 **건너뛰기**를 눌러도 됩니다.)

---

## 5단계. 완전 무료로 쓰기 — 로컬 모델

**데스크톱 앱에서만** 됩니다. API 키도, 인터넷 연결도, 크레딧도 필요 없습니다.
모델 파일을 한 번 내려받으면 내 컴퓨터에서 직접 이미지를 만듭니다.

1. 앱 왼쪽 아래 **⚙️ Settings**(설정)를 클릭합니다.
2. **Local Models** 탭을 클릭합니다.
3. **sd.cpp inference engine** 옆의 **Install** 버튼을 클릭합니다. (자동으로 내려받습니다)
4. 아래 모델 목록에서 하나를 골라 **Download** 를 클릭합니다.

| 모델 | 용량 | 어떤 사람에게 |
|---|---|---|
| **Dreamshaper 8** | 2.1 GB | **처음이라면 이것부터.** 가장 가볍고 빠릅니다 |
| Realistic Vision v5.1 | 2.1 GB | 사진처럼 실사풍으로 |
| Anything v5 | 2.1 GB | 애니메이션·일러스트풍으로 |
| SDXL Base 1.0 | 6.9 GB | 고해상도가 필요할 때 |
| Z-Image Turbo | 2.5 GB + 보조 3.1 GB | 최신 모델. 메모리를 많이 씁니다 |

5. 다운로드가 끝나면 왼쪽의 **Image Studio** 로 돌아갑니다.
6. 모델 선택 칸 **바로 옆에 있는 ⚡ Local 토글**을 켭니다.
7. 방금 받은 모델을 고르고, 프롬프트를 적은 뒤 **Generate** 를 클릭합니다.

> 모델 파일은 시스템에 설치되지 않고 앱 데이터 폴더에만 저장됩니다.
> - 맥: `~/Library/Application Support/open-generative-ai/local-ai`
> - 윈도우: `%APPDATA%\open-generative-ai\local-ai`
> - 리눅스: `~/.config/open-generative-ai/local-ai`
>
> 용량이 부담되면 앱 실행 전에 `OPEN_GENERATIVE_AI_LOCAL_AI_DIR` 환경변수로 다른 드라이브를 지정할 수 있습니다.

영상까지 로컬로 만들려면 NVIDIA/AMD GPU가 달린 별도 서버(Wan2GP)가 필요합니다. GPU가 없다면 권하지 않습니다.

---

## 설치 없이 그냥 써보기

아무것도 설치하기 싫다면 브라우저에서 바로 쓸 수 있습니다.

👉 **[https://muapi.ai/open-generative-ai](https://muapi.ai/open-generative-ai)**

---

## 문제가 생겼을 때

| 증상 | 해결 |
|---|---|
| **환경변수 입력칸에 이름/값 두 칸이 없음** | 정상입니다. 한 개짜리 상자에 `MUAPI_API_KEY=키값` 형식으로 **한 줄** 적으면 됩니다 (`.env` 형식) |
| **웹에서 저장했는데 그대로임** | 실행 중인 세션은 시작할 때 값을 한 번만 읽습니다. **새 세션을 시작**해야 적용됩니다 |
| **웹에서 `muapi`가 연결 실패 / 접속 차단** | 클라우드 환경의 네트워크가 기본 차단입니다. `Network access`를 **Custom**으로 바꾸고 `api.muapi.ai`를 추가하세요 (2단계 🅰️) |
| `/mcp`에 `muapi`가 안 보임 | Claude Code를 완전히 종료 후 재실행. 그래도 없으면 저장소 루트에서 실행 중인지 확인 |
| `muapi`가 `failed`로 표시됨 | 2단계의 확인 명령으로 `MUAPI_API_KEY`가 실제로 들어갔는지 확인. 윈도우는 창을 닫았다 다시 열어야 적용됨 |
| `401 Unauthorized` | 키 **이름**을 넣었을 가능성이 큼. 1단계에서 **키 값**을 다시 복사 |
| 영상이 계속 `processing` | 정상입니다. 영상은 수십 초~수 분 걸립니다 |
| 크레딧 부족 오류 | [muapi.ai](https://muapi.ai) 대시보드에서 충전하거나, 5단계 로컬 모델로 전환 |
| 맥에서 앱이 안 열림 | 4단계의 `xattr -cr` 명령 다시 실행 |
| 소스 빌드 시 `Couldn't find a 'pages' directory` | 저장소 루트에서 실행 중인지 확인 후 `npm run setup` 재실행 |

---

## 개발자용 — 소스에서 직접 빌드

```bash
# 서브모듈까지 함께 받아야 합니다 (workflow·agent 패키지가 들어 있음)
git clone --recurse-submodules https://github.com/Anil-matcha/Open-Generative-AI.git
cd Open-Generative-AI

# 이미 서브모듈 없이 받았다면 한 번만 실행
# git submodule update --init --recursive

# 의존성 설치 + 워크스페이스 빌드 (npm install만으로는 부족합니다)
npm run setup

# 둘 중 하나로 실행
npm run electron:dev   # 데스크톱 앱
npm run dev            # 웹 버전 → http://localhost:3000
```

필요 조건: Node.js v18 이상.

---

## 보안 수칙

- API 키를 **저장소에 커밋하지 않습니다.** 이 저장소의 `.mcp.json`은 `${MUAPI_API_KEY}` 치환만 사용합니다.
- 키는 환경변수 또는 앱 설정창에만 저장합니다. 파일·채팅·스크린샷에 남기지 마세요.
- 키가 유출됐다고 판단되면 [muapi.ai/access-keys](https://muapi.ai/access-keys)에서 해당 키를 **Delete** 하고 새로 발급받으세요.
- 이 스택에는 콘텐츠 필터가 없습니다. 실존 인물의 얼굴을 동의 없이 합성하거나, 허위 사실을 사실처럼 보이게 만드는 생성물은 만들지 마세요.

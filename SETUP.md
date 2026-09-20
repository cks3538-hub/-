# 디자인 도구 세팅 기록

클로드 코드가 "전문가가 만든 것 같은" 웹사이트를 만들 수 있도록, 읽을 **디자인 지침서(스킬)** 와
직접 쥘 **연결 도구(MCP)** 를 이 저장소에 붙여 둔 기록입니다.

## 확인된 환경

| 항목 | 값 |
| --- | --- |
| Node.js | v22.22.2 (요구: v22.20.0 이상) |
| 클로드 코드 | 2.1.278 |
| 확인 위치 | Linux (클로드 코드 원격 컨테이너) |

## 현재 상태

| # | 이름 | 종류 | 상태 | 어디에 있나 |
| --- | --- | --- | --- | --- |
| 1 | `design-taste-frontend` | 스킬 | 설치됨 (이전 작업) | `.claude/skills/design-taste-frontend/` |
| 2 | `emil-design-eng` | 스킬 | 설치됨 | `.claude/skills/emil-design-eng/` |
| 3 | `impeccable` | 스킬 | 경순님 PC에 설치됨 (engine v0.1.5) · **저장소에는 미반영 — 아래 "남은 단계 1" 참고** | `C:\Users\장경순\.claude\skills\impeccable\` |
| 4 | `playwright` | MCP | 등록됨 · 연결 확인 완료 | `.mcp.json` |
| 5 | `figma` | MCP | 등록됨 · **로그인 승인 필요** | `.mcp.json` |

이 저장소에는 위 항목 외에 `agent-browser`, `image-to-code`, `playwright-cli`,
`ui-ux-pro-max`, `unlazy`, `web-design-guidelines`, `website-builder` 스킬과
`context7` MCP가 이미 들어 있습니다.

## 각각 무엇을 해 주나

- **design-taste-frontend** — 템플릿처럼 보이는 화면을 막아 주는 디자인 방향 판단 지침서.
- **emil-design-eng** — 버튼·여백·애니메이션 같은 "손맛" 디테일을 다듬는 지침서.
- **impeccable** — 디자인 슬롭(뻔한 결과물)을 편집 직후 자동으로 잡아 주는 검사기 + 슬래시 명령 모음.
- **playwright MCP** — 클로드가 브라우저를 직접 열어 만든 화면을 보고 고치게 해 주는 손.
- **figma MCP** — 피그마 디자인 파일을 클로드가 읽어 오게 해 주는 손.

## 실제로 실행한 명령

```bash
# 1) 환경 확인
node --version            # v22.22.2
ls .claude/skills
claude mcp list

# 2) 빠진 스킬 설치
npx --yes skills@latest add emilkowalski/skills \
  --skill "emil-design-eng" --agent claude-code --yes --copy

# 3) MCP 연결 (이 저장소를 클론하면 같이 따라오도록 project 스코프 사용)
claude mcp add --scope project playwright npx @playwright/mcp@latest
claude mcp add --scope project --transport http figma https://mcp.figma.com/mcp

# 4) 검증
ls .claude/skills
claude mcp list           # playwright: ... - ✔ Connected
```

> `design-taste-frontend` 는 이미 설치돼 있어 다시 설치하지 않았습니다.

## 남은 단계 1 — impeccable 을 프로젝트에 설치

### 지금 상태

경순님 윈도우 PC에서 아래 명령이 성공했습니다.

```
C:\Users\장경순> npx --yes impeccable@latest install --providers=claude --scope=project
Installed impeccable into: .claude (project)
Installed impeccable engine v0.1.5 (windows-x64)
Installed Claude Code agents into: C:\Users\장경순\.claude\agents
Installed hooks into: .claude
```

다만 홈 폴더(`C:\Users\장경순`)에서 실행해서, impeccable 이 그 폴더를 "프로젝트"로
잡았습니다(공식 규칙: *project root = 가장 가까운 `.git` 상위 폴더, 없으면 현재 폴더*).

결과적으로 설치 경로가 `C:\Users\장경순\.claude\` 인데, 이곳은 **클로드 코드의
사용자 전역 폴더**이기도 합니다. 그래서:

- ✅ **스킬과 에이전트는 전역으로 잡힙니다.** 어느 프로젝트에서 `claude` 를 열어도
  `/impeccable` 슬래시 명령을 쓸 수 있습니다. 다시 설치할 필요 없습니다.
- ⚠️ **자동 검사 훅만 다른 프로젝트에서 동작하지 않습니다.** 훅 경로가
  `${CLAUDE_PROJECT_DIR}/.claude/skills/impeccable/scripts/hook.mjs` 처럼
  "프로젝트 기준 상대 경로"로 적혀서, 다른 폴더에서는 파일을 못 찾고 조용히 넘어갑니다.

### 훅까지 살리고 저장소에도 반영하려면

이 저장소를 클론한 폴더 **안에서** 같은 명령을 한 번 더 실행하면 됩니다.

```powershell
cd <이 저장소를 클론한 폴더>
npx --yes impeccable@latest install --providers=claude --scope=project
```

그러면 그 폴더에 `.claude\skills\impeccable\` 과 `.claude\settings.local.json` 이
새로 생기고, 훅이 절대 경로가 아닌 그 프로젝트 기준으로 올바르게 잡힙니다.
생긴 `.claude/skills/impeccable/` 폴더를 커밋하면 이 저장소를 여는 어느 PC에서나
따라옵니다.

> 훅이 번거로우면 클로드 코드 채팅창에서 `/impeccable hooks off` 로 언제든 끌 수 있습니다.

### 그다음 — `/impeccable init`

터미널이 아니라 **클로드 코드 채팅창**에서 실행합니다. 프로젝트 폴더에서 `claude` 를
켠 뒤 `/impeccable init` 을 입력하면, 이 프로젝트의 대상 고객·목적·톤 같은 맥락을
물어보고 `PRODUCT.md` 로 남깁니다. 이후 모든 impeccable 명령이 이 문서를 참고합니다.

### 원격 컨테이너에서는 왜 안 됐나

클로드 코드 웹/원격 세션에서는 이 명령이 실패합니다. impeccable 이 스킬 묶음을
`impeccable.style` 에서 내려받는데, 원격 세션의 네트워크 정책이 해당 호스트 접속을
차단합니다(`CONNECT tunnel failed, response 403`). 정책 차단이라 우회하지 않았습니다.
설치는 경순님 PC에서만 하시면 됩니다.

## 남은 단계 2 — figma 로그인 승인

`claude mcp list` 에서 figma 가 `Needs authentication` 으로 나오는 것은 정상입니다.
본인 PC에서 `claude` 를 실행하고 `/mcp` 를 입력한 뒤 figma 를 골라 브라우저 로그인을
한 번 승인하면 `✔ Connected` 로 바뀝니다.

## 프로젝트 MCP 승인에 대하여

`.mcp.json` 에 적힌 MCP 는 보안상 한 번 승인해야 켜집니다. 본인 PC에서 이 저장소를 열고
`claude` 를 실행하면 "이 프로젝트의 MCP 서버를 사용할까요?" 라고 물어보는데, 거기서
승인하면 됩니다.

## 비용·라이선스

- 스킬 3종과 Playwright MCP 는 무료입니다.
- Figma MCP 는 베타 기간 무료이며, 공식 문서상 이후 사용량 기반 유료로 전환될 수 있습니다.

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
| 3 | `impeccable` | 스킬 | **미설치 — 아래 "남은 단계 1" 참고** | — |
| 4 | `playwright` | MCP | 등록됨 · 연결 확인 완료 | `.mcp.json` |
| 5 | `figma` | MCP | 등록됨 · **로그인 승인 필요** | `.mcp.json` |

이 저장소에는 위 항목 외에 `agent-browser`, `image-to-code`, `playwright-cli`,
`ui-ux-pro-max`, `unlazy`, `web-design-guidelines`, `website-builder` 스킬과
`context7` MCP가 이미 들어 있습니다.

## 각각 무엇을 해 주나

- **design-taste-frontend** — 템플릿처럼 보이는 화면을 막아 주는 디자인 방향 판단 지침서.
- **emil-design-eng** — 버튼·여백·애니메이션 같은 "손맛" 디테일을 다듬는 지침서.
- **impeccable** — 컴파일된 디자인 스킬 묶음. (아직 미설치)
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

## 남은 단계 1 — impeccable 설치

설치 명령 자체는 아래 한 줄입니다.

```bash
npx --yes impeccable@latest install --providers=claude --scope=project
```

원격 컨테이너에서는 이 명령이 실패합니다. impeccable 은 스킬 묶음을
`impeccable.style` 에서 내려받는데, 이 세션의 네트워크 정책이 해당 호스트로의 접속을
차단합니다(`CONNECT tunnel failed, response 403`). 정책 차단이라 우회하지 않았습니다.

**본인 PC(맥/윈도우) 터미널에서 이 저장소를 클론한 뒤 위 명령을 실행하면 정상 설치됩니다.**
설치 후 생기는 `.claude/skills/impeccable/` 폴더를 커밋해 주세요.

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

---
name: website-builder
description: "무료 웹사이트 제작 스택(UI/UX Pro Max + Framer Motion) 사용 절차. 랜딩페이지·회사 소개·포트폴리오·이벤트 페이지를 '만들어줘', '웹사이트', '랜딩페이지', '홈페이지', 'build a landing page', '애니메이션 넣어줘', '스크롤할 때 움직이게', '모바일에서도 예쁘게' 요청 시 사용. 디자인 시스템은 ui-ux-pro-max 검색 스크립트로 뽑고, 애니메이션은 framer-motion으로 구현한다. 21st.dev(부분 유료)는 사용하지 않는다."
---

# Website Builder (무료 스택)

이 저장소의 웹사이트 제작 기본 스택이다. SNS에 소개된 "UI/UX Pro Max + Framer Motion + 21st.dev" 조합 중 무료인 두 가지만 사용한다.

| 도구 | 역할 | 비용 |
|---|---|---|
| ui-ux-pro-max (`.claude/skills/ui-ux-pro-max/`) | 스타일·팔레트·폰트·UX 규칙·모션 프리셋 검색, 디자인 시스템 생성 | 무료 (MIT) |
| framer-motion (npm) | 스크롤 리빌, 호버, 페이지 전환 애니메이션 | 무료 (MIT) |
| 21st.dev MCP | 완성형 컴포넌트 | **사용 안 함** (AI 생성과 일부 컴포넌트가 유료) |

## 0. 요청 읽기

- 무엇을 하는 사업/서비스인지, 대상 고객, 원하는 분위기(다크·미니멀·볼드 등), 참고 사이트를 먼저 파악한다.
- 부족한 정보는 합리적으로 가정하고 바로 진행한다. 가정한 내용은 결과 끝에 한 줄로 밝힌다. 방향이 완전히 갈릴 때만 질문한다.
- 랜딩·포트폴리오 같은 마케팅 페이지면 `design-taste-frontend` 스킬의 "Design Read" 절차도 함께 따른다.

## 1. 디자인 시스템 뽑기 (ui-ux-pro-max)

```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py "<서비스 종류> <업종> <분위기 키워드>" --design-system -p "<프로젝트명>" --format markdown
```

- 결과의 Pattern / Style / Colors / Typography / Key Effects / Anti-patterns / Checklist를 구현 기준으로 삼는다.
- 세부 규칙 검색: `--domain ux|typography|color|landing|icons`. 스택별 규칙: `--stack nextjs|react|html-tailwind|shadcn`.
- 모션 수치(강도·지속시간·easing): `--domain gsap "<scroll reveal|hover|page transition>"`. GSAP 스니펫으로 나오지만 duration, easing, y offset 수치를 framer-motion에 그대로 옮긴다.
- 프로젝트 안에 기준 문서를 남길 때: `--persist --output-dir <프로젝트 루트>` 를 붙이면 `design-system/<slug>/MASTER.md`가 생성된다.
- Windows에서는 `python3` 대신 `python`을 쓴다.

## 2. 프로젝트 준비

새 프로젝트:

```bash
npx create-next-app@latest <폴더명>
# 질문에 TypeScript: Yes / Tailwind: Yes / App Router: Yes 로 답한다
cd <폴더명>
npm install framer-motion lucide-react
```

- 기존 프로젝트면 `npm install framer-motion`만 실행한다. React 18과 19를 지원한다.
- `motion` 패키지(`import { motion } from "motion/react"`)는 같은 코드베이스다. 이미 설치되어 있으면 그대로 쓴다.
- 아이콘은 `lucide-react`(무료 SVG). 이모지를 아이콘으로 쓰지 않는다.

## 3. 애니메이션 구현 규칙 (framer-motion)

Next.js App Router에서는 애니메이션 컴포넌트 파일 맨 위에 `"use client"`를 붙인다.

스크롤 리빌 기본값: y 12~24px, 0.35~0.5초, easeOut, 한 번만 재생.

```tsx
"use client";
import { motion, useReducedMotion } from "framer-motion";

export function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.45, ease: "easeOut", delay }}
    >
      {children}
    </motion.div>
  );
}
```

- 호버·탭: `whileHover={{ y: -2, scale: 1.02 }} whileTap={{ scale: 0.98 }}`, transition 150~300ms.
- 스태거: 부모 `variants`에 `staggerChildren: 0.08`, 자식은 8개 이하.
- 페이지·모달 전환: `<AnimatePresence mode="wait">` 안에서 `key`를 바꿔 exit 애니메이션을 재생한다.

지켜야 할 것:

- `useReducedMotion()`이 true면 이동 애니메이션을 끄고 최종 상태를 바로 그린다.
- `opacity`와 `transform`(x, y, scale)만 애니메이션한다. width, height, top, left는 레이아웃을 흔든다.
- 히어로의 핵심 문구와 CTA 버튼은 첫 렌더에서 보이게 한다. 애니메이션은 장식이지 콘텐츠를 숨기는 수단이 아니다.
- 모션 강도는 1단계에서 뽑은 디자인 시스템의 Key Effects 범위를 넘지 않는다.

## 4. 반응형·마감 체크 (배포 전 필수)

- 375 / 768 / 1024 / 1440px에서 가로 스크롤이 없어야 한다. 터치 타깃은 44px 이상.
- 텍스트 대비 4.5:1 이상, 키보드 포커스 표시, 클릭 요소에 `cursor-pointer`.
- `npm run build`가 통과해야 한다. 가능하면 `web-design-guidelines` 스킬로 한 번 점검한다.
- 결과 보고는 순서대로: 실행 명령, 접속 주소, 수정하려면 어느 파일을 열지.

## 5. 유지보수

- UI/UX Pro Max 데이터 업데이트: 저장소 루트에서 `npx -y ui-ux-pro-max-cli@latest update --ai claude`.
- ui-ux-pro-max는 전용 CLI로 설치되어 `skills-lock.json`에 등록되지 않는다.
- 재설치(`npx -y ui-ux-pro-max-cli@latest init --ai claude --force`)하면 부속 스킬 6개(banner-design, brand, design, design-system, slides, ui-styling)가 함께 생긴다. 이 저장소는 핵심 스킬만 쓰므로 그 6개 폴더는 다시 삭제한다.

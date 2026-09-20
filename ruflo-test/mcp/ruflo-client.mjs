// 공용 헬퍼: 로컬에 설치된 ruflo MCP 서버(stdio)에 접속한 Client 를 돌려줍니다.
// npx 대신 node + node_modules/ruflo/bin/ruflo.js 를 직접 실행하므로 Windows/macOS/Linux 모두 동일하게 동작합니다.
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

export const RUFLO_BIN = fileURLToPath(new URL('../node_modules/ruflo/bin/ruflo.js', import.meta.url));

export async function connectRuflo(name = 'ruflo-test-client') {
  if (!existsSync(RUFLO_BIN)) {
    throw new Error(`ruflo 가 설치되어 있지 않습니다. ruflo-test 폴더에서 먼저 "npm install" 을 실행하세요. (찾은 경로: ${RUFLO_BIN})`);
  }
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [RUFLO_BIN, 'mcp', 'start'],
    env: { ...process.env, NO_COLOR: '1', CLAUDE_FLOW_MODE: 'v3', npm_config_update_notifier: 'false' },
    stderr: 'pipe',
  });
  const client = new Client({ name, version: '1.0.0' });
  await client.connect(transport);
  return client;
}

/** 도구 응답(content[].text)을 JSON 이면 객체로, 아니면 문자열로 */
export function parseResult(res) {
  const txt = (res.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n');
  try { return JSON.parse(txt); } catch { return txt; }
}

// results/claude-agent-run.jsonl 에서 최종 답변과 메타데이터를 추출합니다.
import { readFileSync, writeFileSync } from 'node:fs';
const lines = readFileSync('results/claude-agent-run.jsonl', 'utf8').trim().split('\n');
let final = '';
for (const l of lines) {
  try { const j = JSON.parse(l); if (j.type === 'result') { final = j.result || ''; writeFileSync('results/claude-agent-result.json', JSON.stringify(j, null, 2)); } } catch {}
}
writeFileSync('results/claude-agent-final.txt', final);
console.log(final || '(최종 답변 없음 — results/claude-agent-run.jsonl 과 results/claude-agent-stderr.txt 를 확인하세요)');

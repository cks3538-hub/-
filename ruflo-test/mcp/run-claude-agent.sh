#!/usr/bin/env bash
# Claude Code CLI를 헤드리스로 띄워 ruflo MCP 도구로 실제 작업을 수행하게 합니다.
# 출력: results/claude-agent-run.jsonl (전체 이벤트 스트림), results/claude-agent-final.txt (최종 답변)
set -u
cd "$(dirname "$0")/.."
mkdir -p results
claude -p "$(cat mcp/agent-task.md)" \
  --output-format stream-json --verbose \
  --mcp-config mcp/claude-mcp.json --strict-mcp-config \
  --allowedTools "mcp__claude-flow__*,Read,Edit,Write,Glob,Grep,Bash(node:*),Bash(npx ruflo:*),Bash(cat:*),Bash(ls:*)" \
  --max-turns 60 \
  --model sonnet \
  < /dev/null > results/claude-agent-run.jsonl 2> results/claude-agent-stderr.txt
echo "exit=$?" > results/claude-agent-exit.txt
node -e '
const fs=require("fs");const lines=fs.readFileSync("results/claude-agent-run.jsonl","utf8").trim().split("\n");
let final="";for(const l of lines){try{const j=JSON.parse(l);if(j.type==="result"){final=j.result||"";fs.writeFileSync("results/claude-agent-result.json",JSON.stringify(j,null,2));}}catch{}}
fs.writeFileSync("results/claude-agent-final.txt",final);
'

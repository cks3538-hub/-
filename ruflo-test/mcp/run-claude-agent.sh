#!/usr/bin/env bash
# Claude Code CLI를 헤드리스로 띄워 ruflo MCP 도구로 실제 작업을 수행하게 합니다. (macOS / Linux / Git Bash)
# 출력: results/claude-agent-run.jsonl (전체 이벤트 스트림), results/claude-agent-final.txt (최종 답변)
set -u
cd "$(dirname "$0")/.."
mkdir -p results
claude -p \
  --output-format stream-json --verbose \
  --mcp-config mcp/claude-mcp.json --strict-mcp-config \
  --allowedTools "mcp__claude-flow__*,Read,Edit,Write,Glob,Grep,Bash(node:*),Bash(npx ruflo:*),Bash(cat:*),Bash(ls:*)" \
  --max-turns 60 \
  --model sonnet \
  < mcp/agent-task.md > results/claude-agent-run.jsonl 2> results/claude-agent-stderr.txt
echo "exit=$?" > results/claude-agent-exit.txt
node mcp/extract-final.mjs

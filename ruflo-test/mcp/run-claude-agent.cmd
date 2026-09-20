@echo off
REM Claude Code CLI를 헤드리스로 띄워 ruflo MCP 도구로 실제 작업을 수행하게 합니다. (Windows cmd / PowerShell)
REM 출력: results\claude-agent-run.jsonl (전체 이벤트 스트림), results\claude-agent-final.txt (최종 답변)
cd /d "%~dp0.."
if not exist results mkdir results
echo Claude 에이전트 실행 중... (1~3분, 비용 약 $0.5)
call claude -p --output-format stream-json --verbose --mcp-config mcp\claude-mcp.json --strict-mcp-config --allowedTools "mcp__claude-flow__*,Read,Edit,Write,Glob,Grep,Bash(node:*),Bash(npx ruflo:*),Bash(cat:*),Bash(ls:*)" --max-turns 60 --model sonnet < mcp\agent-task.md > results\claude-agent-run.jsonl 2> results\claude-agent-stderr.txt
echo exit=%ERRORLEVEL%> results\claude-agent-exit.txt
node mcp\extract-final.mjs

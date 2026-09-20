You are working in the ruflo-test project. You have MCP tools from the server "claude-flow" (ruflo). Complete this job end to end, using the claude-flow MCP tools for coordination and the normal file/shell tools for the code work:

1. Call mcp__claude-flow__swarm_init with topology "hierarchical", maxAgents 3, strategy "specialized".
2. Call mcp__claude-flow__agent_spawn twice: agentType "coder" and agentType "tester" (model "haiku"). Keep the returned agent IDs.
3. Call mcp__claude-flow__task_create with type "feature", priority "high", description "Add trading-fee support to sample/src/price-calc.js", tags ["crypto","fee"], and assign it to the coder agent. Keep the task ID.
4. Implement the feature in sample/src/price-calc.js (ES module):
   - Add and export `netPnL(trades, currentPrice, feeRate = 0)`: like unrealizedPnL, but subtract fees. Fees = feeRate × (sum of qty×price of every trade, buys and sells) . A feeRate of 0.0005 means 0.05%.
   - Add and export `breakEvenPrice(trades, feeRate = 0)`: the price at which netPnL becomes 0 for the currently held quantity. Return 0 when held quantity is 0.
   - Do not change existing exported functions' behavior.
5. Add tests for both functions to sample/test/price-calc.test.js (node:test + node:assert/strict), including feeRate 0 (equals unrealizedPnL) and feeRate 0.0005.
6. Run `node --test sample/test/*.test.js` and make sure every test passes. Fix until green.
7. Call mcp__claude-flow__task_complete (or task_update with status "completed" if task_complete fails) for the task ID, with a short result summary.
8. Call mcp__claude-flow__memory_store with namespace "crypto", key "feature/fee-support", value = one-paragraph summary of what you implemented and the test result.
9. Call mcp__claude-flow__task_list and mcp__claude-flow__swarm_status and include their key facts in your final answer.

Final answer (plain text, Korean): what MCP tools you called and their results (IDs), what code you changed, and the test summary line (pass/fail counts). Do not create git commits.

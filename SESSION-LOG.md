# Elsa Runtime — Session Log

_Reverse chronological. Each session = one Claude Code conversation._

---

## Session 3 — 2026-03-06

**Tool:** Claude Code (Opus 4.6)

### Completed
- **RULES-CORE/LOCAL 分層機制**：
  - `templates/RULES.md` → `templates/RULES-CORE.md` (v3.5)，合併 repo v3.4 + live v3.3 內容
  - Elsa workspace 用 symlink 連回 repo，確保更新自動同步
  - 建立空白 `RULES-LOCAL.md` 供 agent 自訂補充規則
  - SOUL.md 更新規則系統參照
  - RUNBOOK 加入 symlink 部署步驟（適用所有未來 agent）
- **OAuth Token 過期事件排除**：
  - 症狀：`OAuth token refresh failed for openai-codex`，Elsa 完全無回應
  - 踩坑：選錯 provider（Copilot Proxy）、選錯帳號（Free plan）、不知道 openai-codex 是 built-in 要用 paste-token
  - 最終修法：正確帳號 token → `paste-token` → `config set` → `gateway restart`
- **RUNBOOK 加入 OAuth Token 更新 SOP**（完整 7 步驟 + 常見踩坑表）
- **OpenClaw 升級** 2026.2.24 → 2026.3.2
- **瀏覽器能力文件化**：RUNBOOK 加入「階段 4B: 瀏覽器能力」段落
- **資源管理規則**加入 RULES-CORE（tab 管理、RAM 限制）
- **GPT-5.4** released（2026-03-05），評估後決定暫不升級（GPT-5.2 到 June 5 仍可用）

### Discovered
- OpenAI OAuth token 約 10 天過期，需手動 `paste-token` 更新
- `openclaw models auth login` 的 provider 選項只有 plugin providers，openai-codex 是 built-in，要用 `paste-token`
- `openclaw agents config main --model` 不存在，要用 `openclaw config set agents.defaults.model.primary`
- copilot-proxy 需要 VS Code 在 localhost:3000 跑，standalone 環境不適用
- ChatGPT Pro 帳號（elsalab.nthu）vs 個人帳號（cymaxwelllee）token 不同，Free plan token 無法使用 GPT-5.2

### Architecture Decisions
- RULES 分兩層：CORE（repo symlink，唯讀）+ LOCAL（agent 自訂）
- OAuth token 管理：手動 SOP 先行，自動監控排入 BACKLOG（Phase 2 交給 Mayu）

---

## Session 2 — 2026-03-04 (continued)

**Tool:** Claude Code (Opus 4.6)

### Completed
- Installed **Playwright** 1.58.0 (python3.11) + Chromium driver 145.0.7632.6 (~255MB)
- Discovered **OpenClaw has built-in browser** (`openclaw browser` command, Playwright-based, uses Google Chrome)
- example.com test: PASS (both OpenClaw browser + Playwright Python)
- arXiv test: PASS — "Memory in the Age of AI Agents"
- Created `meta/IMPLEMENTATION-STATUS.md` in elsa-system for Claude Chat Project sync
- Created tracking: PROGRESS.md, SESSION-LOG.md, meta/BACKLOG.md

### Browser Setup Results

| Item | Result |
|------|--------|
| Chromium brew install | Skipped — Google Chrome already present |
| Google Chrome | Detected by OpenClaw at `/Applications/Google Chrome.app` |
| Playwright install | OK — 1.58.0 |
| Playwright Chromium driver | OK — 145.0.7632.6 |
| Plan chosen | OpenClaw built-in browser (no extra MCP needed) |
| example.com test | PASS |
| arXiv test | PASS |
| Elsa Telegram web reading | Not tested (needs owner trigger) |

### Discovered
- OpenClaw `browser` command is full Playwright wrapper (snapshot, click, navigate, screenshot, etc.)
- No need for Playwright MCP server or web-fetch skill — OpenClaw handles it natively
- Google Chrome detected automatically, no Chromium install needed

---

## Session 1 — 2026-03-04

**Duration:** ~2 hours
**Tool:** Claude Code (Opus 4.6)

### Completed
- Connected `elsa-runtime` local repo to GitHub remote (`CYMaxwellLee/elsa-runtime`)
- Deployed **T1.0.1 ExecutionLogger** (`data/execution_log.py`) — smoke test passed
- Copied `execution_log.py` to `~/.elsa-system/scripts/` for runtime use
- Placed `FRESH-MACHINE-RUNBOOK.md` in `elsa-system/ops/`
- Installed **OpenClaw CLI** globally (2026.2.24) — `npm install -g openclaw@latest`
- Tested **multi-agent**: `openclaw agents add rei` — success, shared gateway, isolated workspaces
- Created **hello-world custom skill** — discovered skills must be flat `skills/{slug}/` not nested
- Ran **ChromaDB smoke test** — PersistentClient + query passed (first run downloads ONNX model ~79MB)
- **Disk evaluation**: 46GB available (not 16GB as previously recorded), Phase 1-2 OK
- **Architecture review**: repo separation is clean (design vs code vs infra), no code in wrong place
- Updated `RECON-NOTES.md` with all test results
- Created tracking files: `PROGRESS.md`, `SESSION-LOG.md`, `BACKLOG.md`

### Discovered
- OpenClaw `agents` (plural) command, not `agent` (singular)
- Custom skills directory: must be flat `~/.openclaw/workspace/skills/{slug}/`
- Disk was 46GB free, not 16GB — previous measurement used different method
- `scripts/template-renderer/` (350+ LOC) in elsa-system should move to elsa-runtime

### Still Pending
- API key separation (#3) — manual, needs console.anthropic.com
- `template-renderer/` migration to elsa-runtime (low priority)

---

## Session 0 — 2026-02-25

**Duration:** ~1 hour
**Tool:** Claude Code

### Completed
- Cloned `Elsa-System` repo from GitHub
- Installed `gh` CLI + authenticated (`CYMaxwellLee`)
- Installed ChromaDB + sentence-transformers via `python3.11 -m pip`
- Created `~/.elsa-system/` directory structure (chromadb-data, shared/.env, logs, config.yaml)
- Filled RECON-NOTES.md gap analysis (Design Doc vs Reality table)
- Git commit + push

---

_Update this file at the end of every session._

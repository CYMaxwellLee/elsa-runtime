# Elsa Runtime — Session Log

_Reverse chronological. Each session = one Claude Code conversation._

---

## Session 3+4 — 2026-03-06

**Tool:** Claude Code (Opus 4.6)

### Completed (Session 3)
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
- **Gmail Reader Tool** 完成部署：
  - Python CLI（`tools/gmail/`）：list / unread / search / read / labels
  - Google Cloud OAuth 2.0（gmail.readonly scope）
  - OpenClaw skill `gmail-reader` 部署且認到
  - setup.sh 一鍵安裝（pip + skill + auth）
  - 驗證 PASS：165,452 封信，unread/search/labels 全正常
  - 踩坑：Google 測試使用者加不進去 → 改正式版解決
  - RUNBOOK Stage 4C 完整文件化（含常見問題表）

### Completed (Session 4 — 延續 Session 3)
- **Elsa 整合 Gmail skill**：
  - SOUL.md 加入 Gmail 能力提示（精簡版，含 CLI 指令範例）
  - TOOLS.md 更新環境細節（帳號、路徑）
  - 多次 gateway restart 調整後 Elsa 成功讀取未讀信件（Telegram 驗證 PASS）
- **RULES-CORE.md 安全強化**：
  - 新增「禁止 agent 自行修改的檔案」規則：SOUL.md / RULES-CORE.md / IDENTITY.md
  - RULES-LOCAL.md 修改需主人確認
- **SOUL.md 架構設計**：
  - 原則確立：「SOUL 管你是誰，SKILL 管你會什麼」
  - SOUL.md 只放一行能力摘要 + 關鍵指令，詳細用法留在 SKILL.md
  - 可擴展：未來加工具只多一行，不會膨脹
- **Claude CLI 設定**：
  - `/usr/local/bin/claude` symlink 建立（指向 Claude.app 2.1.51 binary）
  - `claude login` + `claude remote-control` 設定指引

### Discovered (Session 3)
- OpenAI OAuth token 約 10 天過期，需手動 `paste-token` 更新
- `openclaw models auth login` 的 provider 選項只有 plugin providers，openai-codex 是 built-in，要用 `paste-token`
- `openclaw agents config main --model` 不存在，要用 `openclaw config set agents.defaults.model.primary`
- copilot-proxy 需要 VS Code 在 localhost:3000 跑，standalone 環境不適用
- ChatGPT Pro 帳號（elsalab.nthu）vs 個人帳號（cymaxwelllee）token 不同，Free plan token 無法使用 GPT-5.2
- Google Cloud OAuth consent screen：測試使用者常加不進去（「無法新增不符合資格的帳戶」），直接改正式版更快
- Google OAuth refresh token 長期有效（6 個月未使用才過期），不像 OpenAI OAuth 每 10 天要手動更新
- GPT-5.4 在 OpenClaw openai-codex provider 尚未支援（registry 最高 5.3-codex），ChatGPT Pro OAuth 回 401

### Discovered (Session 4)
- **OpenClaw `nativeSkills: auto` 不夠用**：SKILL.md 不會自動注入到 agent 的 context，agent 只看到 SOUL.md。SOUL.md 必須有足夠提示（至少一行指令範例），agent 才知道怎麼用工具
- **SOUL.md 寫太含糊會讓 agent 亂猜**：只寫 "gmail-reader skill" 不夠，GPT-5.2 會以為要用瀏覽器。必須寫「用 shell 執行 CLI，不是瀏覽器」+ 給一行實際指令
- **gateway restart 時收到的訊息會超時**：重啟期間的 Telegram 訊息會觸發 typing indicator，但 LLM 來不及回應，2 分鐘後超時（typing TTL reached）。重啟後需要重新傳訊息
- **Claude CLI 不在 PATH**：Claude.app 安裝的 binary 在 `~/Library/Application Support/Claude/claude-code/*/claude`，需手動 symlink 到 `/usr/local/bin/claude`。Mac 可能沒有 `/usr/local/bin/` 目錄，需先 `mkdir -p`
- **`openclaw restart` 不存在**：正確指令是 `openclaw gateway restart`

### Architecture Decisions
- RULES 分兩層：CORE（repo symlink，唯讀）+ LOCAL（agent 自訂）
- OAuth token 管理：手動 SOP 先行，自動監控排入 BACKLOG（Phase 2 交給 Mayu）
- Gmail 整合用 Python CLI tool（非 MCP server）：16GB RAM 不跑背景程序，每次呼叫 1-2 秒即結束
- **SOUL vs SKILL 分離**：SOUL.md 放人格+核心規則+能力一覽（每工具一行），SKILL.md 放完整工具用法。SOUL 不膨脹，SKILL 按需載入
- **核心檔案保護**：SOUL.md / RULES-CORE.md / IDENTITY.md 禁止 agent 自行修改

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

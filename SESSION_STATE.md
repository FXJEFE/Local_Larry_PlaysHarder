# LARRY G-FORCE — Session State Snapshot

**Date:** 2026-06-10
**Canonical root:** `C:\Users\LocalLarry\Documents\LocalLarry\GITHUB\src`
**Hardware:** RTX 4060 Ti 8 GB VRAM + 64 GB DDR5 (GPU+CPU hybrid offload)

---

## What exists now (all built + verified this session)

### Core layout
```
src/
├── main.py            entry: status | run <agent> "<task>" [--model] | auto "<goal>" |
│                      plan "<goal>" | chat (streamed) | serve | telegram | all
├── config.py          loads config.json; OLLAMA_HOST env override; model role resolution + fallbacks
├── config.json        canonical config (paths, model roles, subagents, orchestrator, model_management)
├── orchestrator.py    single run() entry; classify→route→free VRAM→recall memory→execute→remember;
│                      LLM planning (plan/run_goal); records to tasks.db
├── server.py          stdlib HTTP API on :8080 (no extra deps)
├── subagents/
│   ├── base.py        native Ollama tool-call loop + parse_text_tool_calls() fallback + PLATFORM_NOTE
│   ├── executor.py    terminal (run_terminal)            [model: main]
│   ├── editor.py      read/write/edit files              [model: main]
│   ├── searcher.py    memory/file/web search             [model: fast]
│   ├── transcribe.py  faster-whisper (GPU→CPU fallback)  [model: fast]
│   └── debugger.py    reproduce→fix→verify               [model: main, 10 turns]
├── tools/terminal.py  run_terminal + RUN_TERMINAL_TOOL definition
├── utils/
│   ├── memory_manager.py    sessions.db + skills.db + chroma remember/recall
│   ├── task_manager.py      tasks.db (pending→in_progress→completed/failed)
│   ├── intelligent_router.py  force_model → subagent role → complexity → hardware degrade
│   ├── task_complexity.py     keyword+length scoring
│   ├── hardware_monitor.py    nvidia-smi VRAM/util
│   └── model_manager.py       priority unloading (keep_loaded survive; keep_alive:0 evicts)
├── memory/            chroma_db/ + tasks.db + skills.db + sessions.db  (named volumes in Docker)
├── Dockerfile, docker-compose.yml, .dockerignore, requirements-docker.txt
```

### Ollama models (built, in `GITHUB\ollama_modelfiles\Modelfile.LocalLarry-*`)
| Model | Base | Tuning | Role |
|---|---|---|---|
| LocalLarry-15b | qwen3-coder:30b-a3b-q4_K_M (MoE ~3B active) | ctx 32k, num_gpu 18, temp 0.3 | main |
| LocalLarry-Fast | qwen3:8b | ctx 16k, full GPU offload | fast |
| LocalLarry-Uncensored | dolphin3:8b | ctx 16k, full GPU offload | uncensored |
| nomic-embed-text | — | — | embedding |

---

## Test results (all PASS)
| Subagent / feature | Result |
|---|---|
| executor | port 11434 → ollama.exe PID 4836 (native tool calls) |
| editor | wrote editor_smoke.txt, verified on disk |
| debugger | needed text-tool-call fallback; then fixed broken_calc.py → `Total: 15` |
| searcher | accurate chroma_db search on LocalLarry-Fast |
| transcribe | word-perfect transcript (after faster-whisper install + CPU int8 fallback) |
| orchestrator (host) | 3-step plan executed 3/3 |
| orchestrator (Docker) | 2-step plan executed 2/2, Linux commands |
| Docker image | builds clean; standalone OFFLINE ok; host.docker.internal → 50 models |
| API (Docker) | /health /agents /run /memory /remember /tasks all working |
| memory loop | executor result auto-remembered, surfaced in later /memory query |

---

## Persisted state
- `tasks.db`: 4 completed task rows (smoke + orchestrator steps)
- `skills.db`: 1 skill; `sessions.db`: 1 session
- `chroma_db`: 34.6 MB (moved intact from src/chroma_db → src/memory/chroma_db)

---

## How to run
```powershell
cd C:\Users\LocalLarry\Documents\LocalLarry\GITHUB\src
python main.py status
python main.py run executor "check what's on port 11434"
python main.py auto "find which script writes larry.log and fix its level"
python main.py chat
python main.py serve              # HTTP API on :8080

# Docker (native Ollama already on 11434 — see warning)
docker compose build
docker compose run --rm --no-deps --env OLLAMA_HOST=http://host.docker.internal:11434 agent python main.py status
docker compose --profile api up -d api
```

⚠️ **Port 11434:** native Windows Ollama owns it. Either stop it before `docker compose up ollama`, drop the `ports:` block, or just point containers at `host.docker.internal:11434`.

---

## Token streaming (DONE — added + verified)
- `base.py`: `_ollama_chat_stream()` + `chat_with_tools_stream()` emit events
  (routing/token/tool/tool_result/final). Tool-call turns are suppressed (native
  empty-content turns and qwen3-coder `<function=...>` text turns both detected by
  lead-peek), so only the FINAL answer streams token-by-token.
- `orchestrator.run_stream()`: same routing/VRAM/memory/recording as run(), streamed.
- `server.py`: `POST /run/stream` → Server-Sent Events (`data: {json}\n\n`).
- `main.py chat`: prints routing header + `> tool(...) [ok]` + live tokens;
  stdout reconfigured to utf-8/replace (Windows cp1252 would crash on model Unicode).
- Verified: local probe 4 token events; REPL tool task (suppressed turn + streamed
  summary); Docker SSE plain (token deltas) and with tool (tool/tool_result + stream).

## API auth + port (DONE — added + verified)
- HTTP API now on **port 7333** (was 8080): `server.py` default, `config.json api.port`,
  `main.py serve`, compose `api` service all updated.
- **Bearer token auth** on every endpoint except `GET /health` (open for liveness).
  Token resolves: env `LARRY_API_TOKEN` > `config.json api.token` > auto-generated
  (32-char, printed at startup). Constant-time compare (`hmac.compare_digest`).
- Verified: /health open 200; protected route 401 (no token), 401 (wrong token),
  200 (correct token); /run/stream 401 without token, full SSE with token.
- Compose `api` service: `LARRY_API_TOKEN=${LARRY_API_TOKEN:-}` (set in shell/.env,
  else generated + visible in `docker logs locallarry-api`); healthcheck hits open /health.

## Open items (not built — pending decision)
- **Parallel subagent steps** — sequential today; fights 8 GB VRAM, so arguably correct.
- **num_gpu tuning** — LocalLarry-15b at 18 is conservative; try 22 if no OOM.
- **Routing experiment** — move `editor` to `fast` in config.json if multi-step latency bothers.

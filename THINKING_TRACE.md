# LARRY G-FORCE — Build Thinking Trace

An extensive record of the reasoning, decisions, dead-ends, and gotchas behind
the 2026-06-10 rebuild. Written so a future session (or a future you) can
understand *why* the code looks the way it does, not just *what* it does.

---

## 0. Framing

The starting point was a sprawling `GITHUB/` tree: ~200 loose files at the root
(multiple `agent_v2.py` copies, `.bak` litter, ISOs, PDFs, vendored repos) plus a
cleaner `GITHUB/src/`. The pasted spec asked for an organized agent: subdirectories
for `subagents/`, `memory/`, `tools/`, `utils/`, a `config.py/json`, a `main.py`
entry point, custom GGUF-tuned Ollama models, and native (non-regex) tool calling.

**Decision: treat `src/` as the canonical source and build the new structure there.**
Rationale: `src/` already had the freshest single copies of the key modules
(`agent_v2.py`, `telegram_bot.py`, `model_router.py`), and a grep confirmed those
were the ones referenced by the existing config. Building at the root would mean
fighting the duplicate litter; building in `src/` gave a clean island.

---

## 1. Directory structure & the chroma move

Created `subagents/`, `memory/`, `tools/`, `utils/` with `__init__.py`.

**The one risky move:** `src/chroma_db/` (34.6 MB of real vector data — two
collections + `chroma.sqlite3`) had to move into `memory/chroma_db/`. Before
moving I measured it (`Measure-Object Length -Sum`) to confirm it wasn't an empty
scaffold I'd be clobbering. Then `Move-Item` (not copy-delete) to keep it atomic.

**Follow-through:** moving the data dir silently breaks every hardcoded path. I
grepped for `chroma_db` across `src/*.py` and found 4 files referencing it. Updated:
- `larry_paths.py`: `BASE_DIR / "memory" / "chroma_db"`
- `agent_v2.py`: 3 sites (RAG init, YouTube summarizer x2)
- `telegram_bot.py`: 1 site
- `larry_config.json`: `rag.chroma_path` → `./memory/chroma_db`

Lesson reinforced: **a move is only half done until you've chased the references.**

---

## 2. config.py / config.json — why a second config

There was already a `larry_config.json` that legacy modules read. I did **not**
rip it out — too many modules depend on it, and breaking them wasn't the task.
Instead `config.json` is the *new* canonical config for the new layer, and
`config.py` is its loader. The two coexist; legacy reads `larry_config.json`,
new code reads `config.json`.

**Key design choices in config.py:**
- `BASE_DIR = Path(__file__).resolve().parent` — portable, travels with the code.
- `get_path()` resolves relative paths in config under BASE_DIR → absolute. So the
  config stays portable but callers always get absolute paths.
- `resolve_model(role)` — the single most important abstraction. Config refers to
  models by *role* ("main", "fast", "uncensored", "embedding"), not literal names.
  `resolve_model` maps role → configured model, then checks Ollama's `/api/tags`;
  if the preferred model isn't installed, it falls back via
  `models.fallbacks`. This is why the very first `main.py status` worked even
  before the LocalLarry-* models existed — it transparently fell back to the stock
  bases (qwen3-coder, qwen3:8b, dolphin3:8b). After building the models, the same
  command resolved to the LocalLarry-* names with zero code change.
- `os.environ.setdefault("OLLAMA_NUM_PARALLEL", "1")` at import — enforces the
  single-model-at-a-time rule everywhere, so CLI + Telegram never double-load 8 GB.

---

## 3. The Ollama models — adapting the spec to reality

The spec recommended `qwen2.5:14b-instruct-q4_K_M` as the main base. **It wasn't
installed.** `ollama list` showed what was actually present. Rather than force a
14 GB download, I matched the *intent* (14B-class, strong tool calling) to what
existed:
- **main = qwen3-coder:30b-a3b-q4_K_M.** It's nominally 30B/18 GB which looks too
  big for 8 GB, BUT it's an **MoE with ~3B active params per token** — so token
  latency behaves closer to a 3B model while quality is high, and the inactive
  weights spill to the 64 GB DDR5. This is the right pick *because of*, not despite,
  the hardware. (A reviewer flagged this as "risky on 8 GB"; the MoE distinction is
  the rebuttal — and the tests bore it out: multi-step loops ran fine.)
- **fast = qwen3:8b**, **uncensored = dolphin3:8b** — both ~5 GB, full GPU offload.

**The Modelfile gotcha that cost a build cycle:** Ollama's `ollama create` parser
does **not** accept inline `#` comments on `PARAMETER` lines. `PARAMETER num_ctx
32768  # 32k context` fails with `invalid int value [32768  # 32k...]`. Fix: move
every comment to its own line above the parameter. First build attempt failed on
all three models for exactly this; second attempt (comments hoisted) succeeded.

`num_gpu 18` on the 15b is deliberately conservative (~7 GB, leaves headroom).
Documented in the Modelfile that it can go to 22 if no OOM, or down to 14 for
gaming. The 8B models use `num_gpu 99` (offload everything).

---

## 4. Native tool calling — and the qwen3-coder text-format trap

The spec was emphatic: use Ollama's **native** tool calling (`tools=[...]` in
`/api/chat`), not regex detection. `subagents/base.py` implements the loop:
send messages+tools, read `message.tool_calls`, dispatch to python callables,
append `role:"tool"` results, repeat up to `max_turns`.

**The trap, discovered by testing:** the executor and editor tests passed
immediately. The **debugger test silently did nothing** — the model produced a
final text answer with zero tool calls. Reading the raw output revealed why:
qwen3-coder *sometimes* emits tool calls as **text** in its own XML-ish format:

```
<function=run_terminal>
<parameter=command>python broken_calc.py</parameter>
</function>
```

…instead of populating the native `tool_calls` field. When it does this, the
native loop sees no tool calls and returns the text as the answer — so the agent
"describes" instead of "acts", exactly the failure mode the spec warned about.

**Fix:** `parse_text_tool_calls()` in `base.py` — a fallback that, *only when
native `tool_calls` is empty*, scans the content for `<function=...>` blocks (and
`<tool_call>{json}</tool_call>` as a secondary form) and reconstructs the same
structure native calls use. Native always takes precedence; the parser is pure
rescue. After adding it, the debugger re-test rescued all 4 of its calls and
completed the full reproduce→read→fix→verify cycle. This fallback is **essential**
with qwen3-coder, not optional — without it ~half the agent's intended actions
evaporate.

Unit-tested the parser standalone (including int coercion of `<parameter>` values
and trailing `</tool_call>` noise) before trusting it in the loop.

---

## 5. The orchestration layer — single run(), routing, planning

The pasted reviews asked for: per-task model forcing, a clean single `run()`
entry, LLM-based planning, priority-based unloading, intelligent routing,
hardware/complexity awareness, and routing logs. I built these as **small,
composable modules** rather than one mega-class:

- `utils/hardware_monitor.py` — `nvidia-smi` parse → VRAM %, `can_use_heavy_model()`.
- `utils/task_complexity.py` — keyword + length scoring → low/med/high/very_high → role.
- `utils/model_manager.py` — **priority-based unloading.** `keep_loaded` models in
  config (LocalLarry-Fast, nomic-embed-text) are *never* evicted by us; everything
  else is fair game. `free_for(target)` evicts non-target, non-protected loaded
  models; `unload_if_needed()` triggers on VRAM threshold. Uses `/api/ps` to see
  what's loaded and `keep_alive:0` to evict.
- `utils/intelligent_router.py` — decision order: **force_model → subagent role →
  complexity → hardware degrade.** Every decision logged via the existing
  `persistence_logger.log_model_routing`.
- `orchestrator.py` — ties it together: `run()` (classify → route → free VRAM →
  recall memory → execute → record/remember) and `plan()`/`run_goal()` for LLM
  decomposition.

**The bug the first orchestrator test caught:** I initially put the blanket
`unload_if_needed()` *before* routing. With LocalLarry-15b at 94.9% VRAM, that
evicted the 15b at the start of **every step** — forcing three 18 GB reloads in a
3-step plan. Fix: route first, then call `free_for(model)` which *never evicts the
target or keep_loaded* — so a hot model is reused, not reloaded. Also added an
`_is_loaded()` guard to the router's degrade path: don't downgrade a heavy model to
the fast one if the heavy one is *already resident* (reusing a loaded model is free;
swapping it out is not). These two fixes are the difference between "works" and
"thrashes the GPU."

**LLM planning detail:** `plan()` calls the planner model with `format:"json"`
(Ollama constrained decoding) and a prompt that enumerates the 5 subagents. It
validates the returned steps (agent must be a real subagent, task non-empty,
capped at max_steps) and falls back to a single classified step if the JSON is
junk. `run_goal()` executes steps sequentially, **handing each step's result
forward as context** to the next — so step 2 sees what step 1 found.

---

## 6. Cross-session memory — the "learning" loop (Hermes-parity)

A CLI that forgets everything between runs isn't an agent, it's a script. So the
orchestrator gained `recall_context()` (inject top-3 relevant past results before
a run) and `remember_result()` (auto-store the outcome to chroma after). Both are
**guarded with try/except** — if embeddings/chroma are unreachable (e.g. Docker
standalone with no Ollama), they degrade to no-ops rather than crashing the run.
Toggle: `config.orchestrator.use_memory`.

Proven end-to-end in Docker: an executor task run via the API was auto-remembered
as a `task_result` memory, then surfaced in a later `/memory?q=` query. The agent
now accumulates knowledge across runs and restarts.

---

## 7. Docker — slim image, real GPU, the port trap

**Image:** `python:3.11-slim` + ffmpeg (audio decode) + curl. Crucially I wrote a
**separate `requirements-docker.txt`** — curated, no torch/playwright/langchain.
The full `requirements.txt` would balloon the image with multi-GB deps the new
layer doesn't use. The HTTP API uses **stdlib `http.server`** specifically so the
slim image needs *zero* extra web deps.

**OLLAMA_HOST env override:** `config.py` now reads `OLLAMA_HOST` from env first
(compose injects `http://ollama:11434`), falling back to config.json. Verified the
override resolves correctly. This is what lets the same image talk to the
in-compose Ollama *or* the native Windows Ollama via `host.docker.internal:11434`.

**Platform-aware prompts:** the original subagent prompts hardcoded "Windows
cmd.exe, use dir/type/tasklist". Inside the Linux container that's wrong. Added
`PLATFORM_NOTE` in `base.py`, switched by `os.name`, and threaded it through all 5
subagent prompts. Verified: the in-container executor used `uname -r` and
`ls -la`, not `dir`. Without this the containerized agent would emit Windows
commands into a Linux shell.

**compose services:** `ollama` (GPU passthrough, modelfiles mounted),
`model-init` (run-once profile that pulls bases + builds LocalLarry-*),
`agent` (named volumes for memory/logs/sandbox/exports), `api` (profile, :8080).

**The port 11434 trap:** native Windows Ollama already owns 11434. `docker compose
up ollama` collides on the port mapping. Documented three escapes: stop native
first, drop the `ports:` block, or just point containers at
`host.docker.internal`. For daily use, keeping native Ollama + running only the
agent/api container is cheapest (the containerized Ollama starts with an empty
model store anyway until `model-init` runs its ~30 GB of pulls).

---

## 8. Transcription — the cuBLAS reality

First transcribe test: `faster_whisper` wasn't installed. Installed it into the
venv. Second test: it loaded but failed at runtime — the agent itself diagnosed it
by running `where cublas64_12.dll` (not found). CUDA inference needs the cuBLAS 12
runtime this box lacks. **Fix:** `transcribe_file()` now tries `device="auto"`
first, then falls back to `device="cpu", compute_type="int8"`. Third test:
word-perfect transcript. Also bumped transcribe's `max_turns` 2→4 so it has room to
diagnose+retry. (Test audio was generated with Windows TTS `System.Speech` to a
known-content WAV — a repeatable fixture.)

---

## 9. What I deliberately did NOT do

- **Didn't rewrite legacy modules** (agent_v2.py, telegram_bot.py, model_router.py)
  beyond the chroma path edits. The task was structure + new layer, not a rewrite.
- **Didn't delete the duplicate litter** at the GITHUB root. Destructive, not asked
  for, and some "duplicates" may be intentional backups. Flagged, didn't touch.
- **Didn't add token streaming, API auth, or parallel steps.** Streaming is the
  biggest remaining UX gap (whole-turn responses today). Auth matters before the
  :8080 API leaves localhost. Parallel steps fight 8 GB VRAM. All three are
  documented as open items pending a decision rather than half-built.

---

## 10. Verification philosophy used throughout

Every component was tested with a **real task and observed behavior**, not assumed:
each subagent ran an end-to-end task; the orchestrator ran a multi-step plan; the
Docker image was smoke-tested standalone *and* against live Ollama; the API was
hit with curl/Invoke-WebRequest for every endpoint; the memory loop was proven by
observing an auto-stored result resurface in a later query. Where a test failed
(debugger silent, transcribe cuBLAS, orchestrator VRAM thrash), the failure was
root-caused and fixed, then re-tested — not papered over. py_compile gates every
batch of edits before runtime tests.

"""
main.py — entry point for LARRY G-FORCE: starts all agent tools and services.

Usage:
    python main.py                      # status: config, models, DBs, services
    python main.py status               # same as above
    python main.py run <agent> <task> [--model M]   # run one subagent on a task
    python main.py auto <goal> [--model M]          # orchestrator: route or plan+execute
    python main.py plan <goal>          # show the LLM-generated plan only
    python main.py chat [--agent A]     # interactive REPL (memory-backed)
    python main.py serve [--port P]     # start the HTTP API (default :7333, bearer auth)
    python main.py telegram             # start the Telegram bot (foreground)
    python main.py all                  # init everything + start Telegram bot

Subagents: executor, editor, searcher, transcribe, debugger
Examples:
    python main.py run executor "Check what is listening on port 11434"
    python main.py auto "Find which script writes larry.log and fix its log level"
    python main.py run executor "scan localhost ports" --model LocalLarry-Uncensored
"""

import argparse
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import CONFIG, OLLAMA_HOST, ensure_dirs, get_path, resolve_model, installed_models  # noqa: E402


def init_services(verbose: bool = True) -> dict:
    """Create directories, initialize all databases, verify Ollama."""
    ensure_dirs()

    from utils.memory_manager import MemoryManager
    from utils.task_manager import TaskManager

    services = {
        "memory": MemoryManager(),   # creates sessions.db + skills.db
        "tasks": TaskManager(),      # creates tasks.db
    }

    if verbose:
        models = installed_models()
        print(f"{CONFIG.get('agent_name', 'LARRY')} v{CONFIG.get('version', '?')}")
        print(f"  base dir : {BASE_DIR}")
        print(f"  ollama   : {OLLAMA_HOST} "
              f"{'[online, %d models]' % len(models) if models else '[OFFLINE]'}")
        for role in ("main", "fast", "uncensored", "embedding"):
            print(f"  model {role:10s} -> {resolve_model(role)}")
        for key in ("chroma_db", "tasks_db", "skills_db", "sessions_db"):
            p = get_path(key)
            print(f"  {key:12s} -> {p.name} {'[ok]' if p.exists() else '[missing]'}")
    return services


def run_subagent(name: str, task: str, model: str = None) -> int:
    from subagents import SUBAGENTS
    cls = SUBAGENTS.get(name)
    if cls is None:
        print(f"Unknown subagent '{name}'. Available: {', '.join(SUBAGENTS)}")
        return 1
    init_services(verbose=False)
    if model:
        model = resolve_model(model)
    result = cls(model=model).run(task)
    print(result)
    return 0


def run_auto(goal: str, model: str = None) -> int:
    """Orchestrator entry: plan a goal and execute it with subagents."""
    init_services(verbose=False)
    from orchestrator import get_orchestrator
    out = get_orchestrator().run_goal(goal, force_model=model)
    print()
    print(f"=== {out['status'].upper()} "
          f"({out['steps_run']}/{out['steps_planned']} steps) ===")
    for i, r in enumerate(out["results"], 1):
        print(f"\n--- step {i}: {r['subagent_used']} ({r['model_used']}) "
              f"[{r['status']}] ---")
        print(str(r["result"]).strip())
    return 0 if out["status"] == "completed" else 1


def show_plan(goal: str) -> int:
    init_services(verbose=False)
    from orchestrator import get_orchestrator
    steps = get_orchestrator().plan(goal)
    print(f"Plan for: {goal}")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. [{s['agent']}] {s['task']}")
    return 0


def start_server(port: int = None) -> int:
    init_services(verbose=False)
    from server import serve
    serve(port=port)
    return 0


def chat_repl(agent: str = None) -> int:
    """Interactive REPL backed by the orchestrator (memory + routing)."""
    # Model tokens may contain Unicode the console codec can't encode
    # (Windows cp1252). Make stdout tolerant rather than crash mid-stream.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    init_services(verbose=False)
    from orchestrator import get_orchestrator
    orch = get_orchestrator(); orch.verbose = False
    print("LARRY G-FORCE chat. Type a task; 'auto <goal>' to plan; "
          "'exit' to quit.\n")
    while True:
        try:
            line = input("larry> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", ":q"):
            break
        try:
            if line.lower().startswith("auto "):
                out = orch.run_goal(line[5:].strip())
                for r in out["results"]:
                    print(f"\n[{r['subagent_used']}] {str(r['result']).strip()}")
            else:
                # Streamed: print routing header, tool activity, then live tokens
                started = False
                for ev in orch.run_stream(line, agent=agent):
                    t = ev.get("type")
                    if t == "routing":
                        print(f"[{ev['subagent']} via {ev['model']}] ", end="", flush=True)
                    elif t == "tool":
                        print(f"\n  > {ev['name']}("
                              f"{json.dumps(ev['args'])[:80]}) ", end="", flush=True)
                    elif t == "tool_result":
                        print("[ok]" if ev["success"] else "[fail]",
                              end="", flush=True)
                    elif t == "token":
                        if not started:
                            print()
                            started = True
                        print(ev["text"], end="", flush=True)
                print("\n")
        except Exception as e:
            print(f"error: {e}")
    return 0


def start_telegram() -> int:
    """Run the Telegram bot in the foreground (blocks)."""
    bot = os.path.join(BASE_DIR, "telegram_bot.py")
    if not os.path.exists(bot):
        print(f"telegram_bot.py not found at {bot}")
        return 1
    env = os.environ.copy()
    env["OLLAMA_NUM_PARALLEL"] = "1"
    print(f"Starting Telegram bot ({bot}) ...")
    return subprocess.call([sys.executable, bot], cwd=BASE_DIR, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="LARRY G-FORCE agent launcher")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="initialize and show status (default)")
    p_run = sub.add_parser("run", help="run a subagent on a task")
    p_run.add_argument("agent", help="executor|editor|searcher|transcribe|debugger")
    p_run.add_argument("task", nargs="+", help="the task prompt")
    p_run.add_argument("--model", help="force a model (name or role like 'fast')")
    p_auto = sub.add_parser("auto", help="orchestrator: plan and execute a goal")
    p_auto.add_argument("goal", nargs="+", help="the goal")
    p_auto.add_argument("--model", help="force a model for all steps")
    p_plan = sub.add_parser("plan", help="show the LLM-generated plan for a goal")
    p_plan.add_argument("goal", nargs="+", help="the goal")
    p_chat = sub.add_parser("chat", help="interactive REPL")
    p_chat.add_argument("--agent", help="pin all turns to one subagent")
    p_serve = sub.add_parser("serve", help="start the HTTP API")
    p_serve.add_argument("--port", type=int, help="port (default 7333)")
    sub.add_parser("telegram", help="start the Telegram bot")
    sub.add_parser("all", help="init services and start the Telegram bot")

    args = parser.parse_args()

    if args.cmd == "run":
        return run_subagent(args.agent, " ".join(args.task), model=args.model)
    if args.cmd == "auto":
        return run_auto(" ".join(args.goal), model=args.model)
    if args.cmd == "plan":
        return show_plan(" ".join(args.goal))
    if args.cmd == "chat":
        return chat_repl(agent=args.agent)
    if args.cmd == "serve":
        return start_server(port=args.port)
    if args.cmd == "telegram":
        return start_telegram()
    if args.cmd == "all":
        init_services()
        if CONFIG.get("features", {}).get("telegram_enabled", True):
            return start_telegram()
        print("telegram_enabled is false in config.json; nothing else to start.")
        return 0

    init_services()  # default: status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

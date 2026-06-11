"""
orchestrator.py — multi-agent orchestration layer for LARRY G-FORCE.

Single run() entry point:
  - classifies the task -> picks a subagent (or caller forces one)
  - routes to a model (hardware- + complexity-aware, per-task force_model)
  - frees VRAM first (priority-based unloading; keep_loaded models survive)
  - records the task in memory/tasks.db and persistence logs
LLM-based planning:
  - plan(goal)     -> JSON list of {agent, task} steps from the main model
  - run_goal(goal) -> plan + execute each step, handing context forward
"""

import json
import os
import sys
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import CONFIG, resolve_model  # noqa: E402
from subagents import SUBAGENTS  # noqa: E402
from subagents.base import _ollama_chat  # noqa: E402
from utils.intelligent_router import get_router  # noqa: E402
from utils.model_manager import get_model_manager  # noqa: E402
from utils.task_manager import TaskManager  # noqa: E402

try:
    from persistence_logger import log_task
except Exception:
    def log_task(*a, **k):
        pass

PLANNER_PROMPT = """You are the planner of LARRY G-FORCE, a local multi-agent system.
Break the user's goal into the smallest number of sequential steps (1-{max_steps}),
each handled by one subagent:

- executor: runs terminal/shell commands (processes, ports, services, scripts)
- editor: creates and edits files
- searcher: finds information in memory, local files, or web pages
- transcribe: transcribes audio/video files
- debugger: reproduces, diagnoses and fixes failing code

Respond with ONLY this JSON, nothing else:
{{"steps": [{{"agent": "<name>", "task": "<specific instruction>"}}]}}

A simple goal should be a single step. Each task must be self-contained and
concrete (full paths, exact commands to investigate, expected outcome)."""


class Orchestrator:
    """Single entry point for dispatching work to subagents."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.router = get_router()
        self.models = get_model_manager()
        self.tasks = TaskManager()
        cfg = CONFIG.get("orchestrator", {})
        self.planner_model = resolve_model(cfg.get("model", "main"))
        self.max_steps = cfg.get("max_steps", 6)
        self.use_memory = cfg.get("use_memory", True)
        self._memory = None  # lazy: needs Ollama embeddings + chroma

    def _log(self, msg: str):
        if self.verbose:
            print(f"[orchestrator] {msg}")

    def _mem(self):
        if self._memory is None:
            from utils.memory_manager import MemoryManager
            self._memory = MemoryManager()
        return self._memory

    def recall_context(self, task: str, n: int = 3) -> str:
        """Pull relevant past results from vector memory to prime the subagent."""
        if not self.use_memory:
            return ""
        try:
            hits = self._mem().recall(task, n=n)
        except Exception:
            return ""  # no embeddings/chroma -> degrade silently
        if not hits:
            return ""
        lines = [f"- {h['text'][:300]}" for h in hits if h.get("text")]
        return ("Relevant memory from past runs:\n" + "\n".join(lines)) if lines else ""

    def remember_result(self, task: str, result: str):
        if not self.use_memory:
            return
        try:
            self._mem().remember(
                f"Task: {task}\nResult: {str(result)[:1000]}",
                metadata={"kind": "task_result"})
        except Exception:
            pass

    # ------------------ classification ------------------

    @staticmethod
    def classify(task: str) -> str:
        t = task.lower()
        if any(kw in t for kw in ("debug", "fix", "error", "bug", "reproduce",
                                  "crash", "traceback", "investigate why")):
            return "debugger"
        if any(kw in t for kw in ("transcribe", "transcript", "audio file",
                                  "voice message", ".mp3", ".wav", ".ogg")):
            return "transcribe"
        if any(kw in t for kw in ("edit", "write file", "create file", "modify",
                                  "update file", "rename file", "refactor")):
            return "editor"
        if any(kw in t for kw in ("search", "find", "recall", "remember",
                                  "look up", "fetch", "what does", "where is")):
            return "searcher"
        return "executor"

    # ------------------ single-task entry point ------------------

    def run(self, task: str, agent: Optional[str] = None, context: str = "",
            force_model: Optional[str] = None) -> Dict:
        """Main orchestration entry point. agent/force_model override routing."""
        # 1. Pick subagent + model
        subagent_name = agent if agent in SUBAGENTS else self.classify(task)
        routing = self.router.route(task, subagent=subagent_name,
                                    context=context, force_model=force_model)
        model = routing["model"]
        self._log(f"agent={subagent_name} model={model} ({routing['reason']})")

        # 2. Make room for the chosen model. free_for never evicts the target
        # itself or keep_loaded models, so a hot model is reused, not reloaded.
        freed = self.models.free_for(model)
        if freed:
            self._log(f"freed for {model}: {freed}")

        # 3. Prime with relevant memory from past runs (cross-session learning)
        recalled = self.recall_context(task)
        if recalled:
            self._log("injected memory from past runs")
            context = (context + "\n" + recalled).strip() if context else recalled

        # 4. Record + execute
        task_id = self.tasks.add(f"{subagent_name}: {task[:120]}", task,
                                 assigned_to=subagent_name)
        self.tasks.start(task_id)
        log_task(task, "started", metadata={
            "subagent": subagent_name, "model": model,
            "complexity": routing["complexity"], "reason": routing["reason"]})

        try:
            sub = SUBAGENTS[subagent_name](model=model, verbose=self.verbose)
            result = sub.run(task, context=context or None)
            status = "completed"
            self.tasks.complete(task_id, str(result)[:2000])
            self.remember_result(task, result)
        except Exception as e:
            result = f"Error in subagent {subagent_name}: {e}"
            status = "failed"
            self.tasks.fail(task_id, str(e)[:2000])

        log_task(task, status, result=str(result)[:800])
        return {
            "task": task,
            "task_id": task_id,
            "subagent_used": subagent_name,
            "model_used": model,
            "routing_reason": routing["reason"],
            "complexity": routing["complexity"],
            "status": status,
            "result": result,
        }

    # ------------------ streaming single-task entry point ------------------

    def run_stream(self, task: str, agent: Optional[str] = None, context: str = "",
                   force_model: Optional[str] = None):
        """
        Streaming variant of run(). Yields event dicts:
          {"type":"routing","subagent":...,"model":...,"reason":...}
          {"type":"token","text":...} / {"type":"tool",...} / {"type":"tool_result",...}
          {"type":"final","content":...,"status":...,"task_id":...}
        Same routing, VRAM management, memory recall/remember, and tasks.db
        recording as run().
        """
        subagent_name = agent if agent in SUBAGENTS else self.classify(task)
        routing = self.router.route(task, subagent=subagent_name,
                                    context=context, force_model=force_model)
        model = routing["model"]
        self._log(f"agent={subagent_name} model={model} ({routing['reason']}) [stream]")
        yield {"type": "routing", "subagent": subagent_name, "model": model,
               "reason": routing["reason"], "complexity": routing["complexity"]}

        self.models.free_for(model)

        recalled = self.recall_context(task)
        if recalled:
            context = (context + "\n" + recalled).strip() if context else recalled

        task_id = self.tasks.add(f"{subagent_name}: {task[:120]}", task,
                                 assigned_to=subagent_name)
        self.tasks.start(task_id)
        log_task(task, "started", metadata={"subagent": subagent_name,
                 "model": model, "streamed": True})

        final = ""
        status = "completed"
        try:
            sub = SUBAGENTS[subagent_name](model=model, verbose=self.verbose)
            for ev in sub.run_stream(task, context=context or None):
                if ev.get("type") == "final":
                    final = ev.get("content", "")
                else:
                    yield ev
            self.tasks.complete(task_id, final[:2000])
            self.remember_result(task, final)
        except Exception as e:
            final = f"Error in subagent {subagent_name}: {e}"
            status = "failed"
            self.tasks.fail(task_id, str(e)[:2000])

        log_task(task, status, result=final[:800])
        yield {"type": "final", "content": final, "status": status,
               "task_id": task_id, "subagent_used": subagent_name,
               "model_used": model}

    # ------------------ LLM-based planning ------------------

    def plan(self, goal: str) -> List[Dict]:
        """Ask the planner model to break a goal into subagent steps."""
        self._log(f"planning with {self.planner_model} ...")
        self.models.free_for(self.planner_model)
        resp = _ollama_chat(
            self.planner_model,
            [{"role": "system",
              "content": PLANNER_PROMPT.format(max_steps=self.max_steps)},
             {"role": "user", "content": goal}],
            format="json",
        )
        content = resp.get("message", {}).get("content", "{}")
        try:
            steps = json.loads(content).get("steps", [])
        except json.JSONDecodeError:
            steps = []
        steps = [s for s in steps
                 if isinstance(s, dict) and s.get("agent") in SUBAGENTS
                 and s.get("task")][:self.max_steps]
        if not steps:  # planner failed -> single classified step
            steps = [{"agent": self.classify(goal), "task": goal}]
        return steps

    def run_goal(self, goal: str, force_model: Optional[str] = None) -> Dict:
        """Plan a complex goal, then execute each step, handing results forward."""
        steps = self.plan(goal)
        self._log(f"plan: {len(steps)} step(s)")
        for i, s in enumerate(steps, 1):
            self._log(f"  {i}. [{s['agent']}] {s['task'][:100]}")

        results, context = [], ""
        for i, step in enumerate(steps, 1):
            self._log(f"--- step {i}/{len(steps)}: {step['agent']} ---")
            out = self.run(step["task"], agent=step["agent"],
                           context=context, force_model=force_model)
            results.append(out)
            context = (context + f"\nStep {i} ({step['agent']}) result:\n"
                       f"{str(out['result'])[:1500]}").strip()
            if out["status"] == "failed":
                self._log(f"step {i} failed; stopping plan")
                break

        return {
            "goal": goal,
            "steps_planned": len(steps),
            "steps_run": len(results),
            "status": ("completed" if results and
                       all(r["status"] == "completed" for r in results)
                       else "failed"),
            "results": results,
        }


_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


if __name__ == "__main__":
    o = get_orchestrator()
    goal = " ".join(sys.argv[1:]) or "Check the ollama service status"
    print(json.dumps(o.run_goal(goal), indent=2, default=str)[:4000])

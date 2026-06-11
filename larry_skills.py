#!/usr/bin/env python3
"""
larry_skills.py — a pack of concrete, local skills for LARRY G-FORCE.

A "skill" is just a named callable registered on a toolkit's SkillRegistry. This
module shows the extension pattern: write a function, register it, and it's
instantly reachable via `/skill <name>` and via the run_skill function-tool — no
changes to the toolkit needed.

The skills here lean toward what the operator actually watches on this box
(RTX 4060 8GB VRAM, Ollama, NVMe usage), cross-platform aware.

Wire them in:
    from mcp_client import get_mcp_toolkit
    from larry_skills import register_all
    tk = get_mcp_toolkit()
    register_all(tk)            # now /skill gpu, /skill ollama, etc. work
"""

from __future__ import annotations

__version__ = "1.0.0"

import os
import shutil
import subprocess
from typing import Any


def _sh(cmd: str, timeout: int = 20) -> str:
    """Run a shell one-liner and return combined output (skills stay simple)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip() or f"(no output, exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout}s)"
    except Exception as e:
        return f"(error: {e})"


# ── individual skills ─────────────────────────────────────────────────────────
def skill_gpu(_: str = "") -> str:
    """GPU / VRAM snapshot via nvidia-smi (matches the RTX 4060 8GB workflow)."""
    if not shutil.which("nvidia-smi"):
        return "nvidia-smi not on PATH — NVIDIA driver/CUDA not visible from here."
    out = _sh("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu "
              "--format=csv,noheader,nounits")
    if "(" in out and "error" in out.lower():
        return out
    lines = []
    for row in out.splitlines():
        cells = [c.strip() for c in row.split(",")]
        if len(cells) >= 5:
            name, used, total, util, temp = cells[:5]
            pct = (float(used) / float(total) * 100) if total not in ("", "0") else 0
            lines.append(f"{name}: {used}/{total} MiB VRAM ({pct:.0f}%), "
                         f"{util}% util, {temp}°C")
    return "\n".join(lines) or out


def skill_ollama(_: str = "") -> str:
    """What Ollama has loaded right now (VRAM pressure check before routing)."""
    if not shutil.which("ollama"):
        return "ollama not on PATH."
    ps = _sh("ollama ps")
    return ps


def skill_models(_: str = "") -> str:
    """List installed Ollama models."""
    if not shutil.which("ollama"):
        return "ollama not on PATH."
    return _sh("ollama list")


def skill_sysinfo(_: str = "") -> str:
    """OS / CPU / RAM snapshot, cross-platform."""
    if os.name == "nt":
        cpu = _sh('wmic cpu get name /value')
        mem = _sh('wmic ComputerSystem get TotalPhysicalMemory /value')
        return f"{cpu}\n{mem}".strip()
    # linux/mac
    parts = [_sh("uname -a")]
    if shutil.which("nproc"):
        parts.append("cores: " + _sh("nproc"))
    if os.path.exists("/proc/meminfo"):
        parts.append(_sh("grep MemTotal /proc/meminfo"))
    return "\n".join(parts)


def skill_disk(_: str = "") -> str:
    """Disk usage (NVMe capacity watch)."""
    if os.name == "nt":
        return _sh("wmic logicaldisk get DeviceID,FreeSpace,Size")
    return _sh("df -h")


def skill_pyenv(_: str = "") -> str:
    """Python + key ML package versions — quick CUDA/torch sanity check."""
    import sys
    code = (
        "import sys; print('python', sys.version.split()[0]);\n"
        "try:\n import torch;"
        " print('torch', torch.__version__, 'cuda_available', torch.cuda.is_available(),"
        " 'cuda', getattr(torch.version,'cuda',None))\n"
        "except Exception as e:\n print('torch: not importable ->', e)"
    )
    return _sh(f'"{sys.executable}" -c "{code}"', timeout=40)


# ── registration ──────────────────────────────────────────────────────────────
_SKILLS = {
    "gpu":      (skill_gpu,     "GPU/VRAM snapshot via nvidia-smi"),
    "ollama":   (skill_ollama,  "models currently loaded in Ollama (ollama ps)"),
    "models":   (skill_models,  "installed Ollama models (ollama list)"),
    "sysinfo":  (skill_sysinfo, "OS/CPU/RAM snapshot"),
    "disk":     (skill_disk,    "disk usage"),
    "pyenv":    (skill_pyenv,   "python + torch/CUDA versions"),
}


def register_all(toolkit: Any) -> int:
    """Register every skill in this pack onto a toolkit's SkillRegistry.

    Returns the number registered. Safe to call once per toolkit.
    """
    reg = getattr(toolkit, "skills", None)
    if reg is None or not hasattr(reg, "register"):
        raise TypeError("toolkit has no .skills SkillRegistry to register onto")
    count = 0
    for name, (fn, desc) in _SKILLS.items():
        reg.register(name, fn, description=desc, usage=f"/skill {name}")
        count += 1
    return count


if __name__ == "__main__":
    print(f"larry_skills v{__version__}")
    # demonstrate registration + a couple of live runs
    try:
        from mcp_client import get_mcp_toolkit
        tk = get_mcp_toolkit()
        n = register_all(tk)
        print(f"registered {n} skills:", [s['name'] for s in tk.skills.list()])
        print("\n/skill sysinfo:\n" + tk.dispatch("/skill sysinfo")[:300])
        print("\n/skill disk:\n" + tk.dispatch("/skill disk")[:300])
        print("\n/skill gpu:\n" + tk.dispatch("/skill gpu")[:200])
    except Exception as e:
        print("toolkit wiring failed:", e)
        print("\nstandalone skill output (sysinfo):")
        print(skill_sysinfo()[:300])

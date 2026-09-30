"""MediumBOOM last-mile directed assembly writer.

Tries Astra, then Poolside Laguna, then a template that maps uncovered
cover-point names to RV32IM ops. The writer does not score; Verilator does.
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request
from pathlib import Path

from .real_backend import BR, MEM, M_EXT, SAFE_I

_UNIT_OPS = {
    "lsu": MEM,
    "bpred": BR + ["JAL"],
    "issue_q": SAFE_I + M_EXT,
}


def _ops_from_names(names: list[str], rng: random.Random, n: int) -> list[str]:
    pool: list[str] = []
    blob = " ".join(names).lower()
    if any(k in blob for k in ("lsu", "lw", "sw", "lh", "dport", "mem")):
        pool += MEM * 3
    if any(k in blob for k in ("bpred", "beq", "branch", "fetch", "jal")):
        pool += BR * 2 + ["JAL"]
    if any(k in blob for k in ("mul", "div", "rem", "multiplier", "divider")):
        pool += M_EXT * 3
    if any(k in blob for k in ("alu", "decoder", "addi", "issue", "exec")):
        pool += SAFE_I
    if not pool:
        pool = list(SAFE_I + MEM + BR + M_EXT)
    return [rng.choice(pool) for _ in range(n)]


def write_template_asm(path: Path, names: list[str], rng: random.Random, n: int = 20) -> str:
    from .verilator_backend import write_tcm_program

    ops = _ops_from_names(names, rng, n)
    write_tcm_program(path, ops, rng)
    return "template:" + ",".join(ops[:8])


_LLM_CALLS = 0
_LLM_FAILED = False


def _openai_compatible(url: str, key: str, model: str, names: list[str], timeout_s: float) -> str | None:
    prompt = (
        "Write bare-metal RV32IM GNU assembler for a tiny RISC-V TCM core. "
        "Origin 0, no libc, symbol _start. End by storing 1 to symbol tohost. "
        "Target these uncovered coverage names; emit only real RV32IM ops "
        f"(ADD/ADDI/LW/SW/BEQ/MUL/DIV/JAL and friends):\n{names[:24]}\n"
        "Reply with assembler only, no markdown."
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": "You write RV32IM assembly. No markdown."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 700,
            "temperature": 0.2,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        return None
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    text = re.sub(r"^```(?:asm|gas|s)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"```$", "", text.strip())
    if "_start" not in text:
        return None
    return text


def _with_tcm_halt(text: str) -> str:
    if not text.endswith("\n"):
        text += "\n"
    if "0xF000" not in text and "0xf000" not in text.lower():
        text += (
            "    li x5, 0xF000\n"
            "    li x6, 1\n"
            "    sw x6, 0(x5)\n"
            "chia_halt:\n"
            "    j chia_halt\n"
        )
    return text


SELF_ASM = (
    Path(__file__).resolve().parent
    / "testbenches"
    / "verilator"
    / "last_mile_self.S"
)


def write_self_asm(path: Path, names: list[str], rng: random.Random) -> str:
    """Last-mile suite authored in this repo (not an API call). Verilator scores it."""
    text = SELF_ASM.read_text()
    # Tiny per-batch wobble so two directed jobs are not byte-identical,
    # without dropping any of the targeted ops.
    wobble = rng.randint(0, 7)
    text = text.replace("addi x7,  x5, 9", f"addi x7,  x5, {9 + wobble}", 1)
    path.write_text(text)
    blob = " ".join(names).lower()
    aims = []
    if "alu" in blob or "shift" in blob:
        aims.append("alu-shift-lanes")
    if "lsu" in blob or "dport" in blob:
        aims.append("lsu-widths")
    if "mul" in blob or "div" in blob or "issue" in blob:
        aims.append("m-ext")
    if "bpred" in blob or "fetch" in blob or "beq" in blob:
        aims.append("branches")
    return "self:" + (",".join(aims) if aims else "full-suite")


def write_last_mile_asm(path: Path, names: list[str], rng: random.Random) -> str:
    """Write path. Returns a short note of which writer fired.

    Default is ``auto``: Astra if keys exist, else Poolside if keyed, else
    ``self`` (``testbenches/verilator/last_mile_self.S``). Override with
    ``LAST_MILE_WRITER=self|template|astra|poolside|llm``.
    """
    global _LLM_CALLS, _LLM_FAILED
    mode = os.environ.get("LAST_MILE_WRITER", "auto").strip().lower()
    if mode in ("", "auto"):
        if os.environ.get("ASTRA_API_KEY") and os.environ.get("ASTRA_API_BASE"):
            mode = "astra"
        elif os.environ.get("POOLSIDE_API_KEY"):
            mode = "poolside"
        else:
            mode = "self"
    if mode in ("self", "grok", "cursor"):
        return write_self_asm(path, names, rng)
    if mode == "template":
        return write_template_asm(path, names, rng)

    budget = int(os.environ.get("LAST_MILE_LLM_CALLS", "2"))
    if mode in ("astra", "poolside", "llm") and not _LLM_FAILED and _LLM_CALLS < budget:
        astra_key = os.environ.get("ASTRA_API_KEY")
        astra_url = os.environ.get("ASTRA_API_BASE", "").rstrip("/")
        if astra_key and astra_url:
            model = os.environ.get("ASTRA_MODEL", "astra")
            _LLM_CALLS += 1
            text = _openai_compatible(f"{astra_url}/chat/completions", astra_key, model, names, 12.0)
            if text:
                path.write_text(_with_tcm_halt(text))
                return f"astra:{model}"
            _LLM_FAILED = True

        pool_key = os.environ.get("POOLSIDE_API_KEY")
        if pool_key and not _LLM_FAILED:
            url = os.environ.get("POOLSIDE_API_BASE", "https://inference.poolside.ai/v1/chat/completions")
            model = os.environ.get("POOLSIDE_MODEL", "poolside/laguna-s-2.1")
            _LLM_CALLS += 1
            text = _openai_compatible(url, pool_key, model, names, 12.0)
            if text:
                path.write_text(_with_tcm_halt(text))
                return f"poolside:{model}"
            _LLM_FAILED = True

    return write_template_asm(path, names, rng)

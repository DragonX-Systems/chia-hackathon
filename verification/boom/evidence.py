"""Conservative result classification for the BOOM last-mile model.

No API here retires coverage. Local unreachability must pass a separate
configuration/scope/assumption review before it can justify an exclusion.
"""
from __future__ import annotations


def classify(mode: str, native_status: str, returncode: int) -> str:
    """Classify one selected property, not an aggregate multi-cover job."""
    if mode not in {"cover", "bmc", "prove", "prove_unreachable"}:
        raise ValueError(f"Unknown formal mode: {mode}")
    status = native_status.split()[0].upper() if native_status.split() else "ERROR"
    if status in {"TIMEOUT", "UNKNOWN"}:
        return status
    if status == "PASS" and returncode == 0:
        return {"cover": "REACHED", "bmc": "BOUNDED_SAFE",
                "prove": "PROVEN", "prove_unreachable": "PROVEN_UNREACHABLE_LOCAL"}[mode]
    if status == "FAIL" and returncode == 2:
        return {"cover": "BOUNDED_UNREACHED", "bmc": "COUNTEREXAMPLE",
                "prove": "COUNTEREXAMPLE", "prove_unreachable": "REACHABILITY_COUNTEREXAMPLE"}[mode]
    return "ERROR"


def summarize(required: set[str], results: list[dict], identity: dict) -> dict:
    """Summarize already-reviewed per-instance results with exact run identity.

    Identity includes source, elaborated configuration, RTL, monitor, assumptions
    and reset hashes. This function cannot establish the trustworthiness of a
    supplied certificate; native evidence must be retained and reviewed.
    """
    required_keys = {"source", "config", "rtl", "monitor", "assumptions", "reset"}
    if set(identity) != required_keys or any(not isinstance(v, str) or not v for v in identity.values()):
        raise ValueError("Incomplete evidence identity")
    states = {pid: set() for pid in required}
    for row in results:
        if row["id"] not in states:
            raise ValueError(f"Unknown instantiated property: {row['id']}")
        if row.get("identity") != identity:
            raise ValueError("Evidence identity mismatch")
        if row.get("qualification") != "QUALIFIED" or not row.get("artifact"):
            raise ValueError("Unqualified evidence or missing native artifact")
        status = classify(row["mode"], row["native_status"], row["returncode"])
        if status in {"REACHED", "REACHABILITY_COUNTEREXAMPLE"} and not row.get("witness_replayed"):
            raise ValueError("Reachability requires a replayed witness")
        if status in {"PROVEN", "PROVEN_UNREACHABLE_LOCAL"} and not row.get("activation_passed"):
            raise ValueError("Proof requires reset and activation qualification")
        states[row["id"]].add(status)
    reached = {p for p, s in states.items() if s & {"REACHED", "REACHABILITY_COUNTEREXAMPLE"}}
    unreachable = {p for p, s in states.items() if "PROVEN_UNREACHABLE_LOCAL" in s}
    if reached & unreachable:
        raise ValueError("Contradictory reachability evidence for identical scope")
    # Open and unbound entries stay in the denominator. Proving a safety
    # assertion doesn't hit its activation or its associated scenario covers.
    return {"required": len(required), "reached": sorted(reached),
            "local_unreachable": sorted(unreachable),
            "unresolved": sorted(required - reached - unreachable),
            "hit_fraction": len(reached) / len(required) if required else None,
            "retired": [], "core_pruning_authorized": False}

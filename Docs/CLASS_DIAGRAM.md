# Class diagram

Appendix: planner-visible types and CHIA nodes.  
**Submission story:** [SUBMIT.md](../SUBMIT.md). Leftover-split narrative:
[ARCHITECTURE.md](Verification%20ChiaLoop%20Architecture%20Overview.md).

MediumBOOM entry: `experiments/coverage_growth/run_legacy_experiment.py --backend boom` → `loop.py` →
`boom_backend.py` / `enhancement.py` → `CoverageMeterNode`.

## Mermaid (classes)

```mermaid
classDiagram
    direction TB

    class CoverPoint {
        +str pid
        +str kind
        +str unit
        +str bucket
    }

    class FrozenModel {
        +list~CoverPoint~ points
        +str hmac
        +str seed
        +by_id()
    }

    class MeterSnapshot {
        +float hours
        +int n_total
        +int n_covered
        +int n_retired
        +float hit_frac
        +float closure_frac
        +list uncovered
    }

    class CoverageMeterNode {
        +Path store_path
        +FrozenModel model
        +set covered
        +set retired
        +float hours
        +snapshot() MeterSnapshot
        +merge_batch(hits, retired, hours) MeterSnapshot
    }

    class Allocation {
        +Action action
        +float hour_budget
        +list target_pids
        +dict parameters
        +str reason
    }

    class KnobState {
        +dict unit_boost
        +float freshness
    }

    class BatchResult {
        +Action action
        +float hours
        +list hits
        +list retired
        +str notes
    }

    class EngineYieldNode {
        +list rows
        +record(action, hours, hits, retired)
        +coverage_per_hour(action)
    }

    class YieldRow {
        +Action action
        +float hours
        +int hits
        +int retired
        +float cph
    }

    class LeftoverSplit {
        +list formal_targets
        +list directed_targets
        +float crv_closed_frac
        +int n_open
    }

    class TracePoint {
        +float hours
        +float hit_frac
        +float closure_frac
        +str action
        +int hits
        +int retired
    }

    class ArmResult {
        +str arm
        +int seed
        +list~TracePoint~ trace
        +float final_hit
        +float final_closure
    }

    class ArtifactWriterNode {
        +write(targets) Path
    }

    class SymbiYosysNode {
        +run(mode) FormalResult
    }

    CoverageMeterNode --> FrozenModel : owns
    CoverageMeterNode --> MeterSnapshot : snapshot / merge
    FrozenModel --> CoverPoint : contains
    EngineYieldNode --> YieldRow : rows
    LeftoverSplit ..> MeterSnapshot : from split_leftovers
    Allocation ..> KnobState : consumed by simulate_batch
    BatchResult ..> CoverageMeterNode : merge_batch
    ArmResult --> TracePoint : trace
    ArtifactWriterNode ..> BatchResult : Enh 3 collateral
    SymbiYosysNode ..> BatchResult : formal path
```

## Loop collaboration

```mermaid
sequenceDiagram
    participant Driver as run_experiment --backend boom
    participant Meter as CoverageMeterNode
    participant Plan as enhancement_plan
    participant Yield as EngineYieldNode
    participant Boom as boom_backend
    participant SBY as formal / soc_formal

    Driver->>Meter: snapshot
    Meter-->>Driver: MeterSnapshot (open names only)
    Driver->>Plan: plan(snap, yields, remaining)
    Plan-->>Driver: Allocation
    alt action == formal
        Driver->>SBY: cover BMC (LSU; ChipTop may fall back)
        SBY-->>Boom: hits + retired
    else torture / riscv_dv / directed
        Driver->>Boom: SoC ELF + PASS credits
        Boom-->>Driver: hits (no retire)
    end
    Driver->>Meter: merge_batch(hits, retired, hours)
    Driver->>Yield: record(...)
```

## Module → type ownership

| Type | Defined in |
|------|------------|
| `CoverPoint`, `FrozenModel` | `coverage_model.py` |
| `CoverageMeterNode`, `MeterSnapshot` | `meter.py` |
| `Allocation`, `BatchResult`, `KnobState` | `engines.py` |
| `EngineYieldNode`, `YieldRow`, `Action` | `yield_table.py` |
| `LeftoverSplit` | `prune.py` |
| `TracePoint`, `ArmResult` | `loop.py` |
| `SymbiYosysNode` | `nodes/symbiyosys.py` |
| `ArtifactWriterNode` | `nodes/artifact_writer.py` |

## Resource slots (`@ChiaFunction`)

| Resource | Typical borrower |
|----------|------------------|
| `coverage_meter: 1` | snapshot / merge_batch |
| `torture: 1` | SoC CRV carpet |
| `riscv_dv` + `verilator_run` | CRV generate + score |
| `formal: 1` | `chia_run_formal` |
| `artifact_writer: 1` | `chia_write_artifact` (Enh 3) |
| `verilator_run: 1` | directed / SoC score |

Cluster advertisement: `experiments/coverage_growth/cluster.yaml`.

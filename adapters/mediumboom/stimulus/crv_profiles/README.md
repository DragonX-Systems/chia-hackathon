# Supplied MediumBOOM CRV configurations

These five original `riscv-torture` configurations are the input files for
ChiaLoop's five CRV categories. The preparation script reads each file, applies
the additional controls in `../crv_profiles.json`, and saves the resulting
effective config and source-file hash in the catalog bundle. The originals are
left intact.

| Supplied file | Node | Main bias |
| --- | --- | --- |
| `integer_control.config` | `crv-1` | Integer ALU and branches |
| `load_store_ordering.config` | `crv-2` | Loads, stores and memory ordering |
| `mul_div_atomic.config` | `crv-3` | Multiply, divide and atomic operations |
| `privilege_exception.config` | `crv-4` | Control flow, then added checked trap cases |
| `mixed_long_running.config` | `crv-5` | Longer mixed stimulus |

The upstream generator does not expose precise atomic, fence, or trap ratios.
The seeded Scala overlay and checked trap prelude add those controls during
preparation. See [the preparation guide](../CRV_CATEGORIES.md).

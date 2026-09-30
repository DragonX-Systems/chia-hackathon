# DUT adapters

Adapters translate a frozen `JobSpec` into work for a particular DUT and
verification toolchain, then return `EngineEvidence` to `src/chialoop`.
They may depend on the reusable controller; the controller must never depend on
an adapter.

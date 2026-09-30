
## Purpose: Noteable contribution to Chia & verification methodology
1. Chia : dynamic graph distribution, use open-evolve & other nodes, use caching
2. ROI : compute-hours for given coverage reduced by 50%

### Enhancement Plan
- Baseline: plain Vanilla CRV

- Enh 1: faster convergence via BO / alpha-evolve

- Enh 2: Agentic pruning of impossible scenarios (formal)

- Enh 3: Agentic removal of hard to hit scenario from CRV & doing directed test instead

- Enh 4: Dispatching with license availablity


### Evolutionary stimulus-generator search
Treat the constrained-random generator's parameters (instruction mix weights, branch densities, memory access patterns) as a "genome." Use an evolutionary coding agent (BO or AdaEvolve/OpenEvolve, both already CHIA nodes) to evolve the generator config, with **coverage-gained-per-compute-hour** as the fitness function directly — not just coverage.
(Open: check medium boom coverage curve)

### Weeding out impossible coverage Scenarios
CRV --> 70% coverage --> agent analysis for pruning 

### Weeding out tough to hit scenarios
CRV --> 80% coverage --> agent analysis for pruning 
Alternate Method for Tough to hit scenarios

### Weeding out coverage Groups best covered by Formal
CRV --> 70% coverage --> agent analysis for pruning

# Notes

### Opens/Tasks
- Why is MediumBoom CRV showing linear coverage growth curve. dive in.
- Are there impossible coverage scenarios mentioned in mediumBoom cover points/grps.
- open evolve/ada evolve walkthrough.
- Formulate mechanism to prune impossible paths from
- Add chialoop Architecture document that has block diagram & flow diagram.
- Add Agents architecture doc (hard to hit, impossible, formal assertion artifact gen, directed test gen)
- Add mediumBOOM coverage artifact quality ( how many Coverpoint/covergrp/UCIS, how many SVA, how many knobs in CRV etc...)

#### Resolved Opens:
- What actually is the value that chia loop bring? infer from actual graph examples. 
- Go through Key chia functions
- Understand 2-3 examples of what really Chia bringing? tabulate what we liked abt chia and what we did not
- try build intuition of how same can be done on claude code graph-loop
- pen down high level flow for our project.

---


### Tasks
1. Async allocation by chialoop controller. ( as soon as 1 resource get free, it is allocated something)
2. having a snapshot of 50% coverage via CRV.. start your experiment then

3. 



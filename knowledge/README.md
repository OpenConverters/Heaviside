# Knowledge Base

Distilled power electronics design knowledge, ported from Proteus and
restructured for Heaviside's agent prompts. 54 files across 11
categories.

## Categories

| Category | Files | Feeds agent |
|---|---|---|
| `topologies/` | 25 | topology-selector, converter-designer |
| `magnetics/` | 7 | magnetic-pareto-picker |
| `simulation/` | 6 | simulation pipeline (sim/runner.py) |
| `components/` | 6 | component-selector, librarian |
| `control/` | 3 | (future: control-designer agent) |
| `emc/` | 2 | (future: emc-designer agent) |
| `gate-drive/` | 1 | (future: gate-drive agent) |
| `thermal/` | 1 | analyst (pipeline/analyst.py) |
| `protection/` | 1 | (future: protection agent) |
| `reliability/` | 1 | (future: reliability agent) |
| `pcb-layout/` | 1 | (future: layout agent) |

## Usage

Agent prompts reference knowledge files via relative path. Example
from `topology-selector.md`:

```
See knowledge/topologies/topology-selection-guide.md for the full
decision tree.
```

The orchestrator injects referenced knowledge files into the agent's
context window at invocation time.

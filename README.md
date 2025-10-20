## Inspiration

LayerLite began as a late-night rescue mission while shipping a Nemo inference workflow. A rogue dependency on `mlx-types` hijacked the build, pulling in hundreds of megabytes we did not need and breaking the deployment. We traced the issue to a single helper the script touched, and the experience convinced us that the right tool should understand our code well enough to keep only what matters. LayerLite is that tool—born from the frustration of dependency conflicts and the desire for fast, reliable shipping.

## What it does

LayerLite takes a user's Python entry point and produces a minimal, production-ready environment. The main agent provisions an isolated sandbox, installs just the inferred requirements, and executes the script to capture runtime hints. A static analyzer then maps the recursive import graph, tagging every file that is actually used—including compiled extensions and data files. Unused files are stripped, `__init__` modules are patched so they continue to resolve, and the result is a slim package plus artifacts such as a dependency graph, a pruned `requirements.txt`, and the execution logs.

## How we built it

We orchestrate the workflow with Bedrock AgentCore and Strands, mirroring the architecture in `archi_0_aws.png`. The main agent handles requirement discovery, environment creation, and user interactions. The core optimization loop leverages Jedi, custom AST passes, and our own library of site-package mutators to traverse large dependency trees safely. A cleanup agent repeatedly runs the user's script, analyzes errors, and edits library files until the reduced environment behaves exactly like the original. Everything runs inside disposable Python environments whose site-packages are writable, enabling automated refactors without touching the user's machine.

![Architecture](https://raw.githubusercontent.com/QuentinFuxa/LayerLite/refs/heads/main/architecture_aws.png)


## Challenges we ran into

- Balancing static analysis (fast, comprehensive) with dynamic tracing for libraries that hide behavior behind `__all__`, lazy loaders, or compiled modules.
- Keeping `__init__` files coherent after pruning; a single commented import can cascade into runtime failures.
- Managing massive dependency graphs (think `scipy` and `pandas`) without exhausting memory—hence the tree representation and targeted cleanup.
- Coordinating multiple agents when the underlying environment is not yet executable, especially before Code Interpreter can be configured.

## Accomplishments that we're proud of

Optimizing a solar irradiance calculator for Paris showcased the impact: the original 195 MB environment—driven largely by `scipy`, `pvlib`, and `pandas`—shrank to 109 MB. The agent produced a visual map of 83 recursive imports, highlighted dead branches, and delivered a reproducible bundle that cold-started twice as fast. That same pipeline now powers smaller Lambda artifacts and cheaper batch jobs.

## What we learned

We learned that pairing human intuition (“why is this dependency here?”) with an agentic cleanup loop is far more effective than either alone. Rich visualizations of the import tree keep stakeholders confident while the system edits their libraries. Most importantly, investing in tooling that edits site-packages safely transforms the risky parts of dependency surgery into routine automation.

## What's next for LayerLite

Future improvements:
- Function-level slicing to keep only the symbols a script truly touches, not just the files.
- Automatic conflict resolution when a new library breaks an existing environment, reusing the same graph insights to suggest fixes.
- Broader language support and deeper heuristics for non-Python assets, reducing the need for manual agent interventions.

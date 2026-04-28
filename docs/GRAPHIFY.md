# Graphify Integration

CineMate ships with first-class support for the open‑source [Graphify](https://graphify.net) knowledge‑graph skill that turns a codebase into a queryable graph for AI coding assistants.

Graphify is **not** part of the CineMate runtime – it is a **developer tool** that you run locally to understand and navigate the project architecture. The bot continues to run without Graphify; when present, Graphify simply adds
a persistent graph view of this repository.

## Prerequisites

Install the Python implementation of Graphify (CLI) in your local environment:

```bash
pip install graphifyy
```

Or install the Rust CLI if you prefer the Rust toolchain:

```bash
cargo install graphify-rs
```

Both CLIs generate a compatible `graphify-out/` directory in the project root with:

- `graph.json` – machine‑readable knowledge graph
- `graph.html` – interactive visualization in the browser
- `GRAPH_REPORT.md` – auto‑generated architecture report
- `wiki/` / `obsidian/` – optional wiki and Obsidian vault exports

See the upstream docs for full details:

- Python skill + CLI: https://github.com/safishamsi/graphify
- Rust CLI: https://docs.rs/crate/graphify-rs/latest

## One‑time setup for this repo

From the project root (where `main.py` and `pyproject.toml` live), run:

```bash
# Build an initial graph from the CineMate repo
graphify . --wiki

# Or, using the Rust CLI
graphify-rs build --path . --format json,html
```

This will create a `graphify-out/` folder alongside the source tree. You can open `graphify-out/graph.html` in a browser to explore the codebase, or ask your AI coding assistant to
use the generated `GRAPH_REPORT.md` when answering architecture questions.

The `.agent/rules/graphify.md` file in this repository tells compatible assistants (Claude Code, Copilot Chat skills, etc.) to prefer Graphify outputs over raw file reads when available.

## Keeping the graph in sync

After you modify CineMate locally, refresh the graph so it stays aligned with the code:

```bash
# Fast AST‑only refresh (no additional LLM cost)
graphify update .

# Or, if you are using the Rust CLI
graphify-rs build --path . --format json,html
```

You can also wire Graphify into your local Git hooks so the graph is rebuilt automatically after commits. See the upstream Graphify docs for the recommended `pre-commit` / `post-commit` hooks.

## CI / safety notes

- Graphify is **not** installed as a dependency in `pyproject.toml` – this avoids adding a heavy dev‑tool dependency to the production runtime.
- All Graphify artifacts live under `graphify-out/`, which can be safely ignored in Docker images and deployment manifests.
- The FastAPI app, RQ workers, and Telegram webhook flow do **not** import or depend on Graphify in any way, so there is no impact on production behaviour.

## Suggested assistant workflow

If your coding assistant supports Graphify as a skill:

1. Run `graphify .` in the project root to build the graph.
2. Ask your assistant to **read `graphify-out/GRAPH_REPORT.md`** before answering questions about CineMate architecture or data flows.
3. For deep dives, use Graphify queries such as:

   ```bash
   graphify query "show the Telegram webhook → worker → provider flow in CineMate"
   graphify path "telegram_webhook" "LoggingService" --graph graphify-out/graph.json
   ```

This keeps your assistant grounded in the real structure of the CineMate codebase and reduces the chance of hallucinated file names or flows.

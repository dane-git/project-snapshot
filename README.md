# Project Snapshot CLI
LLMs have limited context windows, and popular implementations can get bogged down once a single thread grows too long. A common workaround is to start a fresh chat, but that often means losing valuable context the model had already built up.

**Project Snapshot** is a simple script that generates a single **Markdown “project snapshot”** to serve as a source of truth for an LLM. It produces an ASCII directory tree and then inlines selected files with code fences, applying filters and size limits to keep the output manageable. You can paste the snapshot into a chat thread or store it in thread files to provide consistent context to the LLM. This approach works best for small projects, quick prototyping, or proof-of-concept work.

## Features
- **Config + CLI**: TOML / JSON / INI configs, with CLI overrides.
- **Targeted capture**: include by extension; include/exclude **globs**; exclude dirs/files; optional `.gitignore` respect.
- **Safety**: per-file `max_bytes` with **head/tail** context and a visible `... [truncated] ...` marker.
- **Binary guard**: skips obvious binaries/unreadables (heuristic).
- **Deterministic**: sorted output; ASCII tree (`|--`, ``--`).

## Requirements
- **Python 3.11+** (preferred) — uses built-in `tomllib` for TOML configs.
- **Python 3.8–3.10** — either use JSON/INI configs, or install `tomli` to use TOML:
  ```bash
  pip install -r requirements.txt

## Install

```bash
# (recommended) create and activate a venv first
python -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt

```

## Quickstart

### 1 With a TOML config

Create `snapshot.config.toml`:

```toml
root = "./vaultd/app"
out  = "vaultd_snapshot.md"

include_exts = [".py", ".json", ".test", ".sh", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".html", ""]
exclude_dirs = ["__pycache__", ".git", ".venv", "venv", ".idea", ".mypy_cache", "data", "bu", "bootstrap", "out", "config", ".ipynb_checkpoints", "assets", ".pytest_cache"]
exclude_files = ["scratch.json", "scratch.py"]

# Optional behaviors
respect_gitignore = true  # honor patterns in .gitignore
max_bytes  = 300000       # per-file cap; 0 = unlimited
head_lines = 200          # keep first lines when truncated
tail_lines = 80           # keep last lines when truncated
show_stats = true         # include a summary section

```

Run:
```bash
python project_snapshot_cli.py --config snapshot.config.toml
```
With debug and label:
```bash
python project_snapshot_cli.py --config snapshot.config.toml --label projectName --debug
```

### 2 CLI only

```python
python project_snapshot_cli.py \
  --root vaultd/app \
  --out vaultd_snapshot.md \
  --include-ext .py .json .sh "" \
  --exclude-dir .git __pycache__ .venv \
  --respect-gitignore \
  --max-bytes 300000 --head-lines 200 --tail-lines 80

```

## Config vs CLI precedence

- The tool **merges** config + CLI; **CLI wins only if you pass a flag**.
- Internally, CLI arguments default to **`None`** so they don’t clobber config values inadvertently.
- After merging, any remaining `None` gets a sane default (e.g., `respect_gitignore=false`, `max_bytes=0`, etc.).
    

## Output
- Markdown file with:
    1.  **Directory Tree** (ASCII), excluding configured dirs and dot-dirs.
    2.  **Per-file sections** with fenced code blocks, language inferred by suffix.
    3.  **Stats** (optional): files included, truncated, skipped.
        
Example tree:

```bash
app
|-- scripts
|   |-- reset_and_bootstrap.sh
|   `-- test_run.py
|-- core
|   `-- crypto_v1.py
`-- vaultd.py

```

## Options Reference (CLI)

- `--root PATH` — root directory to scan (overrides config if provided).
- `--out FILE.md` — output markdown path (default `snapshot-YYYYmmdd-HHMMSS.md`).
- `--config FILE.(toml|json|ini|cfg)` — load options from file.
- `--include-ext ...` — list of extensions to include (`.py .json ""`).
- `--exclude-dir ...` — directory names to exclude (`.git __pycache__ .venv`).
- `--exclude-file ...` — file names to exclude.
- `--include-glob ...` — only include files matching glob(s).
- `--exclude-glob ...` — exclude files matching glob(s).
- `--respect-gitignore` — honor patterns in `.gitignore`.
- `--max-bytes N` — per-file byte cap (0 = unlimited).
- `--head-lines N`, `--tail-lines N` — how much context to keep if truncated.
- `--no-stats` — hide the final stats section.
    

## Tips
- Use **absolute paths** in `root` if running the script from different working dirs.
- If your Python < 3.11 and you need TOML, install **tomli** (see requirements.txt).
- For large repos, tune `max_bytes`, `head_lines`, and `tail_lines` to control size.
- Use `--include-glob`/`--exclude-glob` for precision (supports `**`).
    

## Troubleshooting

- **Config not applied**: ensure you passed `--config` and that your file extension matches the parser (`.toml`, `.json`, `.ini`, `.cfg`). On Python < 3.11, install `tomli` to use TOML.
- **Wrong root scanned**: confirm whether `root` in config is **relative to the current working directory**; use absolute paths if needed.
- **Tree shows excluded dirs**: verify names in `exclude_dirs` match directory basenames (not paths); for path patterns, use `--exclude-glob`.
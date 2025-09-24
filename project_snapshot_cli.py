#!/usr/bin/env python3
"""
project_snapshot_cli.py — generate a single Markdown "project snapshot" for LLMs

Features
- CLI flags AND optional config file (TOML/JSON/INI)
- Include/Exclude by file extensions, glob patterns, directories, filenames
- ASCII-only directory tree (no box-drawing chars)
- Size guards: max bytes per file; head/tail capture to keep context while truncating
- Skips obvious binaries; robust UTF-8 read with fallback
- Deterministic ordering
- Minimal dependencies (stdlib only; uses tomllib if available for TOML)

Example config (TOML):

    # snapshot.config.toml
    root = "./project/app"
    out  = "project_snapshot.md"

    include_exts = [".py", ".json", ".test", ".sh", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".html", ""]
    exclude_dirs = ["__pycache__", ".git", ".venv", "venv", ".idea", ".mypy_cache", "data", "bu", "bootstrap", "out", "config", ".ipynb_checkpoints", "assets", ".pytest_cache"]
    exclude_files = ["scratch.json", "scratch.py"]

    include_globs = []          # e.g., ["src/**", "app/**/*.py"]
    exclude_globs = []          # e.g., ["**/migrations/**", "**/*_gen.py"]

    respect_gitignore = true    # best-effort via fnmatch on .gitignore patterns

    max_bytes = 300000          # per file; 0 = no cap
    head_lines = 200            # if truncating, capture first N lines
    tail_lines = 80             # and last N lines
    show_stats = true

Usage
    python project_snapshot_cli.py --config snapshot.config.toml
    python project_snapshot_cli.py --root . --out snapshot.md --include-ext .py .json --exclude-dir .git __pycache__

Notes
- ASCII-only output by design (no fancy characters).
- If both CLI and config are supplied, CLI wins per-option.
"""
from __future__ import annotations

import argparse
import configparser
import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Iterable, List, Dict, Any, Tuple, Optional
from datetime import datetime

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    tomllib = None  # TOML parsing disabled if not available

# ----------------------------- Defaults ------------------------------------
DEFAULT_INCLUDE_EXTS = [
    ".py",
    ".json",
    ".test",
    ".sh",
    ".toml",
    ".yml",
    ".yaml",
    ".cfg",
    ".ini",
    ".html",
    "",  # files with no extension
]
DEFAULT_EXCLUDE_DIRS = [
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".idea",
    ".mypy_cache",
    "data",
    "bu",
    "bootstrap",
    "out",
    "config",
    ".ipynb_checkpoints",
    "assets",
    ".pytest_cache",
]
DEFAULT_EXCLUDE_FILES = ["scratch.json", "scratch.py"]

# ----------------------------- Utilities -----------------------------------

def load_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    suffix = cfg_path.suffix.lower()
    if suffix in (".toml", ".tml") and tomllib is not None:
        with cfg_path.open("rb") as f:
            return tomllib.load(f)
    elif suffix == ".json":
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    elif suffix in (".ini", ".cfg"):
        cp = configparser.ConfigParser()
        cp.read(cfg_path)
        # Flatten the DEFAULT and [snapshot] sections
        merged: Dict[str, Any] = dict(cp.defaults())
        if cp.has_section("snapshot"):
            merged.update(dict(cp.items("snapshot")))
        # Coerce common list-like options (comma/space separated)
        for key in [
            "include_exts",
            "exclude_dirs",
            "exclude_files",
            "include_globs",
            "exclude_globs",
        ]:
            if key in merged and isinstance(merged[key], str):
                merged[key] = _split_list(merged[key])
        for key in ["max_bytes", "head_lines", "tail_lines"]:
            if key in merged and isinstance(merged[key], str) and merged[key].strip().isdigit():
                merged[key] = int(merged[key])
        for key in ["respect_gitignore", "show_stats"]:
            if key in merged and isinstance(merged[key], str):
                merged[key] = merged[key].strip().lower() in {"1", "true", "yes", "on"}
        return merged
    else:
        # Try naive JSON as a fallback
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError(f"Unsupported config format or missing tomllib: {cfg_path}")

def _split_list(s: str) -> List[str]:
    # split by comma or whitespace
    parts = re.split(r"[\s,]+", s.strip())
    return [p for p in parts if p]


def merge_options(cli, cfg):
    out = dict(cfg)
    for k, v in cli.items():
        if v is not None:      
            out[k] = v
    return out


def expand_path(path_str: str) -> Path:
    s = os.path.expanduser(os.path.expandvars(path_str or ".")).strip()
    # Safety: if the string *should* be absolute but lost its leading slash,
    # fix accidental "//cwd/home/..." cases by detecting '/home/...'.
    if not s.startswith("/") and s.startswith("home/"):
        s = "/" + s
    return Path(s).resolve()

def looks_binary(sample: bytes) -> bool:
    # Heuristic: if many NULs or high-bit bytes, treat as binary
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nontext_ratio = sum(ch > 0x7F for ch in sample) / len(sample)
    return nontext_ratio > 0.30


def read_text_safely(path: Path, max_bytes: int) -> Tuple[str, bool, bool]:
    """Return (text, truncated, binary_skipped)."""
    try:
        if max_bytes and path.stat().st_size > max_bytes:
            with path.open("rb") as f:
                data = f.read(max_bytes + 1)
            if looks_binary(data):
                return ("", False, True)
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = data.decode("utf-8", errors="replace")
            return (text, True, False)
        else:
            with path.open("rb") as f:
                data = f.read()
            if looks_binary(data):
                return ("", False, True)
            text = data.decode("utf-8", errors="replace")
            return (text, False, False)
    except Exception:
        return ("", False, True)


def apply_gitignore_patterns(root: Path) -> List[str]:
    patterns: List[str] = []
    gi = root / ".gitignore"
    if gi.exists():
        for line in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


def matches_any_glob(relpath: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, pat) for pat in globs)

def dir_tree_ascii(root: Path, include_pred) -> str:
    lines: List[str] = []
    root = root.resolve()

    def iter_entries(path: Path) -> List[Path]:
        items: List[Path] = []
        for p in path.iterdir():
            if p.is_dir():
                if include_pred(p):
                    items.append(p)
            else:
                if include_pred(p):
                    items.append(p)
        return sorted(items, key=lambda q: (not q.is_dir(), q.name.lower()))

    def walk(path: Path, prefix: str = "") -> None:
        entries = iter_entries(path)
        for i, entry in enumerate(entries):
            connector = "`-- " if i == len(entries) - 1 else "|-- "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "|   "
                walk(entry, prefix + extension)

    lines.append(root.name)
    walk(root)
    return "\n".join(lines)

# ------------------------------ Core ---------------------------------------

def build_snapshot(opts: Dict[str, Any]) -> str:
    # root = Path(opts.get("root", ".")).resolve()
    root = opts.get("root")  # already a Path from main()
    if not isinstance(root, Path):
        root = expand_path(str(root))
    include_exts = set(x.lower() for x in opts.get("include_exts", DEFAULT_INCLUDE_EXTS))
    exclude_dirs = set(opts.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS))
    exclude_files = set(opts.get("exclude_files", DEFAULT_EXCLUDE_FILES))
    include_globs = list(opts.get("include_globs", []))
    exclude_globs = list(opts.get("exclude_globs", []))
    respect_gitignore = bool(opts.get("respect_gitignore", False))

    max_bytes = int(opts.get("max_bytes", 0))
    head_lines = int(opts.get("head_lines", 200))
    tail_lines = int(opts.get("tail_lines", 80))
    show_stats = bool(opts.get("show_stats", True))

    gitignore_pats = apply_gitignore_patterns(root) if respect_gitignore else []

    def include_pred(p: Path) -> bool:
        if p.is_dir():
            return p.name not in exclude_dirs and not p.name.startswith(".")
        rel = str(p.relative_to(root))
        if exclude_globs and matches_any_glob(rel, exclude_globs):
            return False
        if gitignore_pats and matches_any_glob(rel, gitignore_pats):
            return False
        if include_globs and not matches_any_glob(rel, include_globs):
            return False
        if p.name in exclude_files:
            return False
        return p.suffix.lower() in include_exts

    # Directory tree
    tree_txt = dir_tree_ascii(root, include_pred)

    # File collection
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune dirs
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith(".")]
        here = Path(dirpath)
        for fname in filenames:
            p = here / fname
            if include_pred(p):
                files.append(p)
    files = sorted(set(files), key=lambda p: str(p).lower())

    # Build markdown
    out: List[str] = []
    out.append("# Project Snapshot\n")
    out.append("## Directory Tree\n")
    out.append("```text\n")
    out.append(tree_txt)
    out.append("\n```\n\n")

    total_files = 0
    truncated_files = 0
    skipped_binaries = 0

    for path in files:
        total_files += 1
        rel = path.relative_to(root)
        code_lang = _lang_from_suffix(path.suffix.lower())

        text, truncated, bin_skip = read_text_safely(path, max_bytes)
        if bin_skip and not text:
            skipped_binaries += 1
            continue

        # If truncated by bytes, tighten to head/tail lines while noting truncation
        if truncated and (head_lines > 0 or tail_lines > 0):
            lines = text.splitlines()
            head = lines[: head_lines] if head_lines > 0 else []
            tail = lines[-tail_lines :] if tail_lines > 0 else []
            text = "\n".join(head + ["", "... [truncated] ...", ""] + tail)

        if truncated:
            truncated_files += 1

        # Trim surrounding whitespace for markdown neatness
        text = text.strip("\n")

        section = f"## {rel}\n```{code_lang}\n{text}\n```\n\n"
        out.append(section)

    if show_stats:
        out.append("---\n")
        out.append("## Snapshot Stats\n")
        out.append(f"- files_included: {total_files}\n")
        if max_bytes:
            out.append(f"- files_truncated_by_bytes: {truncated_files}\n")
        out.append(f"- files_skipped_as_binary_or_unreadable: {skipped_binaries}\n")

    return "".join(out)


def _lang_from_suffix(suffix: str) -> str:
    return {
        ".test": "test",
        ".py": "python",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".txt": "text",
        ".cfg": "ini",
        ".ini": "ini",
        ".html": "html",
    }.get(suffix, "")


# ------------------------------ CLI ----------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    p = argparse.ArgumentParser(description="Generate a Markdown project snapshot for LLMs")
    p.add_argument("--root", default=None, help="Root directory to scan")
    p.add_argument("--out", default=None, help="Output markdown file ...")

    # Let config control unless explicitly set on CLI:
    p.add_argument("--respect-gitignore", action="store_true", default=None, help="...")
    p.add_argument("--no-stats", dest="show_stats", action="store_false", default=None, help="...")

    # (keep max-bytes/head-lines/tail-lines defaults as None too)
    p.add_argument("--config", help="Path to config file (TOML/JSON/INI)")

    p.add_argument("--include-ext", nargs="*", default=None, help="Whitelist of file extensions (e.g., .py .json '')")
    p.add_argument("--exclude-dir", nargs="*", default=None, help="Directories to exclude by name")
    p.add_argument("--exclude-file", nargs="*", default=None, help="Specific filenames to exclude")

    p.add_argument("--include-glob", nargs="*", default=None, help="Glob(s) that a file's relative path must match")
    p.add_argument("--exclude-glob", nargs="*", default=None, help="Glob(s) that, if matched, exclude a file")


    p.add_argument("--max-bytes", type=int, default=None, help="Max bytes to read per file; 0 = unlimited")
    p.add_argument("--head-lines", type=int, default=None, help="If truncated, include first N lines")
    p.add_argument("--tail-lines", type=int, default=None, help="If truncated, include last N lines")
    
    p.add_argument("--debug", action="store_true", help="Print resolved options")

    p.add_argument("--label", default=None, help="Label used in output filename template")
    p.add_argument("--out-template", default=None,
               help="Output filename template, supports {label}, {date}, {time}")

    args = vars(p.parse_args(argv))

    # Normalize list-ish args (None means not provided)
    if args["include_ext"] is not None:
        args["include_exts"] = args.pop("include_ext")
    if args["exclude_dir"] is not None:
        args["exclude_dirs"] = args.pop("exclude_dir")
    if args["exclude_file"] is not None:
        args["exclude_files"] = args.pop("exclude_file")
    if args["include_glob"] is not None:
        args["include_globs"] = args.pop("include_glob")
    if args["exclude_glob"] is not None:
        args["exclude_globs"] = args.pop("exclude_glob")

    return args


def main(argv: Optional[List[str]] = None) -> int:
    cli = parse_args(argv)
    cfg = load_config(cli.get("config")) if cli.get("config") else {}
    opts = merge_options(cli, cfg)
    raw_root = opts.get("root", ".")
    if isinstance(raw_root, (str, os.PathLike)):
        opts["root"] = expand_path(str(raw_root))
    else:
        opts["root"] = Path(".").resolve()
        
    # Fill defaults where needed
    opts.setdefault("include_exts", DEFAULT_INCLUDE_EXTS)
    opts.setdefault("exclude_dirs", DEFAULT_EXCLUDE_DIRS)
    opts.setdefault("exclude_files", DEFAULT_EXCLUDE_FILES)
    opts.setdefault("include_globs", [])
    opts.setdefault("exclude_globs", [])
    if opts.get("respect_gitignore") is None: opts["respect_gitignore"] = False
    if opts.get("max_bytes") is None:        opts["max_bytes"] = 0
    if opts.get("head_lines") is None:       opts["head_lines"] = 200
    if opts.get("tail_lines") is None:       opts["tail_lines"] = 80
    if opts.get("show_stats") is None:       opts["show_stats"] = True
    
    if opts.get("debug"):
        print("DEBUG raw root:", repr(raw_root))
        print("DEBUG resolved root:", opts["root"])

    # Handle output filename
    label = opts.get("label") or "snapshot"
    template = opts.get("out_template")
    out_path = opts.get("out")

    if not out_path:
        if not template:
            template = "snapshots/{label}_{date}_{time}.md"
        now = datetime.now()
        out_path = template.format(
            label=label,
            date=now.strftime("%Y%m%d"),
            time=now.strftime("%H%M%S"),
        )

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)  # ensure snapshots/ exists

    # Build and write snapshot
    md = build_snapshot(opts)
    out_file.write_text(md, encoding="utf-8")
    print(f"Project snapshot saved to: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

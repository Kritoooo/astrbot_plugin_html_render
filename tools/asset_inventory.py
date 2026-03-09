#!/usr/bin/env python3
"""Generate a lightweight inventory of render assets for astrbot_plugin_html_render.

This is a compatibility-safe utility: it does not modify plugin runtime behavior.
It helps users discover which template/background assets exist in a checkout so
future config additions can refer to real files instead of guesswork.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
TEMPLATE_SUFFIXES = {".html", ".htm", ".css", ".jinja", ".j2"}
SKIP_DIRS = {".git", ".openclaw_backups", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def classify(path: Path) -> str | None:
    suffix = path.suffix.lower()
    lowered = "/".join(path.parts).lower()
    if suffix in IMAGE_SUFFIXES and any(key in lowered for key in ("background", "bg", "image", "images", "assets")):
        return "backgrounds"
    if suffix in TEMPLATE_SUFFIXES and any(key in lowered for key in ("template", "templates", "layout", "layouts", "theme", "themes")):
        return "templates"
    if suffix in IMAGE_SUFFIXES:
        return "images"
    if suffix in TEMPLATE_SUFFIXES:
        return "markup"
    return None


def build_inventory(root: Path) -> dict:
    inventory = {
        "root": str(root),
        "templates": [],
        "backgrounds": [],
        "images": [],
        "markup": [],
    }
    for file_path in sorted(iter_files(root)):
        category = classify(file_path.relative_to(root))
        if category:
            inventory[category].append(str(file_path.relative_to(root)).replace("\\", "/"))
    inventory["counts"] = {k: len(v) for k, v in inventory.items() if isinstance(v, list)}
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory template/background-like assets in this plugin checkout")
    parser.add_argument("root", nargs="?", default=".", help="Plugin root (defaults to current directory)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--write", help="Optional output path to write JSON inventory")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    inventory = build_inventory(root)
    text = json.dumps(inventory, ensure_ascii=False, indent=2 if args.pretty else None)

    if args.write:
        out_path = Path(args.write)
        if not out_path.is_absolute():
            out_path = root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

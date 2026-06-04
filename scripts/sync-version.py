#!/usr/bin/env python3
"""Sync name/version/description from .project-info.json into pyproject.toml and web/package.json."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
INFO = ROOT / ".project-info.json"


def load_info() -> dict:
    return json.loads(INFO.read_text())


def sync_pyproject(info: dict) -> None:
    path = ROOT / "server" / "pyproject.toml"
    text = path.read_text()
    text = re.sub(r'^(name\s*=\s*)"[^"]+"', f'\\1"{info["name"]}"', text, flags=re.MULTILINE)
    text = re.sub(r'^(version\s*=\s*)"[^"]+"', f'\\1"{info["version"]}"', text, flags=re.MULTILINE)
    text = re.sub(r'^(description\s*=\s*)"[^"]+"', f'\\1"{info["description"]}"', text, flags=re.MULTILINE)
    path.write_text(text)
    print(f"  updated {path.relative_to(ROOT)}")


def sync_package_json(info: dict) -> None:
    path = ROOT / "web" / "package.json"
    if not path.exists():
        print(f"  skipping {path.relative_to(ROOT)} (not found)")
        return
    pkg = json.loads(path.read_text())
    pkg["name"] = info["name"]
    pkg["version"] = info["version"]
    pkg["description"] = info["description"]
    path.write_text(json.dumps(pkg, indent=2) + "\n")
    print(f"  updated {path.relative_to(ROOT)}")


if __name__ == "__main__":
    info = load_info()
    print(f"syncing v{info['version']} ...")
    sync_pyproject(info)
    sync_package_json(info)
    print("done")

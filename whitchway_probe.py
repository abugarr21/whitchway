#!/usr/bin/env python3
"""
whitchway_probe.py (agnostic runtime probe)
Creates: whitchway_probe.jsonl in the current directory.

Goal:
- No modification to the target project required.
- You *optionally* provide an app spec (FastAPI) to capture mounted routes.
- Always captures runtime-imported modules/files (sys.modules).
- Collects raw systemd runtime surfaces (failed + loaded units).
This tool captures best-effort, bounded system and application
truth surfaces without modifying the target environment.
Typical:
  python3 whitchway_probe.py --root /path/to/project --app mypkg.main:app
"""

import argparse
import importlib
import json
import os
import sys
import time
import subprocess
from pathlib import Path


def load_app(app_spec: str):
    """
    app_spec: "module.path:attr"
    """
    mod_name, attr = app_spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def run_cmd(argv, timeout_s=4):
    """
    Best-effort command runner.
    - Never raises
    - Never mutates
    - Preserves stdout/stderr/rc/timeout
    """
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "argv": argv,
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "timeout": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "argv": argv,
            "rc": None,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "timeout": True,
        }
    except Exception as e:
        return {
            "argv": argv,
            "rc": None,
            "stdout": "",
            "stderr": repr(e),
            "timeout": False,
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Whitchway agnostic runtime probe")
    ap.add_argument("--root", default=".", help="Project root used to relativize file paths")
    ap.add_argument("--app", default="", help="Optional FastAPI app spec, e.g. 'hbai.main:app'")
    ap.add_argument("--out", default="whitchway_probe.jsonl", help="Output JSONL filename")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outp = Path(args.out)

    # Ensure the target root is importable (best-effort)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    app = None
    app_err = None
    if args.app:
        try:
            app = load_app(args.app)
        except Exception as e:
            app_err = repr(e)

    # ------------------------------------------------------------
    # Runtime-imported Python modules (files only)
    # ------------------------------------------------------------
    mods = []
    for name, m in sorted(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        mods.append({"module": name, "file": f})

    # ------------------------------------------------------------
    # Runtime-mounted FastAPI routes (optional)
    # ------------------------------------------------------------
    routes = []
    if app is not None and hasattr(app, "routes"):
        for r in getattr(app, "routes", []):
            endpoint = getattr(r, "endpoint", None)
            routes.append({
                "path": getattr(r, "path", None),
                "methods": sorted(getattr(r, "methods", []) or []),
                "endpoint": getattr(endpoint, "__qualname__", None) if endpoint else None,
                "module": getattr(endpoint, "__module__", None) if endpoint else None,
            })

    # ------------------------------------------------------------
    # Systemd runtime surfaces (raw, unparsed)
    # ------------------------------------------------------------
    systemd = {
        "calls": [
            run_cmd(["systemctl", "--no-pager", "--plain", "--failed"]),
            run_cmd(["systemctl", "--no-pager", "--plain", "list-units", "--all"]),
        ]
    }

    # ------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------
    meta = {
        "kind": "probe_meta",
        "ts": time.time(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "root": str(root),
        "app_spec": args.app or None,
        "app_import_error": app_err,
    }

    # ------------------------------------------------------------
    # Emit JSONL (append-only, deterministic order)
    # ------------------------------------------------------------
    with outp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        f.write(json.dumps({"kind": "probe_modules", "items": mods}) + "\n")
        f.write(json.dumps({"kind": "probe_routes", "items": routes}) + "\n")
        f.write(json.dumps({"kind": "probe_systemd", "items": systemd}) + "\n")

    print(f"[ok] wrote {outp} (modules={len(mods)}, routes={len(routes)})")
    if app_err:
        print(f"[note] app import failed: {app_err} (modules still captured)")


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""
whitchway_probe.py (agnostic runtime probe)
Creates: whitchway_probe.jsonl in the current directory.

Goal:
- No modification to the target project required.
- Optionally provide an app spec (FastAPI) to capture mounted routes.
- Always captures runtime-imported modules/files (sys.modules).
- Captures raw system/worksite truth surfaces relevant to Project Sentinel:
  (1) RF device/link truth
  (2) Manager conflict truth
  (3) WireGuard truth (if present)
  (4) Systemd timers truth
  (5) Kernel/driver truth (bounded)
  (6) Hardware inventory truth
  (7) Resource headroom truth

Design:
- Observe-only
- Best-effort (never raises)
- Bounded output (explicit truncation)
- Deterministic command ordering
- No parsing / no interpretation

Typical:
  python3 whitchway_probe.py --root /opt/hbai --app hbai.app.main:app
"""

import argparse
import importlib
import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_app(app_spec: str):
    """
    app_spec: "module.path:attr"
    """
    mod_name, attr = app_spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def _truncate_text(s: str, max_chars: int) -> Dict[str, Any]:
    """
    Return {text, truncated, original_len} with explicit truncation.
    """
    if s is None:
        s = ""
    if max_chars <= 0:
        return {"text": "", "truncated": bool(s), "original_len": len(s)}
    if len(s) <= max_chars:
        return {"text": s, "truncated": False, "original_len": len(s)}
    cut = s[:max_chars]
    marker = f"\n[TRUNCATED] original_len={len(s)} max_chars={max_chars}\n"
    return {"text": cut + marker, "truncated": True, "original_len": len(s)}


def run_cmd(argv: List[str], timeout_s: float = 4.0, max_stdout_chars: int = 200_000, max_stderr_chars: int = 50_000):
    """
    Best-effort command runner.
    - Never raises
    - Never mutates
    - Preserves stdout/stderr/rc/timeout
    - Explicitly truncates large outputs
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
        out = _truncate_text(p.stdout or "", max_stdout_chars)
        err = _truncate_text(p.stderr or "", max_stderr_chars)
        return {
            "argv": argv,
            "rc": p.returncode,
            "timeout": False,
            "stdout": out["text"],
            "stderr": err["text"],
            "stdout_truncated": out["truncated"],
            "stderr_truncated": err["truncated"],
            "stdout_len": out["original_len"],
            "stderr_len": err["original_len"],
        }
    except subprocess.TimeoutExpired as e:
        out = _truncate_text((e.stdout or ""), max_stdout_chars)
        err = _truncate_text((e.stderr or ""), max_stderr_chars)
        return {
            "argv": argv,
            "rc": None,
            "timeout": True,
            "stdout": out["text"],
            "stderr": err["text"],
            "stdout_truncated": out["truncated"],
            "stderr_truncated": err["truncated"],
            "stdout_len": out["original_len"],
            "stderr_len": err["original_len"],
        }
    except Exception as e:
        return {
            "argv": argv,
            "rc": None,
            "timeout": False,
            "stdout": "",
            "stderr": repr(e),
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_len": 0,
            "stderr_len": len(repr(e)),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Whitchway agnostic runtime probe")
    ap.add_argument("--root", default=".", help="Project root used to relativize file paths")
    ap.add_argument("--app", default="", help="Optional FastAPI app spec, e.g. 'hbai.app.main:app'")
    ap.add_argument("--out", default="whitchway_probe.jsonl", help="Output JSONL filename")
    ap.add_argument("--timeout", type=float, default=4.0, help="Per-command timeout seconds (default: 4.0)")
    ap.add_argument("--max-stdout", type=int, default=200_000, help="Max stdout chars per command (default: 200000)")
    ap.add_argument("--max-stderr", type=int, default=50_000, help="Max stderr chars per command (default: 50000)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outp = Path(args.out)

    timeout_s = float(args.timeout)
    max_out = int(args.max_stdout)
    max_err = int(args.max_stderr)

    # Ensure the target root is importable (best-effort)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # Optional: app import
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
    # System/worksite surfaces (raw, unparsed)
    # ------------------------------------------------------------
    calls: List[Dict[str, Any]] = []

    # (A) systemd health + loaded units
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "--failed"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "list-units", "--all"], timeout_s, max_out, max_err))

    # (B) systemd timers (silent automation detector)
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "list-timers", "--all"], timeout_s, max_out, max_err))

    # (C) RF device/link truth
    calls.append(run_cmd(["ip", "-br", "link", "show"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["ip", "-br", "addr", "show"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["ip", "route", "show"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["iw", "dev"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["iw", "phy"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["rfkill", "list"], timeout_s, max_out, max_err))

    # (D) Manager conflict truth
    calls.append(run_cmd(["nmcli", "-t", "dev", "status"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "status", "NetworkManager.service"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "status", "wpa_supplicant.service"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["systemctl", "--no-pager", "--plain", "status", "iwd.service"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["pgrep", "-a", "-f", "NetworkManager"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["pgrep", "-a", "-f", "wpa_supplicant"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["pgrep", "-a", "-f", "iwd"], timeout_s, max_out, max_err))

    # (E) WireGuard overlay truth (if installed)
    calls.append(run_cmd(["wg", "show"], timeout_s, max_out, max_err))

    # (F) Kernel/driver truth (bounded by max_stdout)
    calls.append(run_cmd(["uname", "-a"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["lsmod"], timeout_s, max_out, max_err))
    # journalctl -k is typically more reliable than dmesg permissions-wise
    calls.append(run_cmd(["journalctl", "-k", "-b", "--no-pager", "-n", "200"], timeout_s, max_out, max_err))

    # (G) Hardware inventory truth
    calls.append(run_cmd(["lspci", "-nn"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["lsusb"], timeout_s, max_out, max_err))

    # (H) Resource headroom truth
    calls.append(run_cmd(["uptime"], timeout_s, max_out, max_err))
    calls.append(run_cmd(["free", "-h"], timeout_s, max_out, max_err))

    probe_system = {
        "kind": "probe_system",
        "items": {
            "timeout_s": timeout_s,
            "max_stdout_chars": max_out,
            "max_stderr_chars": max_err,
            "calls": calls,
        },
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
    # Emit JSONL (deterministic order)
    # ------------------------------------------------------------
    with outp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        f.write(json.dumps({"kind": "probe_modules", "items": mods}) + "\n")
        f.write(json.dumps({"kind": "probe_routes", "items": routes}) + "\n")
        f.write(json.dumps(probe_system) + "\n")

    print(f"[ok] wrote {outp} (modules={len(mods)}, routes={len(routes)})")
    if app_err:
        print(f"[note] app import failed: {app_err} (modules still captured)")


if __name__ == "__main__":
    main()


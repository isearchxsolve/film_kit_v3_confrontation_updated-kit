#!/usr/bin/env python3
"""preflight.py -- FAIL FAST before any GPU billing starts. Zero-burn guardrail #1.

Run this the instant a paid instance boots, BEFORE render_film.py. If anything is
missing (models, workflows, deps, disk, GPU) it exits non-zero in ~5 seconds, so you
abort for ~0 rupees instead of discovering a broken env 20 minutes into a render.

    python3 src/preflight.py && python3 src/render_film.py --shutdown

The `&&` means: if preflight fails, render never runs -> no GPU time billed.
"""
import os, sys, json, shutil, subprocess

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "src"))
    errs, warns = [], []

    # 1) config loads
    try:
        from cfgutil import load_cfg
        cfg = load_cfg(root)
    except Exception as e:
        print(f"[FAIL] config load: {e}"); sys.exit(2)

    # 2) shots file parses
    shots = []
    try:
        shots = json.load(open(os.path.join(root, cfg["shots_file"])))["shots"]
        if not shots:
            errs.append(f"{cfg['shots_file']} has 0 shots")
    except Exception as e:
        errs.append(f"shots_file {cfg.get('shots_file')}: {e}")

    # 3) workflow graphs present
    for wf in ("s2v", "phantom", "flf2v"):
        if not os.path.exists(os.path.join(root, "workflows", f"{wf}.json")):
            errs.append(f"missing workflow graph: workflows/{wf}.json")

    # 4) model mount present + non-empty (the big one -- re-downloading on a paid box burns money)
    mdir = (cfg.get("paths", {}) or {}).get("models", "")
    if not (mdir and os.path.isdir(mdir) and os.listdir(mdir)):
        errs.append(f"model dir missing/empty: {mdir!r} -- stage models BEFORE renting the GPU")

    # 5) python deps importable
    for mod in ("cv2", "yaml", "numpy"):
        try:
            __import__(mod)
        except Exception:
            errs.append(f"python dep missing: {mod}")

    # 6) scratch disk headroom
    work = (cfg.get("paths", {}) or {}).get("work", "/tmp")
    os.makedirs(work, exist_ok=True)
    free_gb = shutil.disk_usage(work).free / 1e9
    if free_gb < 15:
        warns.append(f"low scratch disk on {work}: {free_gb:.1f} GB free (<15)")

    # 7) GPU visible -- FATAL on a paid GPU box, harmless on a CPU/assembly box.
    require_gpu = bool(cfg.get("rate_per_gpu_hr", 0)) or os.environ.get("KIT_REQUIRE_GPU") == "1"
    gpu_ok = False
    try:
        subprocess.run(["nvidia-smi"], check=True, capture_output=True); gpu_ok = True
    except Exception:
        pass
    if not gpu_ok:
        (errs if os.environ.get("KIT_REQUIRE_GPU") == "1" else warns).append(
            "nvidia-smi unavailable (OK on a CPU/assembly box; set KIT_REQUIRE_GPU=1 on a paid GPU box to make this fatal)")

    for w in warns:
        print(f"[warn] {w}")
    if errs:
        for e in errs:
            print(f"[FAIL] {e}")
        print(f"[preflight] {len(errs)} blocking issue(s) -- ABORT. Do NOT start rendering; fix these for free.")
        sys.exit(1)
    print(f"[preflight] OK -- {len(shots)} shots, profile={cfg.get('_active_profile')}, "
          f"gpu={'yes' if gpu_ok else 'no'}, {free_gb:.0f}GB free. Safe to render.")

if __name__ == "__main__":
    main()

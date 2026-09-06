#!/usr/bin/env python3
"""render_motion.py -- THE BLUEPRINT PATH (stills -> motion).

The artist (render_stills.py) has already drawn every page: one pixel-perfect
still per beat, composition locked, lighting locked, chiaroscuro locked, the
fear on her face locked, the greasepaint on his locked.  The blueprint does 99%
of the work.  This script lets the GPU do the 1%: turn each finished still into
live motion by feeding it as the ANCHOR FRAME into the Wan 2.2 image-to-video
workflows.

The GPU is no longer inventing from a text prompt -- it is ANIMATING a finished
image.  That is the 1% load.

Workflow selection (same as render_film.py, but the image input is the STILL):
  - talking beat  -> s2v  (still + voice audio -> lip-synced motion)
  - 2+ chars     -> phantom (still as subject anchor -> motion)
  - 1 char / 0   -> flf2v (still as first-frame -> motion; last-frame optional)

For FLF2V, if a NEXT beat's still exists, it is used as the end-frame so the
model knows where the motion should arrive.  If no next-frame exists, the same
still is used for both (subtle motion within the frame).

Prerequisite: render_stills.py must have run first (stills exist in <work>/<sid>/still.png).
If a still is missing, this script falls back to the ref image (old behavior).

Usage:
  KIT_CONFIG=config_confrontation.yaml python3 src/render_motion.py          # all beats
  KIT_CONFIG=config_confrontation.yaml python3 src/render_motion.py --only search_run
  KIT_CONFIG=config_confrontation.yaml python3 src/render_motion.py --smoke   # 1 beat, 1 take, low steps

Env:
  COMFY_MOCK=1  synthesize placeholder clips with ffmpeg (free offline dry-run)
  KIT_MOTION_DENOISE  denoise strength for the motion pass (default: 0.45 -- low
                      denoise = faithful to the blueprint still; 1.0 = full re-gen)
  KIT_MOTION_STEPS   override steps for the motion pass (default: cfg steps, but
                      you can lower to ~20 since the still already carries the detail)
"""
import os, sys, json, glob, argparse, subprocess


def load_cfg(root):
    sys.path.insert(0, os.path.join(root, "src"))
    from cfgutil import load_cfg as _lc
    return _lc(root)


def load_shots(root, cfg):
    return json.load(open(os.path.join(root, cfg["shots_file"])))


def done(work, sid):
    return os.path.exists(os.path.join(work, sid, "final.mp4"))


def still_path(work, sid):
    """Path to the blueprint still for a beat (if render_stills.py ran)."""
    p = os.path.join(work, sid, "still.png")
    return p if os.path.exists(p) else None


def next_still_path(work, shots, idx):
    """Path to the NEXT beat's still (for FLF2V end-frame).  None if no next."""
    if idx + 1 >= len(shots):
        return None
    return still_path(work, shots[idx + 1]["id"])


def render_motion(shot, cfg, root, work, shots, idx):
    from comfy_client import ComfyClient
    sid = shot["id"]
    outdir = os.path.join(work, sid)
    os.makedirs(outdir, exist_ok=True)
    if done(work, sid):
        print(f"[skip] {sid} already rendered.")
        return

    client = ComfyClient()
    tone = json.load(open(os.path.join(root, cfg["shots_file"])))["globals"]
    rp = cfg.get("realism", {}) or {}
    pos_suffix = (", " + rp["positive"]) if rp.get("positive") else ""
    neg_extra = (", " + rp["negative"]) if rp.get("negative") else ""
    thr = float((cfg.get("intensity") or {}).get("keep_glitch_threshold", 0.66))
    intensity = float(shot.get("intensity", 0.0) or 0.0)
    keep_glitch = intensity >= thr
    fx_suffix = (", " + shot["fx"]) if shot.get("fx") else ""
    base_neg = shot.get("negative", tone["negative_prompt"]) + neg_extra
    if keep_glitch:
        for bad in ["flickering", "temporal artifacts", "blurry", "motion blur",
                    "soft focus", "low detail", "low quality"]:
            base_neg = base_neg.replace(", " + bad, "")
    if keep_glitch:
        print(f"[motion] {sid}: directed-glitch beat (intensity={intensity}); keeping artifacts as design.")

    positive = (shot["prompt"]
               + (", " + shot["palette"] if shot.get("palette") else "")
               + ", " + shot.get("tone", tone["tone"])
               + fx_suffix + pos_suffix)

    # --- THE BLUEPRINT: use the already-rendered still as the anchor frame ---
    blueprint = still_path(work, sid)
    chars = shot.get("chars", [])

    # Workflow selection
    wf_name = "s2v" if shot.get("talking") else ("phantom" if len(chars) > 1 else "flf2v")
    if not chars:
        wf_name = "flf2v"
    graph = json.load(open(os.path.join(root, "workflows", f"{wf_name}.json")))

    # Build the override dict
    overrides = {
        "positive": positive,
        "negative": base_neg,
        "width": cfg["width"],
        "height": cfg["height"],
        "steps": int(os.environ.get("KIT_MOTION_STEPS", cfg["steps"])),
        "cfg": cfg["cfg"],
        "seconds": min(shot["dur"], cfg["max_clip_seconds"]),
    }

    # --- Wire the blueprint still as the image anchor ---
    if blueprint:
        print(f"[motion] {sid}: blueprint still -> {blueprint}")
        # For all workflows, ref_dirs binds to node 6 (LoadImage).
        # We pass the still directly as the ref image.
        overrides["ref_dirs"] = {"_still": os.path.dirname(blueprint)}

        # For FLF2V, also wire the end-frame (node 7) if a next still exists.
        # We do this by adding a second override key.
        if wf_name == "flf2v":
            nxt = next_still_path(work, shots, idx)
            if nxt:
                print(f"[motion] {sid}: end-frame -> {nxt}")
                # We need to override node 7's image input too.
                # The _bind only routes ref_dirs to node 6, so we set it directly.
                graph["7"]["inputs"]["image"] = nxt
    else:
        # No still exists yet -- fall back to character ref images (old behavior)
        print(f"[motion] {sid}: no blueprint still found, using character refs.")
        if chars:
            ref_dirs = {}
            for c in chars:
                d = os.path.join(root, "refs", c)
                if os.path.isdir(d):
                    ref_dirs[c] = d
            if ref_dirs:
                overrides["ref_dirs"] = ref_dirs

    # Denoise: low denoise = faithful to the blueprint; 1.0 = full re-gen.
    # The blueprint already carries the detail, so we default to 0.45 (mostly faithful).
    denoise = float(os.environ.get("KIT_MOTION_DENOISE", "0.45"))
    # Wire denoise into the KSampler (node 15)
    if "15" in graph and "denoise" in graph["15"]["inputs"]:
        graph["15"]["inputs"]["denoise"] = denoise
        print(f"[motion] {sid}: denoise={denoise} (blueprint-faithful)")

    takes = []
    n_samples = int(shot.get("rejection_samples", cfg["rejection_samples"]))
    for n in range(n_samples):
        overrides["seed"] = 42 + n
        out = client.run(graph, overrides=overrides)
        take = client.save_image(out, os.path.join(outdir, f"take_{n}.mp4"))
        takes.append(take)

    # Pick best take (same sharpness/flicker logic as render_film.py)
    flicker_weight = 0.0 if keep_glitch else 1.5
    best = pick_best(takes, flicker_weight=flicker_weight)

    # Post: upscale -> face restore -> interpolate
    subprocess.run(["bash", os.path.join(root, "src", "postprocess.sh"), best,
                    os.path.join(outdir, "final.mp4"), str(cfg["upscale"]),
                    str(cfg["interpolate_fps"]), str(cfg["codeformer_fidelity"]),
                    os.path.join(root, "refs", "grade_target.json")], check=True)
    print(f"[ok] {sid} -> {outdir}/final.mp4 (motion from blueprint)")


def _take_metrics(path):
    """Same metrics as render_film.py."""
    import cv2, numpy as np
    cap = cv2.VideoCapture(path)
    grays, sharp = [], []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        gd = cv2.medianBlur(g, 3)
        sharp.append(cv2.Laplacian(gd, cv2.CV_64F).var())
        grays.append(g.astype("float32"))
    cap.release()
    if not grays:
        return None
    jerks = [float(np.mean(np.abs(grays[i] - 2 * grays[i - 1] + grays[i - 2])))
             for i in range(2, len(grays))]
    mean_lum = float(np.mean([g.mean() for g in grays]))
    return {
        "sharpness": float(np.mean(sharp)),
        "flicker": float(np.mean(jerks)) if jerks else 0.0,
        "exposure_ok": 25.0 <= mean_lum <= 230.0,
    }


def pick_best(takes, flicker_weight=1.5):
    """Same pick_best as render_film.py."""
    try:
        import cv2  # noqa: F401
    except Exception:
        return takes[0]
    scored = []
    for t in takes:
        try:
            m = _take_metrics(t)
        except Exception:
            m = None
        if m:
            scored.append((t, m))
    if not scored:
        return takes[0]
    sh = [m["sharpness"] for _, m in scored]
    fl = [m["flicker"] for _, m in scored]
    def _norm(x, xs):
        lo, hi = min(xs), max(xs)
        return 0.0 if hi <= lo else (x - lo) / (hi - lo)
    best, best_score = scored[0][0], float("-inf")
    for t, m in scored:
        score = (_norm(m["sharpness"], sh)
                 - flicker_weight * _norm(m["flicker"], fl)
                 - (0.0 if m["exposure_ok"] else 1.0))
        if score > best_score:
            best, best_score = t, score
    return best


def _auto_shutdown():
    cmd = os.environ.get("KIT_SHUTDOWN_CMD", "sudo shutdown -h now")
    print(f"[shutdown] work complete -> stopping instance: {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=False)
    except Exception as e:
        print(f"[shutdown] FAILED ({e}) -- STOP THE INSTANCE MANUALLY to avoid charges.")


def main():
    import time
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--shutdown", action="store_true")
    ap.add_argument("--max-hours", type=float, default=0.0)
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "src"))
    cfg = load_cfg(root); work = cfg["paths"]["work"]; os.makedirs(work, exist_ok=True)
    if a.smoke:
        cfg["rejection_samples"] = 1
        cfg["steps"] = min(int(cfg.get("steps", 30)), 12)
        print("[smoke] motion validation: 1 beat, 1 take, low steps (cheap check).")
    shots = load_shots(root, cfg)["shots"]
    if a.smoke:
        shots = shots[:1]
    t0 = time.time()
    try:
        for idx, shot in enumerate(shots):
            if a.only and shot["id"] != a.only:
                continue
            if a.max_hours and (time.time() - t0) / 3600.0 >= a.max_hours:
                print(f"[budget] reached --max-hours={a.max_hours}; stopping before {shot['id']}. "
                      "Re-run to resume -- finished beats are auto-skipped.")
                break
            render_motion(shot, cfg, root, work, shots, idx)
        print("[ok] motion render loop finished.")
    finally:
        if a.shutdown:
            _auto_shutdown()


if __name__ == "__main__":
    main()

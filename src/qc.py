#!/usr/bin/env python3
"""qc.py -- objective QUALITY CHECK on rendered output before you publish/monetize.

Per shot it reports resolution, fps, duration, audio loudness (LUFS), black-frame and
freeze detection, and cv2 sharpness/flicker, then a PASS / WARN / FAIL verdict.
Exit code is non-zero if ANY shot FAILs, so you can gate a publish step on it.

Thresholds come from the `qc:` section of the active config (calibration = edit config,
no code change). RELIABLE checks: resolution / duration / fps / black / freeze / loudness.
ROUGH TRIAGE ONLY: sharpness and flicker (uncalibrated until tuned on real frames).

    python3 src/qc.py            # checks work/<id>/final.mp4 for every shot in config
    python3 src/qc.py --clips    # checks output/clips/<id>/<id>_master.mp4 instead
"""
import os, sys, json, re, subprocess, argparse

DEFAULT_TH = {"min_w": 640, "min_h": 360, "dur_tol": 0.75, "fps_tol": 1.5,
              "sharp_min": 15, "flicker_max": 12, "lufs_hi": -9, "lufs_lo": -30}

def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}

def parse_fps(s):
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except Exception:
        try:
            return float(s)
        except Exception:
            return 0.0

def loudness(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr)
    if m:
        try:
            return float(json.loads(m.group(0))["input_i"])
        except Exception:
            return None
    return None

def black_freeze(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-vf",
                        "blackdetect=d=0.3:pic_th=0.98,freezedetect=n=0.003:d=0.5",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    return len(re.findall(r"black_start", r.stderr)), len(re.findall(r"freeze_start", r.stderr))

def cv_metrics(path):
    try:
        import cv2, numpy as np
    except Exception:
        return None
    cap = cv2.VideoCapture(path)
    grays, sharp = [], []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        sharp.append(cv2.Laplacian(cv2.medianBlur(g, 3), cv2.CV_64F).var())
        grays.append(g.astype("float32"))
    cap.release()
    if not grays:
        return None
    jerks = [float(np.mean(np.abs(grays[i] - 2 * grays[i - 1] + grays[i - 2])))
             for i in range(2, len(grays))]
    return {"sharpness": float(np.mean(sharp)), "flicker": float(np.mean(jerks)) if jerks else 0.0}

def _worse(cur, new):
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    return new if order[new] > order[cur] else cur

def check(path, expect_dur, fps_target, th):
    if not os.path.exists(path):
        return "FAIL", ["missing file"], {}
    info = ffprobe(path)
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    aud = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vs:
        return "FAIL", ["no video stream"], {}
    v = vs[0]
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    fps = parse_fps(v.get("r_frame_rate", "0/1"))
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    stats = {"res": f"{w}x{h}", "fps": round(fps, 1), "dur": round(dur, 2), "audio": bool(aud)}
    verdict, issues = "PASS", []
    if w < th["min_w"] or h < th["min_h"]:
        issues.append(f"low res {w}x{h}"); verdict = _worse(verdict, "WARN")
    if expect_dur and abs(dur - expect_dur) > th["dur_tol"]:
        issues.append(f"dur {dur:.1f}s vs ~{expect_dur}s"); verdict = _worse(verdict, "WARN")
    if fps_target and abs(fps - fps_target) > th["fps_tol"]:
        issues.append(f"fps {fps:.1f} vs {fps_target}"); verdict = _worse(verdict, "WARN")
    b, fz = black_freeze(path)
    if b:
        issues.append(f"{b} black segment(s)"); verdict = _worse(verdict, "FAIL")
    if fz:
        issues.append(f"{fz} freeze(s)"); verdict = _worse(verdict, "WARN")
    m = cv_metrics(path)
    if m:
        stats["sharp"] = round(m["sharpness"], 1); stats["flicker"] = round(m["flicker"], 2)
        if m["sharpness"] < th["sharp_min"]:
            issues.append("very soft/blurry"); verdict = _worse(verdict, "WARN")
        if m["flicker"] > th["flicker_max"]:
            issues.append("high flicker"); verdict = _worse(verdict, "WARN")
    if aud:
        lu = loudness(path)
        if lu is not None:
            stats["LUFS"] = round(lu, 1)
            if lu > th["lufs_hi"] or lu < th["lufs_lo"]:
                issues.append(f"loudness {lu:.1f} LUFS off -16 target"); verdict = _worse(verdict, "WARN")
    else:
        issues.append("no audio"); verdict = _worse(verdict, "WARN")
    return verdict, issues, stats

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", action="store_true", help="check output/clips masters instead of work clips")
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "src"))
    from cfgutil import load_cfg
    cfg = load_cfg(root)
    th = dict(DEFAULT_TH); th.update(cfg.get("qc", {}) or {})
    work = cfg["paths"]["work"]
    out = os.path.join(root, cfg["paths"]["out"])
    fps_target = cfg.get("interpolate_fps", cfg.get("fps", 24))
    shots = json.load(open(os.path.join(root, cfg["shots_file"])))["shots"]
    fails = warns = passes = 0
    for s in shots:
        sid = s["id"]
        path = (os.path.join(out, "clips", sid, f"{sid}_master.mp4") if a.clips
                else os.path.join(work, sid, "final.mp4"))
        verdict, issues, stats = check(path, s.get("dur", 0), fps_target, th)
        fails += verdict == "FAIL"; warns += verdict == "WARN"; passes += verdict == "PASS"
        print(f"[{verdict}] {sid}  {stats}  {'; '.join(issues) if issues else 'ok'}")
    print(f"\n[qc] {len(shots)} checked: {passes} PASS, {warns} WARN, {fails} FAIL")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()

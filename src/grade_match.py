#!/usr/bin/env python3
"""Match a clip's color grade toward a target look + add subtle film grain.

Usage: grade_match.py IN OUT TARGET_JSON [STRENGTH] [GRAIN]

Uses a GLOBAL (whole-clip) LAB mean/std transfer so the grade is temporally
stable (no per-frame color flicker), then adds light moving grain for a filmic
feel. Decodes/encodes via ffmpeg (frames read with cv2.imread COLOR) for
robustness. Fully NON-FATAL: on any problem it copies IN->OUT unchanged so the
pipeline never breaks.
"""
import sys, os, json, glob, tempfile, subprocess


def get_fps(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True)
        a, b = r.stdout.strip().split("/")
        v = float(a) / float(b)
        return v if v > 0 else 24.0
    except Exception:
        return 24.0


def _copy(src, dst):
    subprocess.run(["cp", src, dst])


def main():
    if len(sys.argv) < 4:
        print("usage: grade_match.py IN OUT TARGET_JSON [STRENGTH] [GRAIN]")
        return 1
    src, dst, target = sys.argv[1], sys.argv[2], sys.argv[3]
    strength = float(sys.argv[4]) if len(sys.argv) > 4 else 0.8
    grain = float(sys.argv[5]) if len(sys.argv) > 5 else 3.5
    try:
        import cv2, numpy as np
    except Exception as e:
        print("[grade] cv2/numpy missing; copy unchanged:", e); _copy(src, dst); return 0
    try:
        tj = json.load(open(target))
        t_mean = np.array(tj["lab_mean"], dtype=np.float32)
        t_std = np.array(tj["lab_std"], dtype=np.float32)
    except Exception as e:
        print("[grade] no target; copy unchanged:", e); _copy(src, dst); return 0
    fps = get_fps(src)
    din = tempfile.mkdtemp(); dout = tempfile.mkdtemp()
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        os.path.join(din, "i_%05d.png")])
    files = sorted(glob.glob(os.path.join(din, "i_*.png")))
    if r.returncode != 0 or not files:
        print("[grade] decode failed; copy unchanged"); _copy(src, dst); return 0
    # pass 1: global LAB stats (temporally stable transform)
    ssum = np.zeros(3, np.float64); ssq = np.zeros(3, np.float64); cnt = 0
    for f in files:
        im = cv2.imread(f, cv2.IMREAD_COLOR)
        if im is None:
            continue
        lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float64).reshape(-1, 3)
        ssum += lab.sum(0); ssq += (lab * lab).sum(0); cnt += lab.shape[0]
    if cnt == 0:
        print("[grade] no readable frames; copy unchanged"); _copy(src, dst); return 0
    s_mean = (ssum / cnt).astype(np.float32)
    s_std = np.sqrt(np.maximum(ssq / cnt - (ssum / cnt) ** 2, 1e-6)).astype(np.float32)
    gain = t_std / (s_std + 1e-5)
    # pass 2: apply grade + grain
    rng = np.random.default_rng(0)
    for f in files:
        im = cv2.imread(f, cv2.IMREAD_COLOR)
        if im is None:
            continue
        lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).astype(np.float32)
        graded = (lab - s_mean) * gain + t_mean
        outl = lab * (1.0 - strength) + graded * strength
        bgr = cv2.cvtColor(np.clip(outl, 0, 255).astype("uint8"), cv2.COLOR_LAB2BGR).astype(np.float32)
        if grain > 0:
            bgr = np.clip(bgr + rng.normal(0, grain, bgr.shape[:2])[..., None], 0, 255)
        cv2.imwrite(os.path.join(dout, os.path.basename(f)), bgr.astype("uint8"))
    enc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.4f}",
                          "-i", os.path.join(dout, "i_%05d.png"), "-c:v", "libx264",
                          "-preset", "slow", "-crf", "12", "-pix_fmt", "yuv420p",
                          "-colorspace", "bt709", "-color_primaries", "bt709",
                          "-color_trc", "bt709", dst])
    if enc.returncode != 0 or not os.path.exists(dst):
        print("[grade] encode failed; copy unchanged"); _copy(src, dst); return 0
    # optional 4:4:4 10-bit ProRes master (env KIT_MASTER=1), encoded from the
    # full-chroma RGB frames BEFORE any H.264 4:2:0 subsampling. Non-fatal.
    # NOTE: prores_ks caps real precision at 10-bit (yuv444p10le) even though the
    # file probes as 12-bit; true 12-bit needs a proprietary encoder.
    if os.environ.get("KIT_MASTER") == "1":
        master = os.path.splitext(dst)[0] + "_MASTER_4444.mov"
        m = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{fps:.4f}",
                            "-i", os.path.join(dout, "i_%05d.png"),
                            "-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0",
                            "-pix_fmt", "yuv444p10le", "-colorspace", "bt709",
                            "-color_primaries", "bt709", "-color_trc", "bt709",
                            "-r", f"{fps:.4f}", master])
        if m.returncode == 0 and os.path.exists(master):
            print(f"[grade] 4:4:4 10-bit master -> {master}")
        else:
            print("[grade] master export skipped (encode failed)")
    print(f"[grade] graded -> {dst} (fps {fps:.3f}, {len(files)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

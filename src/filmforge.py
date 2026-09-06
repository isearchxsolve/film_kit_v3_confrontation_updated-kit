#!/usr/bin/env python3
"""filmforge — single entry point that chains the whole pipeline, resumable.
  preflight -> cull photos -> build young-Kunal set (de-age) -> render shots
            -> voice -> stitch + mux.
Each stage is idempotent; a dropped session re-runs and skips finished work.
"""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

def stage(title, *cmd):
    print(f"\n===== {title} =====")
    r = subprocess.run(list(cmd), cwd=ROOT)
    if r.returncode != 0:
        print(f"[FAIL] stage '{title}' exited {r.returncode}")
        sys.exit(r.returncode)

def main():
    stage("1/7 preflight", PY, os.path.join(SRC, "preflight.py"))
    for person in ("refs/kunal", "refs/gunja"):
        stage(f"2/7 cull {person}", PY, os.path.join(SRC, "cull_photos.py"),
              os.path.join(ROOT, person))
    stage("3/7 build young-Kunal set (de-age)", PY, os.path.join(SRC, "deage.py"),
          "--refs", os.path.join(ROOT, "refs"))
    stage("4/7 cost meter (1 sample shot + projection)", PY, os.path.join(SRC, "cost_meter.py"))
    if os.environ.get("CONFIRM_FULL_RUN") != "1":
        print("")
        print("===== HELD AT COST METER =====")
        print("Review the projection above (saved to work/cost_report.json).")
        print("Nothing else rendered; no budget committed.")
        print("To render the whole film: set CONFIRM_FULL_RUN=1 and run again.")
        print("The sample shot is kept and reused -- zero waste.")
        return
    stage("5/7 render shots", PY, os.path.join(SRC, "render_film.py"))
    stage("6/7 voice", PY, os.path.join(SRC, "voice_pass.py"), "--root", ROOT)
    stage("7/7 stitch + mux", PY, os.path.join(SRC, "stitch.py"), "--root", ROOT)
    print("\nDONE -> output/THE_MOMENT.mp4")

if __name__ == "__main__":
    main()

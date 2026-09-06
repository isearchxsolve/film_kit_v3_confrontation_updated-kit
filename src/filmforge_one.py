#!/usr/bin/env python3
"""filmforge (THE ONE - the single merged film) - resumable.
  preflight -> forge every face (young Kunal, 40s self, creature, her)
            -> cull any supplied photos (no-op when fully generated)
            -> cost meter (HOLD) -> render -> voice (lead + chorus)
            -> auto-compose score -> stitch.
Forces KIT_CONFIG=config_one.yaml so every stage sees the merged config.
Fully generated: no real photos required; the wordless score is auto-composed too.
"""
import os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = sys.executable
os.environ["KIT_CONFIG"] = "config_one.yaml"

def stage(title, *cmd):
    print(f"\n===== {title} =====")
    r = subprocess.run(list(cmd), cwd=ROOT, env={**os.environ})
    if r.returncode != 0:
        print(f"[FAIL] stage '{title}' exited {r.returncode}")
        sys.exit(r.returncode)

def main():
    stage("1/8 preflight", PY, os.path.join(SRC, "preflight.py"))
    stage("2/8 forge faces (protagonist both ages + creature + her)", PY, os.path.join(SRC, "forge_character.py"))
    stage("3/8 cull any supplied photos (no-op when fully generated)", PY, os.path.join(SRC, "cull_photos.py"), os.path.join(ROOT, "refs/gunja"))
    stage("4/8 cost meter (1 sample shot + projection)", PY, os.path.join(SRC, "cost_meter.py"))
    if os.environ.get("CONFIRM_FULL_RUN") != "1":
        print("")
        print("===== HELD AT COST METER =====")
        print("Review the projection above (saved to work/cost_report.json).")
        print("Nothing else rendered; no budget committed.")
        print("To render the whole film: set CONFIRM_FULL_RUN=1 and run again.")
        print("The sample shot is kept and reused -- zero waste.")
        return
    stage("5/8 render shots", PY, os.path.join(SRC, "render_film.py"))
    stage("6/8 voice (lead + chorus)", PY, os.path.join(SRC, "voice_pass.py"), "--root", ROOT)
    # score runs after voice so the GPU is free of the TTS models before MusicGen loads
    stage("7/8 score (auto-composed, wordless)", PY, os.path.join(SRC, "score_gen.py"), "--root", ROOT)
    stage("8/8 stitch + mux", PY, os.path.join(SRC, "stitch.py"), "--root", ROOT)
    print("\nDONE -> output_one/THE_ONE.mp4")

if __name__ == "__main__":
    main()

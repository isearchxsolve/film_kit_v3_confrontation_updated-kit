#!/usr/bin/env python3
"""run.py -- THE CONFRONTATION CLIP: single-command, zero-burn orchestrator.

One script. Maximum automation. Every guardrail in front of the money.

    python3 run.py                     # SAFE default: audit + cost meter, then HOLD (no full render)
    CONFIRM_FULL_RUN=1 python3 run.py  # actually render the full clip (after the gates pass)

Order of operations (fail-fast; each stage must pass before the next):
  1) preflight   -- free, ~5s. Missing model/dep/GPU/disk => abort for ~0 rupees.
  2) forge       -- build character reference identities (idempotent).
  3) cost meter  -- render ONE real sample shot, measure true speed, project GPU-hours + $.
  4) BUDGET GATE -- if projected cost > MAX_BUDGET_USD, ABORT before the full render.
  5) HOLD GATE   -- stop here unless CONFIRM_FULL_RUN=1 (dry-run is the default).
  6) render      -- resumable per-shot render (finished shots auto-skip). Optional auto-shutdown.
  7) voice       -- dramatic VO + chorus.
  8) score       -- original violin-led confrontation cue (+ prelude).
  9) sfx         -- sound design / ambience foley (procedural, free): the world under the score.
 10) lipsync     -- self-skips (all beats are VO/narration).
 11) finale      -- the closing: the film's own moving images accelerate and curdle into a joke.
 12) stitch      -- final mux + master (+ ambience bed, + appended finale) -> THE_CONFRONTATION_CLIP.mp4

Env knobs:
  CONFIRM_FULL_RUN=1   required to spend GPU beyond the single cost-meter sample
  MAX_BUDGET_USD=8     hard ceiling; run aborts if the projection exceeds it (default: unset = no ceiling)
  MAX_HOURS=6          wall-clock cap passed to the renderer (resumable); 0 = no cap
  AUTO_SHUTDOWN=1      stop the instance the instant the render loop ends (prevents idle burn)
  KIT_REQUIRE_GPU=1    make 'no GPU' a fatal preflight error (auto-set on paid profiles)
  KIT_RENDER_MODE=stills       render one pixel-perfect still per beat, freeze-hold as video (comic-book path, 5-8x faster)
  KIT_RENDER_MODE=motion_stills  BLUEPRINT PATH: stills first, then animate each still into motion (1% GPU load)
                        default (unset) = full motion render via render_film.py (text-prompt only, no blueprint)
"""
import os, sys, json, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
PY = sys.executable
os.environ["KIT_CONFIG"] = "config_confrontation.yaml"


def load_cfg():
    sys.path.insert(0, SRC)
    from cfgutil import load_cfg as _lc
    return _lc(ROOT)


def stage(title, *cmd):
    print(f"\n===== {title} =====", flush=True)
    r = subprocess.run(list(cmd), cwd=ROOT, env={**os.environ})
    if r.returncode != 0:
        print(f"[FAIL] stage '{title}' exited {r.returncode} -- stopping (nothing downstream runs).")
        sys.exit(r.returncode)


def main():
    cfg = load_cfg()
    profile = cfg.get("_active_profile")
    rate = float(cfg.get("rate_per_gpu_hr", 0.0) or 0.0)
    work = cfg["paths"]["work"]
    os.makedirs(work, exist_ok=True)

    # VRAM-safe score model on the 40GB card
    if profile == "lightning_a100_40gb":
        os.environ.setdefault("MG_MODEL", "facebook/musicgen-medium")
        os.environ.setdefault("SCORE_CHUNK_SEC", "18")

    # On a paid GPU box, absence of a GPU should be fatal (don't pay to run on CPU)
    if rate > 0:
        os.environ.setdefault("KIT_REQUIRE_GPU", "1")

    print("================ THE CONFRONTATION CLIP -- run.py ================")
    print(f"  profile        : {profile}")
    print(f"  rate/GPU-hr    : ${rate:.2f}" + ("  (free tier)" if rate == 0 else ""))
    print(f"  CONFIRM_FULL_RUN={os.environ.get('CONFIRM_FULL_RUN','(unset)')}  "
          f"MAX_BUDGET_USD={os.environ.get('MAX_BUDGET_USD','(none)')}  "
          f"MAX_HOURS={os.environ.get('MAX_HOURS','0')}  "
          f"AUTO_SHUTDOWN={os.environ.get('AUTO_SHUTDOWN','0')}")
    print("=================================================================")

    # 1) FREE preflight gate
    stage("1/10 preflight (free)", PY, os.path.join(SRC, "preflight.py"))

    # 2) character identities (idempotent)
    stage("2/10 forge character identities", PY, os.path.join(SRC, "forge_character.py"))

    # 3) cost meter -- renders ONE sample, measures real speed, writes cost_report.json
    os.environ["BUDGET_USD"] = os.environ.get("MAX_BUDGET_USD", "0")
    stage("3/10 cost meter (1 sample shot)", PY, os.path.join(SRC, "cost_meter.py"))

    # 4) HARD budget gate
    report_path = os.path.join(work, "cost_report.json")
    projected = None
    try:
        report = json.load(open(report_path))
        projected = float(report.get("projected_cost_usd", 0.0))
    except Exception as e:
        print(f"[budget] could not read {report_path} ({e}); refusing to proceed blind.")
        sys.exit(3)
    cap = os.environ.get("MAX_BUDGET_USD")
    if cap:
        cap = float(cap)
        print(f"\n[budget] projected full-render cost = ${projected:.2f}  |  ceiling = ${cap:.2f}")
        if projected > cap:
            print("[budget] ABORT -- projection EXCEEDS your ceiling. No full render started.")
            print("         The single sample is checkpointed, so nothing is wasted.")
            print("         Options: lower steps/rejection_samples, switch to a cheaper profile,")
            print("                  raise MAX_BUDGET_USD, or set rate_per_gpu_hr: 0.0 if on free hours.")
            sys.exit(4)
        print("[budget] OK -- within ceiling.")
    else:
        print(f"\n[budget] projected full-render cost = ${projected:.2f}  (no MAX_BUDGET_USD ceiling set)")

    # 5) HOLD gate -- dry-run is the default; spending requires explicit confirmation
    if os.environ.get("CONFIRM_FULL_RUN") != "1":
        print("\n===== HELD BEFORE FULL RENDER =====")
        print("Audit + cost meter passed. Review the projection above.")
        print("To render the full clip: CONFIRM_FULL_RUN=1 python3 run.py")
        return

    # 6) full render (resumable; finished shots auto-skip)
    #    KIT_RENDER_MODE=stills -> comic-book path: one pixel-perfect still per beat,
    #    freeze-held as video.  5-8x faster, full sound still plays under the stills.
    render_mode = os.environ.get("KIT_RENDER_MODE", "motion")
    if render_mode == "stills":
        render_script = "render_stills.py"
    elif render_mode == "motion_stills":
        render_script = "render_motion.py"
    else:
        render_script = "render_film.py"
    render_cmd = [PY, os.path.join(SRC, render_script)]
    max_hours = os.environ.get("MAX_HOURS", "0")
    if max_hours and max_hours != "0":
        render_cmd += ["--max-hours", str(max_hours)]
    if os.environ.get("AUTO_SHUTDOWN") == "1":
        render_cmd += ["--shutdown"]
    if render_mode == "stills":
        stage_title = "6/10 render confrontation stills (comic-book path)"
    elif render_mode == "motion_stills":
        stage_title = "6/10 render confrontation motion (blueprint path: stills -> motion)"
    else:
        stage_title = "6/10 render confrontation video"
    print(f"\n[mode] KIT_RENDER_MODE={render_mode} -> {render_script}")
    stage(stage_title, *render_cmd)

    # 7-10) audio + assembly
    stage("7/12 dramatic voice (VO + chorus)", PY, os.path.join(SRC, "voice_pass.py"), "--root", ROOT)
    stage("8/12 original score (violin-led)", PY, os.path.join(SRC, "score_gen.py"), "--root", ROOT)
    stage("9/12 sound design + ambience (world foley)", PY, os.path.join(SRC, "sfx_gen.py"), "--root", ROOT)
    stage("10/12 lip-sync (self-skips for VO)", PY, os.path.join(SRC, "lipsync.py"), "--root", ROOT)
    stage("11/12 finale: moving images -> the joke", PY, os.path.join(SRC, "finale_montage.py"), "--root", ROOT)
    stage("12/12 stitch + cinematic master", PY, os.path.join(SRC, "stitch.py"), "--root", ROOT)

    print("\nDONE -> output_confrontation/THE_CONFRONTATION_CLIP.mp4")


if __name__ == "__main__":
    main()

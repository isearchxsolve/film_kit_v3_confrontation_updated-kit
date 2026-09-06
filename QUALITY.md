# Quality checks

Two gates you run for FREE after a render, before publishing or monetizing.

## 1. Technical QC -- `src/qc.py`

Objective, playable-file checks per shot: resolution, fps, duration, black-frame &
freeze detection, audio loudness (LUFS), and cv2 sharpness/flicker. Prints PASS / WARN /
FAIL and exits non-zero on any FAIL so you can gate an upload on it.

```bash
python3 src/qc.py            # rendered shots  (work/<id>/final.mp4)
python3 src/qc.py --clips    # monetization masters (output/clips/<id>/..._master.mp4)
```

**Reliable:** resolution / duration / fps / black / freeze / loudness -- these catch dead,
truncated, silent, or wrong-size renders.
**Rough triage only:** sharpness & flicker are heuristic and NOT yet calibrated on real
generative frames. High sharpness can mean noise; fast motion reads as flicker.

### Calibration (after your first real GPU batch)
Thresholds live in the `qc:` block of `config.yaml` -- edit the numbers, no code change:
```yaml
qc:
  min_w: 640
  sharp_min: 15      # raise/lower once you have seen real frames
  flicker_max: 12
  lufs_hi: -9
  lufs_lo: -30
```

## 2. Identity-consistency -- `src/identity_check.py`

The monetization-critical check: are Kunal / Gunja the SAME face across every shot?
Detects faces, embeds them with ArcFace, and compares each shot against the character's
reference images in `refs/<char>/`; flags any shot whose best face-match cosine falls below
`identity.drift_cosine`.

```bash
pip install insightface onnxruntime-gpu   # render box; onnxruntime already present
python3 src/identity_check.py             # rendered shots
python3 src/identity_check.py --clips     # monetization masters
# CPU-only box: export KIT_FACE_CTX=-1
```
First run downloads the buffalo_l model (~300MB). Exit 0 = no drift, 1 = drift found,
2 = cannot run (insightface missing or no usable references).

```yaml
identity:
  drift_cosine: 0.35   # below = different-looking face; tune after seeing real output
  sample_frames: 12
  min_det_score: 0.5
```

## Honest limits
- `qc.py` proves a render technically SUCCEEDED; it does not judge realism, anatomy, prompt
  adherence, or lip-sync -- those still need YOUR EYES on the footage.
- `identity_check.py` needs real faces + insightface, so it cannot be validated on the
  synthetic self-test media; its thresholds are provisional until run on real frames.
- Neither tool can tell you a shot looks "AI-generated." There is no automated substitute
  for watching the output before you publish.

#!/usr/bin/env bash
# postprocess.sh IN OUT UPSCALE FPS CODEFORMER_FIDELITY
# High-fidelity finishing pass: upscale + face-restore -> motion-faithful interpolation.
# Prefers real tools (CodeFormer / RIFE / Real-ESRGAN); otherwise falls back to
# high-quality, MOTION-COMPENSATED ffmpeg so motion is carried faithfully and detail
# is preserved. BT.709 color is kept end-to-end. Deterministic and resumable-safe.
set -euo pipefail
IN="$1"; OUT="$2"; UP="${3:-1.75}"; FPS="${4:-48}"; FID="${5:-0.7}"; GRADE="${6:-}"
POST_MODE="${POST_MODE:-gpu}"   # gpu = CodeFormer/RIFE/Real-ESRGAN; cpu = ffmpeg-only (FREE, offline, no GPU)
echo "[postprocess] POST_MODE=$POST_MODE"
WORK="$(dirname "$OUT")"; TMP="$WORK/_pp"; rm -rf "$TMP"; mkdir -p "$TMP"
COLOR="-colorspace bt709 -color_primaries bt709 -color_trc bt709"

# source fps (only synthesize the *added* frames; never resample the real ones)
SRCFPS="$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate \
  -of default=nk=1:nw=1 "$IN" | awk -F/ '{ if (NF==2 && $2>0) printf "%.4f",$1/$2; else print $1 }')"
[ -z "$SRCFPS" ] && SRCFPS=24

# 1) extract frames losslessly
ffmpeg -y -loglevel error -i "$IN" -qscale:v 1 "$TMP/f_%05d.png"

# 2) upscale + face restore (CodeFormer does both; else Real-ESRGAN; else geometric)
# POST_MODE=cpu skips every GPU tool and uses the ffmpeg/geometric path so this whole
# stage runs free & offline on a laptop (lower quality: no AI face-restore).
if [ "$POST_MODE" != "cpu" ] && command -v codeformer >/dev/null 2>&1; then
  codeformer -w "$FID" --upscale "$UP" -i "$TMP" -o "$TMP/cf" >/dev/null 2>&1 || true
  if [ -d "$TMP/cf/final_results" ]; then SRC="$TMP/cf/final_results"; else SRC="$TMP"; fi
elif [ "$POST_MODE" != "cpu" ] && command -v realesrgan-ncnn-vulkan >/dev/null 2>&1; then
  mkdir -p "$TMP/up"
  if realesrgan-ncnn-vulkan -i "$TMP" -o "$TMP/up" -s 4 >/dev/null 2>&1; then SRC="$TMP/up"; else SRC="$TMP"; fi
else
  SRC="$TMP"
fi

# 3) reassemble at SOURCE fps (geometric upscale here only if no external upscaler ran)
if [ "$SRC" = "$TMP" ]; then VF="scale=iw*${UP}:ih*${UP}:flags=lanczos"; else VF="scale=trunc(iw/2)*2:trunc(ih/2)*2"; fi
ffmpeg -y -loglevel error -framerate "$SRCFPS" -pattern_type glob -i "$SRC/*.png" \
  -vf "$VF" -c:v libx264 -preset slow -crf 14 -pix_fmt yuv420p $COLOR "$TMP/base.mp4"

# 3.5) FAST DEBUG SHORT-CIRCUIT: skip interpolation + grade for a quick, cheap cycle.
# KIT_FAST_POST=1 is for debugging only (final quality drops interpolation/grade).
if [ "${KIT_FAST_POST:-0}" = "1" ]; then
  cp "$TMP/base.mp4" "$OUT"; rm -rf "$TMP"; echo "postprocess(fast, no-interp/grade) -> $OUT"; exit 0
fi

# 4) interpolate to target fps (RIFE if present, else motion-compensated ffmpeg)
if [ "$POST_MODE" != "cpu" ] && command -v rife-ncnn-vulkan >/dev/null 2>&1; then
  mkdir -p "$TMP/ri" "$TMP/ro"
  ffmpeg -y -loglevel error -i "$TMP/base.mp4" "$TMP/ri/%06d.png"
  rife-ncnn-vulkan -i "$TMP/ri" -o "$TMP/ro" >/dev/null 2>&1 || true
  if [ -n "$(ls -A "$TMP/ro" 2>/dev/null)" ]; then
    ffmpeg -y -loglevel error -framerate "$FPS" -i "$TMP/ro/%06d.png" \
      -c:v libx264 -preset slow -crf 14 -pix_fmt yuv420p $COLOR "$OUT"
  else
    cp "$TMP/base.mp4" "$OUT"
  fi
else
  # synthesize intermediate frames ALONG motion vectors (faithful motion, no ghosting stutter)
  ffmpeg -y -loglevel error -i "$TMP/base.mp4" \
    -vf "minterpolate=fps=${FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" \
    -c:v libx264 -preset slow -crf 14 -pix_fmt yuv420p $COLOR "$OUT"
fi
# 5) optional color-grade + film-grain match to a target look (refs/grade_target.json)
if [ -n "$GRADE" ] && [ -f "$GRADE" ] && command -v python3 >/dev/null 2>&1; then
  if python3 "$(dirname "$0")/grade_match.py" "$OUT" "$TMP/graded.mp4" "$GRADE"; then
    [ -f "$TMP/graded.mp4" ] && mv "$TMP/graded.mp4" "$OUT"
  fi
fi
rm -rf "$TMP"
echo "postprocess -> $OUT"

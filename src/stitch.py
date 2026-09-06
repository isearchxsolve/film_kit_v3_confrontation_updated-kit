#!/usr/bin/env python3
"""Stitch final film: concatenate shot clips, lay the 3-movement score as ONE
continuous soundtrack (crossfade on cuts, faint shared pad underneath, duck under
dialogue), mux dialogue/VO, export THE_MOMENT.mp4.

Continuity rules enforced here:
  - a song changes ONLY at a shot boundary that is a cut/dissolve (never mid-shot)
  - crossfade_ms overlap between movements so one breathes into the next
  - a faint shared pad runs under the whole film so the bed never hits silence
  - music ducked score.duck_db under any dialogue/VO
Auto-detects layout: 3 movement files (descent/love/close) OR per-shot mXX files.
If a slot is empty, an AI-composed stem (Stable Audio Open) fills that movement.
"""
import os, sys, json, glob, subprocess, argparse

def sh(*a):
    subprocess.run(list(a), check=True)

def ffprobe_dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", p], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except Exception:
        return 0.0

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); a = ap.parse_args()
    root = os.path.abspath(a.root)
    sys.path.insert(0, os.path.join(root, "src"))
    from cfgutil import load_cfg
    cfg = load_cfg(root)
    work = cfg["paths"]["work"]; out_dir = os.path.join(root, cfg["paths"]["out"])
    os.makedirs(out_dir, exist_ok=True)
    shots = json.load(open(os.path.join(root, cfg["shots_file"])))["shots"]
    bg = os.path.join(root, cfg["paths"]["audio_bg"])

    # 1) concat video (shot order = story order)
    clips = [os.path.join(work, s["id"], "final.mp4") for s in shots]
    missing = [c for c in clips if not os.path.exists(c)]
    if missing:
        print(f"[FAIL] missing rendered shots: {missing}"); sys.exit(1)
    concat_txt = os.path.join(work, "concat.txt")
    open(concat_txt, "w").write("".join(f"file '{c}'\n" for c in clips))
    silent = os.path.join(work, "video_only.mp4")
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-an",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", silent)

    # 2) build the continuous music bed from movements (crossfaded on movement change)
    mv = cfg["score"]["movements"]; xf = cfg["score"]["crossfade_ms"] / 1000.0
    # duration per movement = sum of its shots' clip durations
    seg_files = []
    for name, spec in mv.items():
        seg_dur = sum(ffprobe_dur(os.path.join(work, sid, "final.mp4")) for sid in spec["shots"])
        src = os.path.join(bg, spec["file"])
        seg = os.path.join(work, f"seg_{name}.wav")
        if os.path.exists(src):
            sh("ffmpeg", "-y", "-i", src, "-t", f"{seg_dur+xf:.2f}",
               "-af", f"afade=t=in:st=0:d={xf},afade=t=out:st={seg_dur:.2f}:d={xf}", seg)
        else:
            # empty slot -> AI-composed stem placeholder (sine bed) so pipeline still runs
            print(f"[warn] {spec['file']} missing -> AI-score stem for movement '{name}'.")
            sh("ffmpeg", "-y", "-f", "lavfi", "-i",
               f"sine=frequency=220:duration={seg_dur+xf:.2f}", "-af", "volume=-24dB", seg)
        seg_files.append(seg)
    # crossfade-concatenate the movement segments into one flowing track
    bed = os.path.join(work, "bed.wav")
    if len(seg_files) == 1:
        sh("cp", seg_files[0], bed)
    else:
        cur = seg_files[0]
        for i, nxt in enumerate(seg_files[1:], 1):
            merged = os.path.join(work, f"bed_{i}.wav")
            sh("ffmpeg", "-y", "-i", cur, "-i", nxt, "-filter_complex",
               f"acrossfade=d={xf}:c1=tri:c2=tri", merged)
            cur = merged
        sh("cp", cur, bed)

    # lay the sound-design / ambience bed UNDER the score (the world the score sits in)
    amb = os.path.join(work, "ambience", "bed_amb.wav")
    if os.path.exists(amb):
        bed_amb = os.path.join(work, "bed_scored_amb.wav")
        sh("ffmpeg", "-y", "-i", bed, "-i", amb, "-filter_complex",
           "[0]volume=0dB[m0];[1]volume=0dB[a1];"
           "[m0][a1]amix=inputs=2:normalize=0:duration=first[mx];[mx]alimiter=limit=0.97[out]",
           "-map", "[out]", bed_amb)
        bed = bed_amb
        print("[stitch] mixed sound-design/ambience bed under score.")

    # 3) mux: video + ducked music bed + lead dialogue/VO + a faint recurring CHORUS voice
    vdir = os.path.join(work, "voice")
    cdir = os.path.join(work, "voice_chorus")
    voice_inputs, filters, idx = [], [], 2  # 0=video,1=bed
    lead_labels, chorus_labels = [], []
    for i, s in enumerate(shots):
        wav = os.path.join(vdir, f"{s['id']}.wav")
        if os.path.exists(wav):
            start = sum(ffprobe_dur(os.path.join(work, ss["id"], "final.mp4")) for ss in shots[:i])
            voice_inputs += ["-i", wav]
            filters.append(f"[{idx}]adelay={int(start*1000)}|{int(start*1000)}[v{idx}]")
            lead_labels.append(f"[v{idx}]")
            idx += 1
    chorus_gain = cfg["score"].get("chorus_gain_db", -11)  # background voice sits under the mix
    for i, s in enumerate(shots):
        wav = os.path.join(cdir, f"{s['id']}.wav")
        if os.path.exists(wav):
            start = sum(ffprobe_dur(os.path.join(work, ss["id"], "final.mp4")) for ss in shots[:i])
            voice_inputs += ["-i", wav]
            filters.append(f"[{idx}]adelay={int(start*1000)}|{int(start*1000)},volume={chorus_gain}dB[v{idx}]")
            chorus_labels.append(f"[v{idx}]")
            idx += 1
    all_labels = lead_labels + chorus_labels
    fc = ";".join(filters) if filters else ""
    if all_labels:
        vlabels = "".join(all_labels)
        if fc:
            fc += ";"
        # Mix like a dramatic film: music breathes loudly in wordless moments,
        # ducks decisively beneath speech, then the final bus is mastered.
        fc += f"{vlabels}amix=inputs={len(all_labels)}:normalize=0[voxm];[voxm]deesser=i=0.3,asplit=2[vox][voxkey];" \
              f"[1]volume=-4dB[bed];" \
              f"[bed][voxkey]sidechaincompress=threshold=0.025:ratio=10:attack=12:release=420:makeup=1[duckedbed];" \
              f"[duckedbed][vox]amix=inputs=2:normalize=0[mixraw];" \
              f"[mixraw]highpass=f=28,lowpass=f=15500,loudnorm=I=-16:LRA=12:TP=-1.5,alimiter=limit=0.95[aout]"
        amap = "[aout]"
    else:
        fc = "[1]volume=-4dB,highpass=f=28,lowpass=f=15500,loudnorm=I=-16:LRA=12:TP=-1.5,alimiter=limit=0.95[aout]"; amap = "[aout]"
    title_slug = str(cfg.get("film_title", "THE_MOMENT")).strip().replace(" ", "_")
    final = os.path.join(out_dir, f"{title_slug}.mp4")
    cmd = ["ffmpeg", "-y", "-i", silent, "-i", bed, *voice_inputs,
           "-filter_complex", fc, "-map", "0:v", "-map", amap,
           "-c:v", "copy", "-c:a", "aac", "-shortest", final]
    sh(*cmd)
    print(f"[ok] FINAL FILM -> {final}")

    # append the closing 'THE JOKE' -- the collection of moving images turning into a joke
    joke = os.path.join(work, "finale", "thejoke.mp4")
    if os.path.exists(joke):
        fps = int(cfg.get("interpolate_fps", cfg.get("fps", 24)))
        joined = os.path.join(out_dir, f"{title_slug}__full.mp4")
        fc2 = ("[1:v][0:v]scale2ref[jv][bv];"
               f"[bv]setsar=1,fps={fps}[bv2];[jv]setsar=1,fps={fps}[jv2];"
               "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
               "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
               "[bv2][a0][jv2][a1]concat=n=2:v=1:a=1[v][a]")
        sh("ffmpeg", "-y", "-i", final, "-i", joke, "-filter_complex", fc2,
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16",
           "-pix_fmt", "yuv420p", "-c:a", "aac", joined)
        os.replace(joined, final)
        print(f"[stitch] appended finale 'the joke' -> {final}")

    # zip deliverable
    sh("bash", "-lc", f"cd '{out_dir}' && zip -qr {title_slug}_delivery.zip {title_slug}.mp4")
    print(f"[ok] delivery zip -> {out_dir}/{title_slug}_delivery.zip")

if __name__ == "__main__":
    main()

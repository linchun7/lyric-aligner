#!/usr/bin/env python3
"""Freeze a recording-grouped outer validation sample and unfilled listening CSV.

The pool supplies independently identified recording_group, audio_path,
audio_sha256, id, text, start_ms, end_ms. Timing is a clip locator, never gold.
Previous exposed recording groups must be excluded by the pool producer.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyric_aligner.io.path_safety import validate_artifact_output_tree
from lyric_aligner.io.materializer_path_safety import declared_input_paths


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def select_recordings(pool, *, tracks=12, per_track=5, seed="outer-upgrade-20260907-v1"):
    if tracks < 2 or per_track < 1:
        raise ValueError("need at least two recording groups and one cue each")
    groups = defaultdict(list)
    ids = set()
    rank = lambda value: hashlib.sha256((seed + ":" + value).encode()).hexdigest()
    for row in pool:
        if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            raise ValueError("pool ids must be unique nonempty strings")
        ids.add(row["id"])
        group = row.get("recording_group")
        if not isinstance(group, str) or not group or not row.get("text"):
            raise ValueError("recording identity/text missing")
        for key in ("start_ms", "end_ms"):
            if isinstance(row.get(key), bool) or not isinstance(row.get(key), int) or row[key] < 0:
                raise ValueError("invalid pool timing")
        if row["end_ms"] <= row["start_ms"]:
            raise ValueError("invalid pool interval")
        groups[group].append(dict(row))
    eligible = sorted((g for g, rows in groups.items() if len(rows) >= per_track), key=rank)
    if len(eligible) < tracks:
        raise ValueError("insufficient distinct recording groups")
    selected = []
    for index, group in enumerate(eligible[:tracks]):
        partition = "holdout" if index % 3 == 0 else "calibration"
        for row in sorted(groups[group], key=lambda r: rank(r["id"]))[:per_track]:
            selected.append({**row, "partition": partition})
    return selected


def build_pack(pool_path, output):
    pool = json.loads(pool_path.read_text(encoding="utf-8-sig"))
    protected = {"pool": pool_path, **declared_input_paths({"pool": {"records": pool}})}
    validate_artifact_output_tree(inputs=protected, output_dir=output)
    selected = select_recordings(pool)
    # Validate every final recording before creating the output directory.
    audio_hashes = {}
    for row in selected:
        path = Path(row["audio_path"]).resolve()
        if path not in audio_hashes:
            audio_hashes[path] = digest(path)
        if audio_hashes[path] != row["audio_sha256"]:
            raise ValueError("pool final audio hash mismatch")
        info = sf.info(path)
        if row["end_ms"] > info.frames / info.samplerate * 1000:
            raise ValueError("pool interval extends past final audio")
    output.mkdir(parents=True, exist_ok=False)
    annotations = []
    for index, row in enumerate(selected):
        clip_name = f"clip_{index:03d}.wav"
        with sf.SoundFile(row["audio_path"]) as audio:
            sr = audio.samplerate
            start_frame = max(0, round((row["start_ms"] - 2000) * sr / 1000))
            end_frame = min(len(audio), round((row["end_ms"] + 2000) * sr / 1000))
            audio.seek(start_frame)
            data = audio.read(end_frame - start_frame, dtype="float32", always_2d=True)
        sf.write(output / clip_name, data, sr, subtype="PCM_16")
        row.update(clip=clip_name, clip_start_frame=start_frame, sample_rate=sr, clip_sha256=digest(output / clip_name))
        annotations.append({"id":row["id"], "clip":clip_name, "text":row["text"], "gold_start_ms":"", "gold_end_ms":"", "uncertainty_ms":"", "listener":"", "notes":""})
    lock = {"schema_version":"outer-validation-selection-1.0", "purpose":"pending_human_annotation_not_gold", "sampling":"seeded_recording_group_then_cue", "seed":"outer-upgrade-20260907-v1", "pool_sha256":digest(pool_path), "records":selected}
    lock["lock_sha256"] = hashlib.sha256(json.dumps(lock,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    (output / "selection.lock.json").write_text(json.dumps(lock,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (output / "annotations.csv").open("x",encoding="utf-8-sig",newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(annotations[0]))
        writer.writeheader()
        writer.writerows(annotations)
    (output / "README.txt").write_text("Pending human annotation, not ground truth.\nListen to each WAV and confirm the displayed phrase is actually performed.\nEnter start/end in milliseconds relative to the CLIP, not the full mix.\nIf identity, occurrence or clipping is ambiguous, leave timing blank and explain in notes.\nUncertainty is annotation tolerance, not statistical confidence.\nDo not inspect holdout labels while tuning. Keep recording groups separated.\n",encoding="utf-8")
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args = parser.parse_args()
    lock = build_pack(args.pool,args.output_dir)
    print(f"Locked {len(lock['records'])} clips / 120 pending boundaries; no gold generated")


if __name__ == "__main__":
    main()

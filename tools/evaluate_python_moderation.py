# SPDX-License-Identifier: GPL-3.0-only
"""Compare current Python moderation with a frozen local reference snapshot.

Research corpora, registry data and per-case predictions stay outside Git.
The source reference is opened read-only; baseline decisions are not replaced.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moderation import ModerationError, require_clean


def measures(counts):
    tp, fn, fp, tn = (counts.get(key, 0) for key in ("tp", "fn", "fp", "tn"))
    ratio = lambda x, n: round(x / n, 6) if n else None
    return {"n": tp + fn + fp + tn, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "recall": ratio(tp, tp + fn), "false_positive_rate": ratio(fp, fp + tn),
            "precision": ratio(tp, tp + fp), "f1": ratio(2 * tp, 2 * tp + fn + fp)}


def evaluate(reference, output):
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "predictions.sqlite3"
    if result_path.exists():
        raise SystemExit("Use a new output directory; prior predictions must remain intact")
    original = sqlite3.connect(f"{(reference / 'reference.sqlite3').as_uri()}?mode=ro", uri=True)
    original.row_factory = sqlite3.Row
    result = sqlite3.connect(result_path)
    result.execute("CREATE TABLE predictions(case_id TEXT PRIMARY KEY, blocked INTEGER NOT NULL)")
    grouped = defaultdict(lambda: defaultdict(int))
    total, elapsed = 0, time.perf_counter()
    pending = []
    rows = original.execute("""SELECT c.id,c.category,c.language,c.source,c.expected,c.source_label,c.text,p.blocked
        FROM cases c JOIN predictions p ON c.id=p.case_id WHERE p.engine='python' ORDER BY c.id""")
    for row in rows:
        text = " ".join(unicodedata.normalize("NFC", row["text"]).split())
        field = "name" if row["category"] in {"callsign", "portable_derived"} else "text"
        try:
            require_clean(text, field)
            blocked = False
        except ModerationError:
            blocked = True
        pending.append((row["id"], int(blocked)))
        for engine, prediction in (("baseline", bool(row["blocked"])), ("improved", blocked)):
            key = (row["category"], row["language"], row["source"], engine)
            counts = grouped[key]
            if row["expected"] is None:
                counts["total"] += 1
                counts["matched"] += prediction
            else:
                counts[("tp" if prediction else "fn") if row["expected"] else ("fp" if prediction else "tn")] += 1
        total += 1
        if len(pending) == 2000:
            result.executemany("INSERT INTO predictions VALUES (?,?)", pending)
            pending.clear()
        if total % 100000 == 0:
            print(f"Python regression comparison: {total} records", flush=True)
    result.executemany("INSERT INTO predictions VALUES (?,?)", pending)
    result.commit()
    groups = []
    for (category, lang, source, engine), counts in sorted(grouped.items()):
        groups.append({"category": category, "language": lang, "source": source, "engine": engine,
            **(dict(counts) if category == "lexicon" else measures(counts))})
    files = [ROOT / "moderation.py", ROOT / "data/moderation-words.txt", ROOT / "data/callsigns.txt"]
    report = {"reference": str(reference), "records": total, "wall_seconds": time.perf_counter() - elapsed,
              "updated_filter_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in files}, "groups": groups,
              "interpretation": "Regression reference used during development, not a new blind benchmark. "
                                "Source offence/toxicity labels are not equivalent to profanity policy."}
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    percent = lambda x: f"{x * 100:.1f}%" if x is not None else "—"
    lines = ["# Python moderation improvements", "", report["interpretation"], "",
             f"Evaluated {total:,} source records against the frozen previous Python decisions.", "",
             "| Corpus | Language | Filter | Detection | False positives |",
             "| --- | --- | --- | ---: | ---: |"]
    for group in groups:
        if group["category"] == "real_context":
            lines.append(f"| {group['source']} | {group['language']} | {group['engine']} | "
                         f"{percent(group['recall'])} | {percent(group['false_positive_rate'])} |")
    lines.extend(["", "Known callsign collisions (identifier field only):", ""])
    for group in groups:
        if group["category"] in {"callsign", "portable_derived"}:
            lines.append(f"- {group['category']}, {group['engine']}: {group['fp']}/{group['n']} rejected.")
    lines.extend(["", "The small built-in callsign exception set deliberately covers previously observed",
        "registry collisions. Its score is an intentional regression fix, not proof of generalization",
        "to all future callsigns. No exception applies to the author's chat/logbook message body.", "",
        "Vocabulary was selected from licensed word lists before this pass. Anatomical/identity terms",
        "and ordinary technical words were not copied wholesale. Increased detection can still produce",
        "additional rejections of benign profanity uses; see source-specific false-positive rates.", ""])
    (output / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps({"records": total, "report": str(output / "REPORT.md")}, indent=2), flush=True)
    original.close()
    result.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.reference.resolve(), args.output.resolve())

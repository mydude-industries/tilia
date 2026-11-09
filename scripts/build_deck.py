#!/usr/bin/env python3
"""Compress the Tilia Tarot masters (tarot/, git-ignored) into the public WebP deck (webapp/tarot/).

Dev-only; needs Pillow:  uv run --with pillow python scripts/build_deck.py tarot webapp/tarot
Re-runnable: a card whose WebP is newer than its master is skipped. Runtime code stays stdlib.
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SIZE = (600, 900)
QUALITIES = (80, 72, 64, 56, 48, 40, 32)  # brief: 80, 72, 64; the engraved masters need more steps
MAX_BYTES = 150 * 1024
BACK = "card-back"
# The Magician is selected in deck.json but missing from the masters' manifest.json.
FALLBACK = {
    "01-the-magician.png": {
        "name": "The Magician", "number": "I",
        "scene": "Robed magician under an arch of linden branches, wand raised, hand pointing to earth, "
                 "infinity sign overhead, table with cup, sword, pentacle and wand",
    },
}


@dataclass
class Job:
    id: str
    src: Path
    dst: Path
    entry: dict


def card_id(file: str) -> str:
    """The stem, minus any retry suffix: '17-the-star-v2.png' -> '17-the-star'."""
    return re.sub(r"-v\d+$", "", Path(file).stem)


def symbols(scene: str) -> list[str]:
    """The scene's things, split on commas only ("between dark and light pillars" is one)."""
    if "Rider-Waite-Smith" in scene:
        return []
    parts = [p.strip() for p in scene.split(",") if p.strip()]
    return parts[:8] if len(parts) >= 2 else []


def plan(manifest: list[dict], src: Path, dst: Path) -> tuple[list[Job], list[str]]:
    jobs, known = [], set()
    for e in manifest:
        known.add(e["file"])
        master = src / e["file"]
        if not master.is_file():
            continue
        cid = card_id(e["file"])
        out = dst / f"{cid}.webp"
        if out.is_file() and out.stat().st_mtime >= master.stat().st_mtime:
            continue
        jobs.append(Job(cid, master, out, e))
    orphans = sorted(p.name for p in src.glob("*.png") if p.name not in known)
    return jobs, orphans


def _encode(job: Job) -> None:
    from PIL import Image

    with Image.open(job.src) as im:
        im = im.convert("RGB").resize(SIZE, Image.LANCZOS)
    for q in QUALITIES:
        im.save(job.dst, "WEBP", quality=q)
        if job.dst.stat().st_size <= MAX_BYTES:
            return


def load_manifest(src: Path) -> list[dict]:
    """manifest.json (scenes, numbers) with deck.json's selected files laid over it."""
    man = json.loads((src / "manifest.json").read_text())
    by_name = {e["name"]: dict(e) for e in man}
    deck_path = src / "deck.json"
    if not deck_path.is_file():
        return list(by_name.values())
    out = []
    for sel in json.loads(deck_path.read_text()):
        e = by_name.get(sel["name"]) or {"name": sel["name"], "number": "", "scene": ""}
        e.update(FALLBACK.get(sel["file"], {}) if not e["scene"] else {})
        e["file"] = sel["file"]
        out.append(e)
    return out


def build(src: Path, dst: Path, manifest: list[dict] | None = None) -> dict:
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(src) if manifest is None else manifest
    jobs, orphans = plan(manifest, src, dst)
    for job in jobs:
        _encode(job)
    result = []
    for e in manifest:
        cid = BACK if e["file"].startswith(BACK) else card_id(e["file"])
        if (dst / f"{cid}.webp").is_file():
            result.append({"id": cid, "name": e["name"], "number": e.get("number", ""),
                           "file": f"{cid}.webp", "scene": e.get("scene", ""),
                           "symbols": [] if cid == BACK else symbols(e.get("scene", ""))})
    (dst / "manifest.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    present = sum(1 for e in manifest if (src / e["file"]).is_file())
    return {"built": len(jobs), "skipped": present - len(jobs), "orphans": orphans}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: build_deck.py <masters dir> <output dir>", file=sys.stderr)
        return 2
    r = build(Path(argv[0]), Path(argv[1]))
    print(f"built {r['built']}, skipped {r['skipped']} up to date")
    if r["orphans"]:
        print("orphans (PNGs not in the deck, not built):")
        for o in r["orphans"]:
            print(f"  {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""One-off import: place images/*.jpg into the four scenario galleries.

Grouping is by visual similarity (so a wolf's gallery holds only wolves):
  جادوگر  6  wizard + demon birds/crows
  نفرین   8  all wolves (house at the forest edge)
  بیمارستان 5 hooded ghosts / spirits
  زندان   7 skulls & skeletons

Run:  python scripts/import_images.py
Replaces the current scenario_images rows and the cover for each scenario.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.db import execute, query  # noqa: E402

# scenario_id -> ordered file list; FIRST file becomes the cover
GROUPS: dict[int, list[str]] = {
    6: [  # جادوگر — wizard silhouette first, then the mystical birds
        "4d7665854eddacbcd6d9c14c1446073d.jpg",
        "4e1a95ad919004a57acc9d1cc1d003c1.jpg",
        "3313e51ee03fc78d08ad906c71f868a3.jpg",
        "9aba00122cf65e75c527f8a55504021b.jpg",
        "d6f6820b1fd35092571f4f5e1d383ebe.jpg",
    ],
    8: [  # نفرین — wolves, forest wolf first (house at the forest edge)
        "e8f8407e79d97945847fafbafe143f22.jpg",
        "0acf0d2ce4ff423e0e701c5ee1b38d62.jpg",
        "5156e92e578f028cd4290d7931fe967f.jpg",
        "8cbd1f04f6217a491561f87726fe3f54.jpg",
        "b89f757aa22904a7abb9f03a092b12f1.jpg",
    ],
    5: [  # بیمارستان — hooded ghosts / spirits
        "16f5b4e47e711685dc1d0e004222e731.jpg",
        "09d01ad23c0e620ade16586fc98bebf2.jpg",
        "341806dd786cb2ec67f7dd48502327ed.jpg",
        "e506902bbb2a3a13fcab76e3ca6902a9.jpg",
    ],
    7: [  # زندان — skulls & skeletons
        "4164bb1e16165f0a9e69f56864961f67.jpg",
        "edb2cb76a8e6be05736ea84ff682851e.jpg",
        "5f69900703f73170856a1def93dd3a52.jpg",
    ],
}


class _Upload(io.BytesIO):
    """File-like wrapper matching what save_image() expects."""

    def __init__(self, path: Path):
        super().__init__(path.read_bytes())
        self.filename = path.name


def main() -> int:
    app = create_app()
    images_dir = ROOT / "images"

    with app.app_context():
        from app.services.images import UploadError, delete_image, save_image

        for sid, filenames in GROUPS.items():
            scenario = None
            for r in query("SELECT id, title FROM scenarios WHERE id=?", (sid,)):
                scenario = r
            if scenario is None:
                print(f"WARN: scenario {sid} not found, skipped")
                continue

            # 1. remove old gallery rows (+ uploaded files only, never /static/img)
            for old in query("SELECT path FROM scenario_images WHERE scenario_id=?", (sid,)):
                delete_image(old["path"])
            execute("DELETE FROM scenario_images WHERE scenario_id=?", (sid,))

            # 2. process + insert the new, similar group (first = cover)
            new_cover = None
            for idx, name in enumerate(filenames):
                src = images_dir / name
                if not src.is_file():
                    print(f"WARN: missing {name}")
                    continue
                try:
                    info = save_image(_Upload(src), folder="scenarios", prefix=f"s{sid}")
                except UploadError as exc:
                    print(f"FAIL {name}: {exc}")
                    continue
                execute(
                    "INSERT INTO scenario_images(scenario_id, path, alt, sort_order, is_cover) "
                    "VALUES (?,?,?,?,?)",
                    (sid, info.url, scenario["title"], idx, 1 if idx == 0 else 0),
                )
                if idx == 0:
                    new_cover = info.url
                print(f"  [{sid}] {name} -> {info.filename} ({info.width}x{info.height})")

            if new_cover:
                execute(
                    "UPDATE scenarios SET cover_image=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (new_cover, sid),
                )
                print(f"scenario {sid} '{scenario['title']}': cover -> {new_cover}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

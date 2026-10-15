"""Discover a user's flat deck without moving, renaming, or re-encoding artwork."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from app.services import tarot_assets as assets

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_TYPES = {".mp4", ".mov", ".webm"}
BACK_NAMES = {"back", "card back", "tarot back", "mat lung", "lung bai"}
EFFECT_CATEGORIES = ("CARD_FLIP", "SMOKE", "LIGHT", "PARTICLES", "TRANSITION")
MANIFEST_NAME = ".tarot-mapping.json"
# Read existing library assignments as well as the canonical Studio categories.
EFFECT_ALIASES = {
    "card flip": "CARD_FLIP", "purple smoke": "SMOKE", "smoke": "SMOKE",
    "magical light": "LIGHT", "light": "LIGHT", "gold particles": "PARTICLES",
    "particles": "PARTICLES", "transition": "TRANSITION",
}
ASSET_WARNING_PREFIXES = (
    "Không có ảnh lưng bài phù hợp;", "Chưa có ảnh/video nền;",
    "Có MP4 chưa xác định loại.", "Hiệu ứng chưa xác định loại:",
)


def current_render_warnings(report, saved_warnings):
    """Replace historical scan diagnostics with live ones; retain runtime failures."""
    runtime = [w for w in saved_warnings if not w.startswith(ASSET_WARNING_PREFIXES)]
    return list(dict.fromkeys([*report["warnings"], *runtime]))


def key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", assets.normalize(value)).strip()


def effect_category(value):
    return EFFECT_ALIASES.get(key(value)) if isinstance(value, str) else None


def classify_file(relative: str, assignment="auto") -> str | None:
    """The containing directory determines the role before names are considered."""
    path = Path(relative)
    if len(path.parts) != 2:
        return None
    directory, suffix = path.parts[0], path.suffix.lower()
    if directory == "backgrounds" and suffix in IMAGE_TYPES | VIDEO_TYPES:
        return "BACKGROUND"
    if directory == "cards" and suffix in IMAGE_TYPES:
        return "CARD_IMAGE"
    if directory == "effects" and suffix == ".mp4":
        return effect_category(path.stem if assignment == "auto" else assignment) or "UNKNOWN_EFFECT"
    return None


def resolve(root: str | Path, relative: str) -> Path:
    root = Path(root).expanduser().resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"Local asset missing or outside its directory: {relative}")
    return target


def _aliases():
    aliases = {}
    for card in assets.catalog():
        values = [card["id"], *card["aliases"], Path(card["commons_file"]).stem]
        if card["category"] == "major":
            values += [f"{card['number']:02} {name}" for name in card["aliases"]]
            values += [f"{card['number']} {name}" for name in card["aliases"]]
        else:
            values += [f"{card['suit']}{card['number']:02}", f"{card['suit']} {card['number']:02}"]
        for value in values:
            aliases[key(value)] = card["id"]
    return aliases


def read_mapping(root):
    path = Path(root).expanduser().resolve() / MANIFEST_NAME
    if not path.exists():
        return {"cards": {}, "effects": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not all(isinstance(value.get(k, {}), dict) for k in ("cards", "effects")):
            raise ValueError("mapping must contain cards/effects dictionaries")
        return value
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read .tarot-mapping.json: {exc}") from exc


def correct_mapping(root, category, filename, value):
    if category not in ("cards", "effects") or Path(filename).name != filename:
        raise ValueError("Choose a direct filename from the scanned directory")
    if category == "effects" and value is not None:
        value = effect_category(value)
        if value is None:
            raise ValueError("Unknown effect category")
    allowed = {c["id"] for c in assets.catalog()} if category == "cards" else set(EFFECT_CATEGORIES)
    if value is not None and value not in allowed:
        raise ValueError("Unknown canonical card or effect category")
    resolve(root, f"{category}/{filename}")
    mapping = read_mapping(root)
    mapping.setdefault(category, {})[filename] = value
    mapping["version"] = 1
    assets._write(Path(root).expanduser().resolve()/MANIFEST_NAME, json.dumps(mapping, ensure_ascii=False, indent=2).encode("utf-8"))


def discover(directory: str | Path) -> dict:
    # Intentionally uncached: assignments, file changes and warnings are rebuilt
    # from disk on every WebUI rerun and every batch validation.
    root = Path(directory).expanduser().resolve()
    if not (root / "cards").is_dir():
        raise ValueError(f"Missing flat cards directory: {root / 'cards'}")
    mapping = read_mapping(root)
    aliases = _aliases()
    by_id, duplicates, unmatched, invalid, ignored = {}, {}, [], {}, []
    backs = []
    metadata = {card["id"]: card for card in assets.catalog()}
    for file in sorted((root/"cards").iterdir(), key=lambda f: f.name.casefold()):
        if not file.is_file() or classify_file(f"cards/{file.name}") != "CARD_IMAGE":
            continue
        relative = f"cards/{file.name}"
        try:
            resolve(root, relative)
            with Image.open(file) as image:
                if min(image.size) < 100 or image.width * image.height > 30_000_000:
                    raise ValueError("image dimensions must be 100px–30MP")
                image.verify()
        except (OSError, ValueError) as exc:
            invalid[file.name] = str(exc)
            continue
        name = key(file.stem)
        if name in BACK_NAMES:
            backs.append(relative)
            continue
        if name in ("cover", "cover rare") and file.name not in mapping.get("cards", {}):
            ignored.append(file.name)
            continue
        if file.name in mapping.get("cards", {}):
            card_id = mapping["cards"][file.name]
            if card_id is None:
                ignored.append(file.name)
                continue
        else:
            card_id = aliases.get(name)
            if card_id is None:
                cleaned = re.sub(r"^(rws|rider waite smith|rider waite|tarot) ", "", name)
                card_id = aliases.get(cleaned)
        if card_id not in metadata:
            unmatched.append(file.name)
            continue
        if card_id in by_id:
            duplicates.setdefault(card_id, [by_id[card_id]["image_path"]]).append(relative)
        else:
            by_id[card_id] = {**metadata[card_id], "image_path": relative, "local_path": str(resolve(root, relative))}
    effects, unknown_effects = [], []
    for file in sorted((root/"effects").glob("*")):
        if not file.is_file() or file.suffix.lower() != ".mp4":
            continue
        category = mapping.get("effects", {}).get(file.name, "auto")
        category = classify_file(f"effects/{file.name}", category)
        if category in EFFECT_CATEGORIES:
            effects.append({"category": category, "path": f"effects/{file.name}", "name": file.name})
        else:
            unknown_effects.append(file.name)
    backgrounds = [f"backgrounds/{file.name}" for file in sorted((root/"backgrounds").glob("*"))
                   if file.is_file() and classify_file(f"backgrounds/{file.name}") == "BACKGROUND"]
    music = [f"music/{f.name}" for f in sorted((root/"music").glob("*")) if f.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}]
    missing = [cid for cid in metadata if cid not in by_id]
    warnings = []
    if not backs:
        warnings.append("Không có ảnh lưng bài phù hợp; dùng lưng bài tím dự phòng. Cover.jpg không được coi là lưng bài.")
    if not backgrounds:
        warnings.append("Chưa có ảnh/video nền; dùng nền khói được dựng cục bộ.")
    if unknown_effects:
        warnings.extend(f"Hiệu ứng chưa xác định loại: {root / 'effects' / name}. "
                        "Gán loại trước khi chọn sử dụng; tệp không dùng không chặn dựng video."
                        for name in unknown_effects)
    return {"root": str(root), "cards": by_id, "missing": missing, "duplicates": duplicates,
            "unmatched": unmatched, "invalid": invalid, "ignored": ignored,
            "card_backs": backs, "effects": effects, "unknown_effects": unknown_effects,
            "backgrounds": backgrounds, "music": music, "warnings": warnings,
            "complete": not missing and not duplicates and not invalid}


def validate_required(report, *, effects=(), background="", card_back="", logo="", music=""):
    """Only selected template dependencies are errors; unused files are warnings."""
    known_effects = {item["path"] for item in report["effects"]}
    for relative in effects:
        if relative not in known_effects:
            raise ValueError(f"Required effect {relative} is missing or unclassified. "
                             "Assign its category in the Studio asset panel, rescan, then retry.")
    for relative, candidates, role in (
        (background, report["backgrounds"], "background"),
        (card_back, report["card_backs"], "card back"),
    ):
        if relative and relative not in candidates:
            raise ValueError(f"Required {role} missing or in the wrong directory: {relative}")
    for relative in dict.fromkeys([*effects, background, card_back, logo, music]):
        if not relative:
            continue
        path = resolve(report["root"], relative)
        try:
            if path.suffix.lower() in VIDEO_TYPES:
                from moviepy import VideoFileClip
                with VideoFileClip(str(path), audio=False) as clip:
                    if not clip.duration or (relative in effects and clip.duration > 120):
                        raise ValueError("Effects must be at most 120 seconds; video must have a positive duration")
                    clip.get_frame(0)
            elif path.suffix.lower() in IMAGE_TYPES:
                with Image.open(path) as image:
                    image.verify()
        except Exception as exc:
            raise ValueError(f"Required asset cannot be decoded: {relative}. Replace the file and retry. {exc}") from exc


def selected_cards(report, ids):
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Choose distinct Tarot cards")
    for cid in ids:
        if cid in report["duplicates"]:
            raise ValueError(f"Duplicate image mapping for {cid}; correct the local registry first")
        if cid not in report["cards"]:
            raise ValueError(f"Missing local Tarot image: {cid}")
    return [report["cards"][cid] for cid in ids]

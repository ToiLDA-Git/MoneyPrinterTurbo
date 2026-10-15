"""Owned Tarot asset store; no provider calls during selection or rendering."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import requests
from PIL import Image

from app.utils import utils

DEFAULT_CTA_URL = "https://trai-bai-tarot.vercel.app"
EFFECT_CATEGORIES = ("card_flip", "purple_smoke", "magical_light", "particles")
USER_AGENT = "MoneyPrinterTurbo-TarotLibrary/1.0 (local asset importer; https://github.com/harry0703/MoneyPrinterTurbo)"
MAJORS = (
    ("Fool", "The Fool", "Kẻ Khờ"), ("Magician", "The Magician", "Nhà Ảo Thuật"),
    ("High Priestess", "The High Priestess", "Nữ Tư Tế"), ("Empress", "The Empress", "Hoàng Hậu"),
    ("Emperor", "The Emperor", "Hoàng Đế"), ("Hierophant", "The Hierophant", "Giáo Hoàng"),
    ("Lovers", "The Lovers", "Người Yêu"), ("Chariot", "The Chariot", "Cỗ Xe"),
    ("Strength", "Strength", "Sức Mạnh"), ("Hermit", "The Hermit", "Ẩn Sĩ"),
    ("Wheel of Fortune", "Wheel of Fortune", "Bánh Xe Số Phận"), ("Justice", "Justice", "Công Lý"),
    ("Hanged Man", "The Hanged Man", "Người Treo Ngược"), ("Death", "Death", "Cái Chết"),
    ("Temperance", "Temperance", "Tiết Độ"), ("Devil", "The Devil", "Ác Quỷ"),
    ("Tower", "The Tower", "Tòa Tháp"), ("Star", "The Star", "Ngôi Sao"),
    ("Moon", "The Moon", "Mặt Trăng"), ("Sun", "The Sun", "Mặt Trời"),
    ("Judgement", "Judgement", "Phán Xét"), ("World", "The World", "Thế Giới"),
)
RANKS = ("Ace", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Page", "Knight", "Queen", "King")
RANKS_VI = ("Át", "Hai", "Ba", "Bốn", "Năm", "Sáu", "Bảy", "Tám", "Chín", "Mười", "Tiểu Đồng", "Kỵ Sĩ", "Nữ Hoàng", "Vua")
SUITS = {"Wands": "Gậy", "Cups": "Cốc", "Swords": "Kiếm", "Pentacles": "Tiền"}
MAJOR_ALIASES = {
    0: ["Gã Khờ", "Chàng Khờ"], 1: ["Ảo Thuật Gia"], 2: ["Nữ Giáo Hoàng"],
    5: ["Đại Tư Tế"], 6: ["Tình Nhân", "Đôi Tình Nhân"], 7: ["Chiến Xa"],
    10: ["Bánh Xe Vận Mệnh"], 12: ["Người Bị Treo"], 13: ["Tử Thần"],
    14: ["Sự Điều Độ"], 20: ["Sự Phán Xét"],
}


def catalog() -> list[dict]:
    cards = []
    for number, (filename, name, vi) in enumerate(MAJORS):
        aliases = [name, vi, *MAJOR_ALIASES.get(number, [])]
        if name == "Judgement":
            aliases += ["Judgment", "The Judgement", "The Judgment"]
        if name.startswith("The "):
            aliases.append(name[4:])
        cards.append(dict(id=f"major-{number:02}", name=name, name_vi=vi,
                          category="major", suit=None, number=number, aliases=aliases,
                          commons_file=f"RWS Tarot {number:02} {filename}.jpg"))
    for suit, vi in SUITS.items():
        for number, (rank, rank_vi) in enumerate(zip(RANKS, RANKS_VI), 1):
            name = f"{rank} of {suit}"
            aliases = [name, f"{rank_vi} {vi}", f"{number} {vi}"]
            if suit == "Cups":
                aliases.append(f"{rank_vi} Ly")
            cards.append(dict(id=f"{suit.lower()}-{number:02}", name=name,
                              name_vi=f"{rank_vi} {vi}", category="minor", suit=suit,
                              number=number, aliases=aliases, commons_file=f"{'Pents' if suit == 'Pentacles' else suit}{number:02}.jpg"))
    return cards


def library_root() -> Path:
    return Path(utils.storage_dir("tarot", create=True)).resolve()


def asset_path(relative: str) -> Path:
    root = library_root()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Asset path must stay inside the Tarot library")
    return result


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tarot-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cards() -> list[dict]:
    result = []
    for card in catalog():
        relative = f"cards/{card['id']}.png"
        metadata = asset_path(f"metadata/{card['id']}.json")
        provenance = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
        result.append({**card, "image_path": relative, "available": asset_path(relative).is_file(),
                       "provenance": provenance})
    return result


def save_card(card_id: str, data: bytes, provenance: dict | None = None):
    if card_id not in {card["id"] for card in catalog()}:
        raise ValueError("Unknown Tarot card")
    if len(data) > 25 * 1024 * 1024:
        raise ValueError("Card image exceeds 25 MB")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 30_000_000 or min(image.size) < 150:
            raise ValueError("Card image must have at least 150 pixels per side and at most 30 megapixels")
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG")
    encoded = output.getvalue()
    _write(asset_path(f"cards/{card_id}.png"), encoded)
    _write(asset_path(f"metadata/{card_id}.json"), json.dumps({
        **(provenance or {"source": "local upload", "license": "user supplied"}),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }, ensure_ascii=False, indent=2).encode())


def import_public_domain(progress=None) -> dict:
    """Resume safely. Verify Commons PD metadata for every image before downloading."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    imported, errors = [], {}
    for index, card in enumerate(cards()):
        if card["available"]:
            if progress:
                progress((index + 1) / 78)
            continue
        try:
            response = session.get("https://commons.wikimedia.org/w/api.php", params={
                "action": "query", "format": "json", "prop": "imageinfo", "iilimit": 1,
                "titles": "File:" + card["commons_file"], "iiprop": "url|extmetadata",
            }, timeout=30)
            response.raise_for_status()
            pages = response.json()["query"]["pages"]
            info = next(iter(pages.values()))["imageinfo"][0]
            metadata = info["extmetadata"]
            license_name = metadata.get("LicenseShortName", {}).get("value", "")
            if license_name.casefold() != "public domain" or metadata.get("Copyrighted", {}).get("value", "").lower() != "false":
                raise ValueError("Commons did not verify this image as public domain")
            from urllib.parse import urlsplit
            if urlsplit(info["url"]).hostname != "upload.wikimedia.org":
                raise ValueError("Unexpected Commons image host")
            image = session.get(info["url"], timeout=60)
            image.raise_for_status()
            save_card(card["id"], image.content, {
                "source": info["descriptionurl"], "download_url": info["url"],
                "license": license_name, "commons_metadata": metadata,
                "original_sha256": hashlib.sha256(image.content).hexdigest(),
            })
            imported.append(card["id"])
        except (requests.RequestException, ValueError, KeyError, OSError) as exc:
            errors[card["id"]] = str(exc)
        if progress:
            progress((index + 1) / 78)
    return {"imported": imported, "errors": errors}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c)))


def mentions(script: str) -> list[dict]:
    """Longest non-overlapping aliases avoid King/Queen and numeric-prefix collisions."""
    text = normalize(script)
    candidates = []
    for card in catalog():
        for alias in card["aliases"]:
            for match in re.finditer(r"(?<!\w)" + re.escape(normalize(alias)) + r"(?!\w)", text):
                candidates.append((match.start(), match.end(), card["id"]))
    accepted = []
    for start, end, card_id in sorted(candidates, key=lambda item: (-(item[1]-item[0]), item[0])):
        if not any(start < other[1] and end > other[0] for other in accepted):
            accepted.append((start, end, card_id))
    return [{"id": card_id, "position": start / max(1, len(text))}
            for start, end, card_id in sorted(accepted)]


def select_cards(script: str, manual_ids: list[str] | None = None) -> list[dict]:
    by_id = {card["id"]: card for card in cards()}
    found = mentions(script)
    # Mentioned cards always remain exact; manual selection supplies scripts without names.
    ids = list(dict.fromkeys([m["id"] for m in found] if found else (manual_ids or [])))
    if not ids:
        raise ValueError("No Tarot cards identified. Use full card names or select cards manually.")
    for card_id in ids:
        if card_id not in by_id:
            raise ValueError(f"Unknown Tarot card: {card_id}")
        if not by_id[card_id]["available"]:
            raise ValueError(f"Missing image for {by_id[card_id]['name']}; import it in the Tarot library.")
    return [by_id[card_id] for card_id in ids]


def effects() -> list[dict]:
    return [{"id": f"{category}/{p.name}", "category": category,
             "path": f"effects/{category}/{p.name}", "name": p.name}
            for category in EFFECT_CATEGORIES
            for p in sorted(asset_path(f"effects/{category}").glob("*.mp4"))]


def save_effect(category: str, data: bytes) -> str:
    if category not in EFFECT_CATEGORIES or not data or len(data) > 150 * 1024 * 1024:
        raise ValueError("Choose a supported effect category; MP4 limit is 150 MB")
    digest = hashlib.sha256(data).hexdigest()
    relative = f"effects/{category}/{digest}.mp4"
    target = asset_path(relative)
    _write(target, data)
    try:
        from moviepy import VideoFileClip
        with VideoFileClip(str(target), audio=False) as clip:
            if not clip.duration or clip.duration > 120:
                raise ValueError("Effects must be valid MP4 clips up to 120 seconds")
    except Exception:
        target.unlink(missing_ok=True)
        raise ValueError("Effect cannot be decoded as a short MP4 video") from None
    return f"{category}/{digest}.mp4"

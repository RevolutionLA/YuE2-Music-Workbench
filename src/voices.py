"""Local saved reference voices."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from settings import settings

_SAFE = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+")


def voices_dir() -> Path:
    path = Path(settings.voices_dir)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _catalog_path() -> Path:
    return voices_dir() / "index.json"


def _load() -> list[dict]:
    path = _catalog_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = data.get("voices") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _save(items: list[dict]) -> None:
    _catalog_path().write_text(
        json.dumps({"voices": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_voices() -> list[dict]:
    return _load()


def get_voice_by_name(name: str) -> dict:
    key = (name or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="speaker is required")
    found = None
    for item in _load():
        if item.get("name") == key or item.get("id") == key:
            found = item
    if not found:
        raise HTTPException(status_code=404, detail=f"speaker not found: {key}")
    wav = voices_dir() / found["filename"]
    if not wav.exists():
        raise HTTPException(status_code=404, detail="voice audio missing")
    return found


def get_voice(voice_id: str) -> dict:
    for item in _load():
        if item.get("id") == voice_id:
            wav = voices_dir() / item["filename"]
            if not wav.exists():
                raise HTTPException(status_code=404, detail="voice audio missing")
            return item
    raise HTTPException(status_code=404, detail="voice not found")


def read_voice_audio(voice_id: str) -> tuple[bytes, str]:
    item = get_voice(voice_id)
    path = voices_dir() / item["filename"]
    return path.read_bytes(), item.get("reference_text") or ""


def save_voice(name: str, reference_text: str, audio: bytes, suffix: str = ".wav") -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="voice name is required")
    if not audio:
        raise HTTPException(status_code=400, detail="audio is empty")
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    if ext.lower() not in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm"}:
        ext = ".wav"
    voice_id = uuid.uuid4().hex[:12]
    filename = f"{voice_id}{ext.lower()}"
    (voices_dir() / filename).write_bytes(audio)
    item = {
        "id": voice_id,
        "name": name[:80],
        "reference_text": reference_text or "",
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    items = _load()
    items.append(item)
    _save(items)
    return item


def delete_voice(voice_id: str) -> None:
    items = _load()
    found = None
    kept = []
    for item in items:
        if item.get("id") == voice_id:
            found = item
        else:
            kept.append(item)
    if not found:
        raise HTTPException(status_code=404, detail="voice not found")
    path = voices_dir() / found["filename"]
    if path.exists():
        path.unlink()
    _save(kept)

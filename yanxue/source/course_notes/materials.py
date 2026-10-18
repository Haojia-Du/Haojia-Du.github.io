"""Expose only files already imported into the selected lecture."""
from __future__ import annotations

import json
from pathlib import Path

from .core import DOCUMENT_EXTENSIONS, MEDIA_EXTENSIONS, PipelineError


def lecture_files(lecture: Path) -> list[Path]:
    source = lecture / "source"
    paths = sorted(path.resolve() for path in source.rglob("*") if path.is_file()) if source.exists() else []
    refs = source / ".external-sources.json"
    if refs.exists():
        paths += [Path(value).resolve() for value in json.loads(refs.read_text(encoding="utf-8")) if Path(value).is_file()]
    return list(dict.fromkeys(p for p in paths if p.suffix.lower() in MEDIA_EXTENSIONS | DOCUMENT_EXTENSIONS))


def file_at(lecture: Path, index: int) -> Path:
    paths = lecture_files(lecture)
    if index < 0 or index >= len(paths):
        raise PipelineError("材料不存在，请刷新学习台。")
    return paths[index]


def material_data(lecture: Path) -> dict:
    evidence = lecture / "extracted" / "evidence.json"
    return {"files": [{"id": i, "name": p.name, "suffix": p.suffix.lower(),
                       "kind": "media" if p.suffix.lower() in MEDIA_EXTENSIONS else "document"}
                      for i, p in enumerate(lecture_files(lecture))],
            "evidence": json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else []}

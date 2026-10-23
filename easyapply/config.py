from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import Behavior, Config, ExperienceLevel, SearchCriteria, Uploads


def _parse_experience_levels(levels: Optional[List[int]]) -> List[ExperienceLevel]:
    if not levels:
        return []
    out: List[ExperienceLevel] = []
    for lv in levels:
        try:
            out.append(ExperienceLevel(int(lv)))
        except Exception:
            continue
    return out


def load_config(config_path: Path) -> Config:
    with config_path.open("r") as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}

    uploads_raw = raw.get("uploads", {}) or {}
    resume_val = uploads_raw.get("Resume")
    resume_path: Any
    if isinstance(resume_val, list):
        resume_path = [Path(p).expanduser() for p in resume_val if p]
    elif resume_val:
        resume_path = Path(resume_val).expanduser()
    else:
        resume_path = None
    cover_path_val = uploads_raw.get("Cover Letter") or uploads_raw.get("Cover_Letter")
    cover_path = Path(cover_path_val).expanduser() if cover_path_val else None
    if not resume_path:
        raise ValueError("uploads.Resume is required in config")
    uploads = Uploads(Resume=resume_path, Cover_Letter=cover_path)

    search = SearchCriteria(
        # positions=[p for p in raw.get("positions", []) if p],
        # locations=[l for l in raw.get("locations", []) if l],
        query=str(raw.get("query", "")),
        search_url=str(raw.get("search_url", "")),
        experience_levels=_parse_experience_levels(raw.get("experience_level")),
        time_posted=raw.get("time_posted"),
        blacklisted_companies=[c for c in raw.get("blacklist", []) if c],
        blacklisted_title_words=[w for w in raw.get("blackListTitles", []) if w],
    )

    dl_raw = raw.get("daily_limit")
    daily_limit = int(dl_raw) if isinstance(dl_raw, (int, str)) and str(dl_raw).isdigit() else None

    behavior = Behavior(
        daily_limit=daily_limit,
        min_delay=float(raw.get("min_delay", 1.5)),
        max_delay=float(raw.get("max_delay", 3.0)),
        headless=bool(raw.get("headless", False)),
    )


    output_filename = raw.get("output_filename", "output.csv")

    cfg = Config(
        test=bool(raw.get("test", False)),
        username=str(raw.get("username", "")),
        password=str(raw.get("password", "")),
        phone_number=str(raw.get("phone_number", "")),
        location=str(raw.get("location", "")),
        image=str(raw.get("image", "")),
        salary=str(raw.get("salary", "")),
        context_files=[Path(p).expanduser() for p in raw.get("context_files", [])],
        uploads=uploads,
        search=search,
        behavior=behavior,
        output_filename=output_filename,
    )

    if not cfg.username or not cfg.password or not cfg.phone_number:
        raise ValueError("username, password and phone_number are required in config")
    if not cfg.search.query:
        raise ValueError("query must be a non-empty string")

    return cfg

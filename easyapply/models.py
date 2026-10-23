from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from pydantic import BaseModel


class ExperienceLevel(IntEnum):
    ENTRY = 1
    ASSOCIATE = 2
    MID_SENIOR = 3
    DIRECTOR = 4
    EXECUTIVE = 5
    INTERNSHIP = 6


@dataclass(slots=True)
class Uploads:
    # Allow either a single resume path or a list of resume files to choose from.
    Resume: Union[Path, List[Path]]
    Cover_Letter: Optional[Path] = None


@dataclass(slots=True)
class Behavior:
    daily_limit: Optional[int] = None
    min_delay: float = 1.5
    max_delay: float = 3.0
    headless: bool = False


@dataclass(slots=True)
class SearchCriteria:
    query: str = field(default_factory=str)
    experience_levels: List[ExperienceLevel] = field(default_factory=list)
    time_posted: Optional[str] = None  # hour|day|week|month
    blacklisted_companies: List[str] = field(default_factory=list)
    blacklisted_title_words: List[str] = field(default_factory=list)  # Unused
    search_url: str = ""


@dataclass(slots=True)
class Config:
    test: bool
    username: str
    password: str
    phone_number: str
    location: str
    image: str
    salary: str
    uploads: Uploads
    search: SearchCriteria
    behavior: Behavior = field(default_factory=Behavior)
    output_filename: str = "output.csv"
    context_files: List[Path] = field(default_factory=list)


class AnswerManyOutput(BaseModel):
    answers: List[str | None]


class ChooseResumeOutput(BaseModel):
    is_apply: bool
    chosen_resume: str | None

from __future__ import annotations

import logging
import re
import time
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd


class AnswerStrategy(Protocol):
    @abstractmethod
    def answer(self, question: str, options: list[str] = [], error: str | None = None, job_description: str | None = None) -> str:
        """Return an answer to a single normalized question.

        error: optional field validation error or hint text to guide answering.
        job_description: optional job description text context to guide relevance.
        """
        ...

    @abstractmethod
    def answer_many(self, questions: list[tuple[str, list[str], str | None]], jid: int, job_description: str | None = None, form_raw_html: str | None = None) -> list[str]:
        """Batch answer multiple questions.

        Each tuple contains: (question_text, options_list, error_text_or_None).
        job_description provides shared context across all questions.
        Implementations MAY fallback to calling answer() repeatedly.
        """
        ...

    @abstractmethod
    def choose_resume(self, job_description: str | None, resume_paths: list[str]) -> str:
        """Given a job description and a list of candidate resume paths, return the chosen path.

        Implementations may use heuristics or LLMs. Must return one of the provided paths.
        """
        ...


@dataclass(slots=True)
class SimpleAnswerStrategy(AnswerStrategy):
    salary: str
    qa_file: Path
    logger: logging.Logger

    def __post_init__(self) -> None:
        if not self.qa_file.exists():
            df = pd.DataFrame(columns=["Question", "Answer"])  # type: ignore
            df.to_csv(self.qa_file, index=False, encoding="utf-8")

    def answer(self, question: str, jid: int, options: list[str] = [], error: str | None = None, job_description: str | None = None) -> str:
        q = question.lower().strip()
        q = re.sub(r'[^a-zA-Z0-9\s]', '', q)
        ans: str
        if q.startswith("do you") or q.startswith("have you") or q.startswith("can you"):
            ans = "Yes"
        elif "how many" in q:
            ans = "1"
        elif "years" in q:
            ans = "3"
        elif "sponsor" in q or "visa" in q:
            ans = "No"
        elif "us citizen" in q:
            ans = "Yes"
        elif "are you legally" in q:
            ans = "Yes"
        elif "salary" in q:
            ans = self.salary
        elif any(k in q for k in ["gender", "race", "ethnicity", "lgbtq", "nationality", "government"]):
            ans = "Prefer not to say"
        else:
            if error:
                self.logger.info(f"Not able to answer automatically (error hint: {error}). Prompting user.")
            else:
                self.logger.info("Not able to answer question automatically. Please provide answer")
            prompt_parts = [f"Q: {question}"]
            if error:
                prompt_parts.append(f"Error: {error}")
            if job_description:
                prompt_parts.append("(Job Description context available; answer accordingly)")
            prompt_parts.append("A: ")
            ans = input("\n".join(prompt_parts))
        # append to CSV if new
            # Read existing
        try:
            df = pd.read_csv(self.qa_file)
        except Exception:
            df = pd.DataFrame(columns=["Question", "Answer"])  # type: ignore
        if not (df["Question"].str.lower() == q).any():
            df.loc[len(df)] = {"Question": q, "Answer": ans}  # type: ignore
            df.to_csv(self.qa_file, index=False, encoding="utf-8")
            self.logger.debug(f"Appended QA: {q} -> {ans}")
        return ans

    def answer_many(self, questions: list[tuple[str, list[str], str | None]], jid: int, job_description: str | None = None) -> list[str]:  # type: ignore[override]
        return [self.answer(q, jid, opts, err, job_description) for q, opts, err in questions]

    def choose_resume(self, job_description: str | None, resume_paths: list[str]) -> str:  # type: ignore[override]
        """Simple filename-based heuristic: match JD keywords against filename tokens."""
        if not resume_paths:
            raise ValueError("resume_paths must be non-empty")
        jd = (job_description or "").lower()
        # Lightweight keyword buckets
        key_groups = [
            {"data", "analytics", "analyst", "etl", "sql", "python"},
            {"backend", "api", "django", "flask", "server"},
            {"frontend", "react", "angular", "vue", "typescript", "javascript"},
            {"devops", "docker", "kubernetes", "k8s", "terraform", "ci", "cd", "aws", "azure", "gcp"},
            {"ml", "machine", "learning", "ai", "pytorch", "tensorflow"},
            {"qa", "quality", "testing", "selenium", "automation"},
        ]
        jd_tokens: set[str] = set()
        for group in key_groups:
            if any(term in jd for term in group):
                jd_tokens |= group
        import os
        import re
        best = resume_paths[0]
        best_score = -1.0
        for rp in resume_paths:
            name = os.path.splitext(os.path.basename(rp))[0].lower()
            tokens = set(re.split(r"[^a-z0-9]+", name)) - {""}
            score = float(len(tokens & jd_tokens))
            if score == 0 and "general" in tokens:
                score = 0.5
            if score > best_score:
                best = rp
                best_score = score
        self.logger.debug(f"Simple chooser selected resume '{os.path.basename(best)}' (score={best_score})")
        return best

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd  # local persistence for manual answers
from dotenv import load_dotenv
from google import genai  # type: ignore
from google.genai import types as genai_types  # type: ignore
from inputimeout import TimeoutOccurred, inputimeout

from easyapply.exceptions import ApplicationFailed, SkipApplication, Terminate
from easyapply.models import AnswerManyOutput, ChooseResumeOutput
from easyapply.qna.prompter import Prompter

from .base import AnswerStrategy

load_dotenv()


class GeminiAnswerStrategy(AnswerStrategy):
    """AnswerStrategy implementation backed by Gemini API.

    Lightweight use-case: answer application form questions succinctly.
    Falls back to SimpleAnswerStrategy heuristics for common patterns
    to save tokens. If the API is unavailable, returns a safe default.

    New behavior: If Gemini returns an answer containing 'N/A' (case-insensitive)
    we will PROMPT the user interactively instead of propagating 'N/A'. This prevents
    ambiguous / empty answers from silently being accepted.
    """

    def __init__(
            self,
            qa_file: os.PathLike[str] | str,
            logger: logging.Logger,
            model_name: str = "gemini-2.5-flash",
            temperature: float = 0.2,
        context_files: Sequence[os.PathLike[str] | str] | None = None,
        resume_files: Sequence[os.PathLike[str] | str] | None = None,
        prompter: Prompter | None = None,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        # fallback strategy always available
        # self.fallback = SimpleAnswerStrategy(salary=salary, qa_file=Path(qa_file), logger=logger)
        self.qa_file = Path(qa_file)
        self.logger = logger

        # Optional prompter for manual answers (e.g., DiscordPrompt). Should expose ask(question: str) -> str
        self.prompter = prompter

        # Process context files (text) and resume files (PDF)
        self._context_files: list[tuple[str, str]] = []  # (filename, content)
        self._resume_files: list[tuple[str, Path]] = []  # (filename, path)

        # Load context files (text files)
        if context_files:
            for cf in context_files:
                cpath = Path(cf)
                if cpath.exists() and cpath.suffix.lower() != ".pdf":
                    content = self._load_text_context(cpath)
                    if content:
                        self._context_files.append((cpath.name, content))

        # Load resume files (PDF)
        self._pdf_parts: list = []  # populated with genai_types.Part instances for PDFs
        if resume_files:
            for rf in resume_files:
                rpath = Path(rf)
                if rpath.exists() and rpath.suffix.lower() == ".pdf":
                    self._resume_files.append((rpath.name, rpath))

        self.client = genai.Client(vertexai=True, project=os.getenv("GCP_PROJECT_ID"), location="global")

        # Build binary PDF parts for resumes (if library available) AFTER client setup
        if genai_types and self._resume_files:
            for name, rpath in self._resume_files:
                if not rpath.exists():
                    continue
                data = rpath.read_bytes()
                part = genai_types.Part.from_bytes(data=data, mime_type="application/pdf")
                self._pdf_parts.append((name, part))
                self.logger.debug(f"Prepared resume PDF part '{name}' ({len(data)} bytes)")

        # Initialize cached content for prompt prefix (global context + resumes)
        self._cached_content = None
        if self.client and genai_types:
            self._init_cached_content()

    def _init_cached_content(self) -> None:
        """Initialize cached content containing global context and resume PDFs.

        This cached content will be reused across multiple API calls to reduce costs.
        The system prompt is NOT cached as it needs to be sent with each request.

        """

        if hasattr(self, "_cached_content") and self._cached_content and \
                self._cached_content.expire_time and self._cached_content.expire_time > datetime.now(timezone.utc):
            self.logger.debug("Using existing cache content")
            return

        for cached in self.client.caches.list():
            try:
                if cached.name:
                    self._cached_content = cached
                    self.logger.debug("Using existing cache content")
                    return
            except Exception as e:
                self.logger.debug(f"Failed to delete previous prompt cache: {e}")

        # Build the cached content with structured format
        cache_parts: list = []

        # Build global context text
        global_context_parts: list[str] = []

        # Add context files
        if self._context_files:
            for filename, content in self._context_files:
                global_context_parts.append(f"[START CONTEXT {filename}]\n")
                global_context_parts.append(content)
                global_context_parts.append(f"\n[END CONTEXT {filename}]\n\n")

        # Start building the full global context
        if global_context_parts or self._pdf_parts:
            global_context_text = "[START GLOBAL CONTEXT]\n\n"

            if global_context_parts:
                global_context_text += "\n".join(global_context_parts)

            # Add resume sections (PDFs will be added as parts)
            if self._pdf_parts:
                for name, part in self._pdf_parts:
                    global_context_text += f"\n[START RESUME {name}]\n"
                    cache_parts.append(genai_types.Part.from_text(text=global_context_text))
                    cache_parts.append(part)
                    global_context_text = f"\n[END RESUME {name}]\n\n"

            global_context_text += "\n\n[END GLOBAL CONTEXT]"
            cache_parts.append(genai_types.Part.from_text(text=global_context_text))

        # Only create cache if there's content to cache
        if cache_parts:
            # Create cached content using the Gemini caching API
            self._cached_content = self.client.caches.create(
                model=self.model_name,
                config=genai_types.CreateCachedContentConfig(
                    contents=cache_parts,
                    ttl="3600s"
                )
            )
            self.logger.info(f"Created prompt cache (name={self._cached_content.name})")
        else:
            self.logger.debug("No content to cache; skipping cache creation")
            self._cached_content = None

    def answer_many(self, questions: list[tuple[str, list[str], str | None]], jid: int, job_description: str | None = None, form_raw_html: str | None = None) -> list[str]:  # type: ignore[override]
        """Batch answer implementation.

        If Gemini client available: send a single prompt enumerating questions,
        return list of answers line-by-line. Falls back to per-question calls otherwise.
        """

        instruction = """
You are an assistant helping fill out job application forms.
You will be asked a series of questions related to the job application.
Answer each question based on the provided context files and resumes.
Write each answer as a string in the answer list in the order of questions. The length of the answer list should match the number of questions.
Write only the direct answer WITHOUT any prose. If unsure put null instead of a string. Null is used to signal the system to ask for user input.
Do not attempt to fabricate answers.
If information is missing or insufficient to answer (especially the 'have you?' or 'can you?' questions), set the answer to null.
Some questions might have multiple sub-questions; answer each part and add only one string to the answer list. Please indicate which part you are answering if needed.
Answer the text questions briefly and in short answers. Usually one or two words will do. No need to write in full sentences if it doesn't add clarity. 
You can elaborate in full sentences if it is an open ended question (e.g. self introduction or Describe how you used...), but make sure the answer are on-point and relevant. Do not add unnecessary details. Write no more than 150 words for open-ended questions.
The answers should look as if they are written by a human.
Avoid using buzzwords or vague language that provides no extra meaning (e.g. 'utilise', 'leverage', 'harness', 'facilitate'); 

Please use simple and clear language. Please also use UK English and wordings, e.g. 'CV' instead of 'resume', 'HR' instead of 'Hiring Manager', etc.
Make sure you pick the answer from the provided options without extra text when available.
You are given some additional context information (in text and/or PDF format) that help you understand the applicant's background.

For 'Have you used/worked with X?', 'Do you have experience with X?', 'How many years of experience do you have with X' questions:
This refers to ANY experience, including personal hobby projects, academic work, internships, or professional experience.
A similar technology or skill mentioned in the context can also be considered as a valid 'Yes' answer.
If the answer can't be determined from the provided context, do not assume the applicant has no experience with it; put null instead of 0 or 'No'.
If user started using a specific technology this year, assume 1 year of experience instead of 0.

For 'Have you used X during **work**?', 'How many year of **work** experience do you have with X?' questions:
This is different from 'Years of experience' questions; This specifically refers to professional work experience, which can't be more than the total years of experience. 
Look for the 'Work experience' sections in the context and resumes.
Again, if you can't find the answer, put null instead of 0 or 'No'. 

NEVER ASSUME ANYTHING if not provided in the context. The user is happy to provide manual answers if unsure. The user will be upset if you provide wrong or made-up answers.

Make sure the answers makes sense in the context of the question, and don't contradict with the global context.
Accept any personal data consent and background check request.

Some question text might be poorly extracted or missing; You have been provided with the raw form HTML as well to help you understand the question better.

Cover letter/open-ended question guidelines:
- Make it as easy to read as possible
- Short sentences, clear and concise
- No need to explain abbreviations or common terms
- Avoid boilerplate, buzzwords, or generic phrases
- Use the same wording/keywords as the job description where applicable
- For cover letter, follow the provided template structure


Example:
Q1: Do you have work experience with Python and data analysis? Options: Yes, No
A: Yes
(Note: This should be only based on professional work experience mentioned in the resumes or context. Do not consider personal or academic projects here.)

Q2: Have you worked with machine learning frameworks such as TensorFlow or PyTorch? Options: Yes, No
A: No
(Note: This refers to any experience with machine learning frameworks, including personal projects or academic work.)

Q3: Have you used cloud platforms like AWS or Azure in your projects? Options: Yes, No
A: Not sure
(Note: If unsure or the information isn't provided, put null to prompt user input.)

Q4: Tell me about your experience with project management and teamwork. Options: (no options. Please answer with text)
A: During my previous role, I led a team...
(Note: Not necessarily work experience; can include academic or personal projects.)

Output:

{
    "answers": ["Yes", "No", null, "During my previous role, I led a team..."]
}



"""
        lines: list[str] = [instruction.strip()]

        if job_description:
            lines.append("[START JOB DESCRIPTION]")
            lines.append(job_description[:10000])
            lines.append("[END JOB DESCRIPTION]")
        lines.append("[START QUESTIONS]")
        for idx, (q, opts, err) in enumerate(questions, start=1):
            opt_str = ", ".join(opts) if opts else "(no options. Please answer with text)"
            lines.append(f"[START Q{idx}]")
            if err:
                lines.append(f"{q}\nOptions: {opt_str}\nField Error: {err}\n")
            else:
                lines.append(f"{q}\nOptions: {opt_str}\n")
            lines.append(f"[END Q{idx}]")
        lines.append("[END QUESTIONS]")

        if form_raw_html:
            lines.append("[START RAW FORM HTML]")
            lines.append(form_raw_html)
            lines.append("[END RAW FORM HTML]")

        prompt = "\n\n".join(lines)

        # Use cached content if available, otherwise build full content
        if self._cached_content:
            self._init_cached_content()
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[genai_types.Part.from_text(text=prompt)],
                config=genai_types.GenerateContentConfig(
                    cached_content=self._cached_content.name,
                    temperature=self.temperature,
                    response_schema=AnswerManyOutput,
                    response_mime_type="application/json"
                ),
            )
        else:
            # Fallback to non-cached approach
            contents = [genai_types.Part.from_text(text=prompt)]
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=self.temperature,
                    response_schema=AnswerManyOutput,
                    response_mime_type="application/json"
                ),
            )

        answer_output = AnswerManyOutput.model_validate_json(self._extract_text(response))
        if len(answer_output.answers) != len(questions):
            self.logger.debug(f"Gemini batch mismatch answers {len(answer_output.answers)} vs questions {len(questions)}; falling back")
            raise ValueError("Mismatched answer count")

        return [
            self._prompt_user(
                question=questions[i],
                jid=jid
            ) if ans is None or 'null' in ans else ans.strip()
            for i, ans in enumerate(answer_output.answers)
        ]

    def answer(self, question: str, options: list[str] = [], error: str | None = None, job_description: str | None = None) -> str:
        raise NotImplementedError("Use answer_many for GeminiAnswerStrategy")

    def choose_resume(self, job_description: str | None, resume_paths: list[str], apply_factor: float = 0.7) -> str:  # type: ignore[override]
        """Use Gemini to select the best resume from a list.

        Attaches each resume PDF as a binary part and asks the model to choose the
        single best filename based on the job description. Falls back to simple
        heuristic or first path if API unavailable or response invalid.
        """
        # Validate inputs
        if not resume_paths:
            raise ValueError("resume_paths must be non-empty")

        # Build the resume selection prompt
        import os
        basenames = [os.path.basename(p) for p in resume_paths]

        instruction = f"""
You are an assistant that helps select the best resume for a job application.
Based on the job description, and the previously provided global context (which includes information about the applicant, and their resume PDFs),
determine whether to apply to this job or skip it.
If you decide to apply, choose the single BEST FIT resume PDF and set chosen_resume to its filename.
Do not choose the other context files that are not resumes.
If you decide to skip the application, set is_apply to false, and chosen_resume to null. No explanations.
The applicant does not wish to apply to every job; only the ones that match their skills and experience, and don't require way more qualifications than they have.
The applicant aims to apply to approximately {apply_factor:.0%} of the jobs that are the best matches for the applicant.
That means you should choose a resume {apply_factor:.0%} of the time, and skip the application {1 - apply_factor:.0%} of the time.
Carefully review the job description requirements to make your decision. Some job titles may be misleading.
Please also skip unpaid or volunteer positions, or anything that is not a proper job.

Example Output (Apply with My_resume.pdf):
{{
    "is_apply": true,
    "chosen_resume": "My_resume.pdf"
}}

Example Output (Skip application):
{{
    "is_apply": false,
    "chosen_resume": null
}}
"""
        prompt_parts: list[str] = [instruction.strip()]

        if job_description:
            prompt_parts.append(f"\n[START JOB DESCRIPTION]\n{job_description[:10000]}\n[END JOB DESCRIPTION]")

        # prompt_parts.append("\n[RESUME FILENAMES]\n" + "\n".join(basenames) + "\n[/RESUME FILENAMES]")

        prompt_text = "\n".join(prompt_parts)

        # Use cached content if available

        if self._cached_content:
            self._init_cached_content()
            # Build contents with just the prompt (resume PDFs are already in applicant context)
            contents = [genai_types.Part.from_text(text=prompt_text)]
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    cached_content=self._cached_content.name,
                    temperature=self.temperature,
                    response_schema=ChooseResumeOutput,
                    response_mime_type="application/json"
                ),
            )
        else:
            # Fallback to non-cached approach
            contents: list = [genai_types.Part.from_text(text=prompt_text)]
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=self.temperature,
                    response_schema=ChooseResumeOutput,
                    response_mime_type="application/json"
                ),
            )

        self.log_output(response)
        resume_output = ChooseResumeOutput.model_validate_json(self._extract_text(response))

        if not resume_output.is_apply or resume_output.chosen_resume is None:
            self.logger.debug("Gemini choose_resume decided to skip application")
            raise SkipApplication()

        if resume_output.chosen_resume in basenames:
            idx = basenames.index(resume_output.chosen_resume)
            return str(resume_paths[idx])

        lower_map = {b.lower(): i for i, b in enumerate(basenames)}
        if resume_output.chosen_resume and resume_output.chosen_resume.lower() in lower_map:
            return str(resume_paths[lower_map[resume_output.chosen_resume.lower()]])
        # Try partial contains
        for i, b in enumerate(basenames):
            if resume_output.chosen_resume and resume_output.chosen_resume.lower() in b.lower():
                return str(resume_paths[i])
        self.logger.debug(f"Gemini choose_resume returned unrecognized value: {resume_output.chosen_resume!r}; using fallback")
        return self.fallback.choose_resume(job_description, resume_paths)  # type: ignore[arg-type]

    # Internal helpers
    def _extract_text(self, response) -> str:
        candidate = getattr(response, "candidates", [None])[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", []) if content else []
        texts = []
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                texts.append(t)
        return "\n".join(texts)

    def _load_text_context(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        cleaned = raw.strip()
        self.logger.debug(f"Loaded text context '{path}' ({len(cleaned)} chars)")
        return cleaned

    # Manual prompt & persistence similar to SimpleAnswerStrategy's path
    def _prompt_user(self, question: tuple[str, list[str], str | None], jid: int) -> str:
        """Prompt the user for an answer, record to fallback QA CSV.

        We intentionally bypass heuristic auto-answers here to ensure
        the user provides a definitive response when Gemini is unsure.
        """
        ans: str | None = None
        # Try primary prompter if provided
        if self.prompter is not None and hasattr(self.prompter, "ask"):
            ans = self.prompter.ask(
                (f'Question from https://www.linkedin.com/jobs/view/{jid}\n' if jid else '') +
                f'**{question[0]}**\n' +
                (f'Options: {", ".join(question[1])}\n' if question[1] else '') +
                (f'({question[2]})\n' if question[2] else ''),
            )
            if ans and ans.strip():
                self.logger.debug("Primary prompter provided an answer")
            else:
                self.logger.debug("Primary prompter returned None")

        answer = ans.strip() if ans and ans.strip() else None

        if answer is None:
            raise ApplicationFailed("No answer provided for question")

        if 'skip' in answer.lower():
            raise SkipApplication()

        if 'stop' in answer.lower():
            raise Terminate()

        q_cleaned = re.sub(r'\s+', ' ', question[0].strip()).replace(",", "")
        try:
            df = pd.read_csv(self.qa_file)
        except Exception:
            df = pd.DataFrame(columns=["Question", "Answer"])  # type: ignore
        df.loc[len(df)] = {"Question": q_cleaned, "Answer": answer}  # type: ignore
        df.to_csv(self.qa_file, index=False, encoding="utf-8")
        self.logger.debug(f"Appended manual QA: {q_cleaned} -> {answer}")
        return answer

    def log_output(self, output: genai_types.GenerateContentResponse) -> None:
        text = self._extract_text(output)
        with open("gemini_output.log", "a", encoding="utf-8") as f:
            f.write(text)
            f.write(f"\n[Thoughts token count: {output.usage_metadata.thoughts_token_count if output.usage_metadata and hasattr(output.usage_metadata, 'thoughts_token_count') else 'N/A'}]\n")
            f.write(f"[Output token count: {output.usage_metadata.candidates_token_count if output.usage_metadata and hasattr(output.usage_metadata, 'candidates_token_count') else 'N/A'}]\n")
            f.write("\n" + "="*80 + "\n")


__all__ = ["GeminiAnswerStrategy"]

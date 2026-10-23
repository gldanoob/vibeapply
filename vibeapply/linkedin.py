from __future__ import annotations

import logging
import os
import random
import re
import time
import urllib.parse
from csv import writer
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from seleniumbase import BaseCase
from seleniumbase.common.exceptions import NoSuchElementException

from vibeapply.exceptions import (ApplicationFailed, ReachedLimit,
                                  SkipApplication, Terminate)
from vibeapply.qna.prompter import Prompter

from .models import Behavior, Config, ExperienceLevel, SearchCriteria
from .qna.base import AnswerStrategy
from .selectors import *


class ApplyClient:
    def __init__(self, driver: BaseCase, logger: logging.Logger, cfg: Config, answerer: AnswerStrategy, notifier: Optional[Prompter] = None) -> None:
        self.driver = driver
        self.log = logger
        self.cfg = cfg
        self.answerer = answerer
        # Optional notifier (e.g., DiscordPrompt or StdioPrompter) exposing send(message:str)
        self.notifier = notifier
        # Holds Q/A pairs for the current application
        self.current_answers: List[Tuple[str, str]] = []

    # ---------- Auth ----------
    def login(self) -> None:
        # Check if already logged in
        self.driver.open("https://www.linkedin.com/")
        self.driver.wait_for_ready_state_complete()

        if "/feed" in self.driver.get_current_url():
            self.log.info("Already logged in to LinkedIn")
            return

        self.log.info("Logging in to LinkedIn")
        self.driver.open("https://www.linkedin.com/login?fromSignIn=true&trk=guest_homepage-basic_nav-header-signin")
        self.driver.wait_for_ready_state_complete()

        self.driver.type("#username", self.cfg.username)
        time.sleep(1.5)
        self.driver.type("#password", self.cfg.password)
        time.sleep(1.0)
        self.driver.click('button[type="submit"]')
        time.sleep(6)
        if "/checkpoint/challenge" in self.driver.get_current_url():
            self.log.info("Login requires verification step. Please complete it in the browser.")
            input("Press Enter to continue after completing verification...")
        self.log.info("Login complete")

    # ---------- Search helpers ----------
    def _experience_param(self, levels: List[ExperienceLevel]) -> str:
        if not levels:
            return ""
        joined = ",".join(str(int(lv)) for lv in levels)
        return f"&f_E={joined}"

    def _time_param(self, time_posted: str | None) -> str:
        if not time_posted:
            return ""
        tmap = {"hour": "r3600", "day": "r86400", "week": "r604800", "month": "r2592000"}
        key = time_posted.lower()
        return f"&f_TPR={tmap[key]}" if key in tmap else ""

    def _search_url(self, search: SearchCriteria, start: int) -> str:
        url = (
            f"https://www.linkedin.com/jobs/search-results/?f_AL=true&geoId=102299470&keywords={urllib.parse.quote(search.query)}&start={start}"
            + self._time_param(self.cfg.search.time_posted)
            + self._experience_param(self.cfg.search.experience_levels)
        ) if not self.cfg.search.search_url else self.cfg.search.search_url
        self.log.debug(f"Search URL: {url}")
        return url

    def _human_sleep(self, behavior: Behavior) -> None:
        delay = random.uniform(behavior.min_delay, behavior.max_delay)
        self.log.debug(f"Sleeping {delay:.2f}s")
        time.sleep(delay)

    def _with_url_params(self, url: str, params: Dict[str, str]) -> str:
        url_parts = list(urllib.parse.urlparse(url))
        query = dict(urllib.parse.parse_qsl(url_parts[4]))
        query.update(params)
        url_parts[4] = urllib.parse.urlencode(query)
        return urllib.parse.urlunparse(url_parts)

    # ---------- Main loop ----------
    def run_for(self, limit: int | None = None, test_job: int | None = None) -> int:
        if self.notifier:
            self.notifier.send(f"Starting job search with limit = {limit}...")
        self.log.info(f"Searching jobs with query '{self.cfg.search.query}'")
        applied_count = 0
        start = 0
        self.driver.set_window_rect(1, 1, 1920, 1080)
        self.driver.open(self._search_url(self.cfg.search, start))

        self.driver.wait_for_ready_state_complete()
        self.log.info("Looking for jobs.. Please wait..")

        if test_job is not None:
            self.log.info(f"Test job ID provided ({test_job}); applying only to this job")
            url = self.driver.get_current_url()
            test_url = self._with_url_params(url, {"currentJobId": str(test_job)})
            self.driver.open(test_url)
            self.driver.wait_for_ready_state_complete()
            self._apply_to_job(str(test_job))
            return 1

        try:
            while True:
                self._human_sleep(self.cfg.behavior)
                # scroll results container
                results = self.driver.find_elements("*[componentkey='SearchResultsMainContent']")
                if results:
                    for i in range(300, 3000, 150):
                        self.driver.execute_script("arguments[0].scrollTo(0, arguments[1]);", results[0], i)
                        time.sleep(0.05)
                # collect job IDs
                # job_cards = self.driver.find_elements(JOB_LINKS)
                job_cards = self.driver.find_elements("*[data-view-name='job-search-job-card']")
                self.log.debug(f"Found {len(job_cards)} job cards on page start={start}")

                for card in job_cards:
                    # Scroll

                    if 'Applied' in card.text:
                        self.log.debug("Skipping card marked Applied")
                        continue
                    if 'Easy Apply' not in card.text:
                        self.log.debug("Skipping as job has no easy apply")
                        continue

                    self._human_sleep(self.cfg.behavior)
                    button = card.find_element(By.CSS_SELECTOR, "div[role='button']")
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", button)
                    button.click()
                    current_url = self.driver.get_current_url()
                    jid_match = re.search(r'currentJobId=(\d+)', current_url)
                    if not jid_match:
                        self.log.debug("Could not extract job id from URL; skipping")
                        continue
                    jid = jid_match.group(1)

                    if os.path.exists("skipped_jobs.txt"):
                        with open("skipped_jobs.txt", "r") as f:
                            skipped_jobs = f.read().splitlines()
                        if jid in skipped_jobs:
                            self.log.debug(f"Skipping previously skipped job id={jid}")
                            continue

                    try:
                        self._apply_to_job(jid)
                        applied_count += 1
                        if limit is not None and applied_count >= limit:
                            self.log.debug("Page application limit reached")
                            raise ReachedLimit()
                    except ApplicationFailed as e:
                        self.log.debug(f"Failed to apply to job: {e}")
                        if self.notifier:
                            self.notifier.send(f"Please apply manually to https://www.linkedin.com/jobs/view/{jid}")

                        self._close_application()
                    except SkipApplication:
                        # Remember skipped applications to avoid reattempting
                        with open("skipped_jobs.txt", "a") as f:
                            f.write(f"{jid}\n")

                        self._close_application()
                        self.log.debug("Skipped application")
                    except (ReachedLimit, Terminate) as e:
                        raise e
                    except Exception as e:
                        self.log.debug("Unexpected error during application", exc_info=True)
                        if self.notifier:
                            self.notifier.send(f"Error applying to https://www.linkedin.com/jobs/view/{jid}: {e}")

                        self._close_application()
                # paginate
                start += 25
                if start > 500:  # guardrail to avoid infinite loops
                    self.log.info("Reached pagination guardrail; stopping this search combo")
                    break
                self.driver.get(self._search_url(self.cfg.search, start))
                self.driver.wait_for_ready_state_complete()
        except ReachedLimit:
            self.log.info("Reached application limit; stopping")
        except Terminate:
            self.log.info("Termination requested; stopping application process")
        return applied_count

    def _apply_to_job(self, job_id: str) -> None:

        if self.driver.is_element_present(ERROR) and 'limit' in self.driver.get_text(ERROR).lower():
            self.log.info("Reached application limit for today; skipping")
            raise ReachedLimit()

        if self.driver.get_text(APPLY_BUTTON) != "Easy Apply":
            self.log.info("Job is not Easy Apply; skipping")
            raise ApplicationFailed("Job is not Easy Apply")

        job_title = self.driver.get_text(JOB_TITLE)
        job_company = self.driver.get_text(JOB_COMPANY)

        self.log.debug(f"Preparing to apply to job #{job_id}: {job_title} at {job_company}")

        for company in self.cfg.search.blacklisted_companies:
            if company.lower() in job_company.lower():
                self.log.info("Company blacklisted")
                raise SkipApplication()

        self.current_job_id = job_id
        self.current_job_description = self.driver.get_attribute(JOB_DESC, "textContent") or ""
        self.log.debug(f"Job description length: {len(self.current_job_description)} characters")

        # Read job description and choose whether to apply
        resume_path = self._choose_resume_for_job()

        self.log.debug(f"Applying to job #{job_id}: {job_title} with resume {resume_path}")

        self.driver.click(APPLY_BUTTON)
        self.log.info("Clicked Easy Apply button")
        self._human_sleep(self.cfg.behavior)

        if self.driver.is_element_present(LIMIT_REACHED_DIALOG):
            dialog_text = self.driver.get_text(LIMIT_REACHED_DIALOG)
            if 'apply limit' in dialog_text.lower():
                self.log.info("Reached application limit for today")
                raise ReachedLimit()

            if 'safety reminder' in dialog_text.lower():
                self.driver.click(APPLY_BUTTON)
                self._human_sleep(self.cfg.behavior)

        self.current_answers = []
        self._fill_info()
        self._submit_application(resume_path=resume_path)

        self._notify_application_result(job_title, job_id, job_company)

        # log
        self._write_result(job_id, job_title, job_company)
        # Notify user about application outcome and answers
        self.log.info("Application Submitted")

    def _fill_info(self) -> None:
        if self.driver.is_element_present(SHADOW_ROOT + 'input[id*="phone"]') and self.cfg.phone_number:
            self.driver.type(SHADOW_ROOT + 'input[id*="phone"]', self.cfg.phone_number)
        if self.driver.is_element_present(SHADOW_ROOT + 'input[id*="location"]') and self.cfg.location:
            self.driver.type(SHADOW_ROOT + 'input[id*="location"]', self.cfg.location + Keys.ESCAPE)
        if self.driver.is_element_present(SHADOW_ROOT + 'input[accept*="image"]') and self.cfg.image:
            self.driver.show_file_choosers()
            self.driver.choose_file(SHADOW_ROOT + 'input[accept*="image"]', self.cfg.image)

    def _submit_application(self, resume_path: str) -> None:
        for _ in range(20):
            self._human_sleep(self.cfg.behavior)

            if self.driver.is_element_present(UPLOAD_RESUME_INPUT):
                self.driver.show_file_choosers()
                self.driver.choose_file(UPLOAD_RESUME_INPUT, resume_path)
                self.log.debug("Uploaded resume")

            if self.driver.is_element_present(SHADOW_ROOT + "textarea") and (shadow_root := self.get_shadow_root()):
                text_areas = shadow_root.find_elements(By.CSS_SELECTOR, "textarea")
                for ta in text_areas:
                    ta.clear()
                self.log.debug("Cleared text area")

            # if self.driver.is_element_present(UPLOAD_COVER_LETTER_INPUT):
            #     self.driver.clear(UPLOAD_COVER_LETTER_INPUT)
            #     self.log.debug("Cleared cover letter")

            if self.driver.is_element_present(FOLLOW) and self.driver.get_attribute(FOLLOW, "checked") == "true":
                self.driver.js_click(FOLLOW)
                self.log.debug("Toggled Follow company")

            if self.driver.is_element_present(SUBMIT):
                self._human_sleep(self.cfg.behavior)
                if self.cfg.test:
                    self.log.info("Test mode enabled, closing application without submitting")
                    self._close_application()
                else:
                    self.driver.click(SUBMIT)
                    self.driver.wait_for_ready_state_complete()
                    self._human_sleep(self.cfg.behavior)
                    self.driver.click(CLOSE)
                    self.log.info("Submitted application and closed form")
                return

            if self.driver.is_element_present(FIELD_ERROR):
                self.log.info("Form has validation errors; attempting to answer questions")
                self._process_questions()
                if "application was sent" in self.driver.get_page_source():
                    self.log.info("LinkedIn confirms application was sent")
                    return

            if self.driver.is_element_present(REVIEW):
                self.driver.click(REVIEW)
                self.log.debug("Clicked Review")
                continue

            if self.driver.is_element_present(NEXT):
                self.driver.click(NEXT)
                self.log.debug("Clicked Next")
                continue

            # nothing actionable; bail
            break

        raise ApplicationFailed("Could not complete application form")

    def _close_application(self) -> None:
        if self.driver.is_element_visible(CLOSE):
            self.driver.click(CLOSE)
            self.driver.click(DISCARD_BUTTON)
            self.log.info("Closed application form")

    def _choose_resume_for_job(self) -> str:
        """Pick a resume file path based on job description and available resumes.

        If a single path is configured, return it. If a list is provided, pick the
        one with the best keyword overlap between the job description and the
        resume file name. Falls back to the first in the list if scores tie or
        job description is unavailable.
        """
        import os
        jd_full = getattr(self, 'current_job_description', '') or ''
        resumes = self.cfg.uploads.Resume
        # Single path
        if not isinstance(resumes, list):
            return os.path.abspath(str(resumes))

        chosen = self.answerer.choose_resume(jd_full, [str(p) for p in resumes])  # type: ignore[attr-defined]
        if not chosen or chosen.lower() == "none":
            raise SkipApplication("Answer strategy chose to skip application")
        self.log.info(f"Selected resume via strategy: {os.path.basename(chosen)}")
        return os.path.abspath(str(chosen))

    def _get_text_fallback(self, *candidates: list[WebElement]) -> str:
        for elements in candidates:
            if elements:
                return elements[0].text.strip()
        return ""

    def get_shadow_root(self) -> WebElement | None:
        shadow_root_container = self.driver.find_elements(By.CSS_SELECTOR, "#interop-outlet")
        if not shadow_root_container:
            self.log.debug("No shadow root found for questions")
            return None
        shadow_root = shadow_root_container[0].shadow_root
        return shadow_root

    def _process_questions(self) -> None:
        shadow_root = self.get_shadow_root()
        if not shadow_root:
            self.log.debug("No shadow root; cannot process questions")
            return

        fields: list[WebElement] = shadow_root.find_elements(By.CSS_SELECTOR, FIELDS)
        entries: list[dict] = []

        form = shadow_root.find_element(By.CSS_SELECTOR, ".artdeco-modal__content form")
        form_raw_html = form.get_attribute("innerHTML") if form else ""

        for field in fields:
            field_text = field.text.strip()
            qtext = self._get_text_fallback(
                field.find_elements(By.CSS_SELECTOR, "span[aria-hidden='true']"),
                field.find_elements(By.TAG_NAME, "legend"),
                field.find_elements(By.TAG_NAME, "label")
            )
            radios = field.find_elements(By.CSS_SELECTOR, RADIO)
            options = field.find_elements(By.TAG_NAME, "option")
            checkboxes = field.find_elements(By.CSS_SELECTOR, CHECKBOX)
            error = [err.text for err in field.find_elements(By.CSS_SELECTOR, ERROR)]
            radio_values = [r.get_attribute("data-test-text-selectable-option__input") or r.get_attribute("value") or "" for r in radios]
            option_values = [opt.get_attribute("value") or "" for opt in options]
            checkbox_values = ["yes", "no"] if checkboxes else []
            all_option_values = option_values + radio_values + checkbox_values
            entries.append({
                "field": field,
                "qtext": qtext,
                "radios": radios,
                "options": options,
                "checkboxes": checkboxes,
                "error": error,
                "field_text": field_text,
                "all_values": all_option_values,
            })

        # Batch answer if strategy supports it (include error hints)
        if hasattr(self.answerer, "answer_many"):
            answer_inputs = [
                (e["qtext"], e["all_values"], "; ".join(e["error"]) or None)
                for e in entries
            ]
            answers = self.answerer.answer_many(answer_inputs,   job_description=getattr(self, 'current_job_description', None),
                                                jid=int(getattr(self, 'current_job_id', 0)), form_raw_html=form_raw_html)
        else:
            answers = [
                self.answerer.answer(
                    e["qtext"],
                    e["all_values"],
                    "; ".join(e["error"]) or None,
                    getattr(self, 'current_job_description', None),
                )  # type: ignore[arg-type]
                for e in entries
            ]

        if not answers or len(answers) != len(entries):
            self.log.info("Could not obtain answers for all questions")
            raise ApplicationFailed("Could not obtain answers for all questions")

        for entry, ans in zip(entries, answers):
            if ans == "-":
                self.log.debug("Skipping question")
                continue

            qtext = entry["qtext"]
            field = entry["field"]
            radios: list[WebElement] = entry["radios"]
            options: list[WebElement] = entry["options"]
            checkboxes: list[WebElement] = entry["checkboxes"]
            self.log.debug(f"Answering question: '{qtext}' -> '{ans}'")
            # record the answer for notification; deduplicate by question (update last)
            if qtext:
                for i, (q, _) in enumerate(self.current_answers):
                    if q == qtext:
                        self.current_answers[i] = (qtext, ans)
                        break
                else:
                    self.current_answers.append((qtext, ans))

            if options:
                for opt in options:
                    val = (opt.get_attribute("value") or "").lower()
                    if ans.lower() in val:
                        opt.click()
                        self.log.debug("Selected option from dropdown")
                        break
                else:
                    self.log.debug("No matching option found in dropdown.")
                continue

            # radio
            try:
                if radios:
                    for r in radios:
                        val = (r.get_attribute("data-test-text-selectable-option__input") or r.get_attribute("value") or "").lower()
                        if ans.lower() in val:
                            self.driver.execute_script("arguments[0].click();", r)
                            self.log.debug("Selected radio option")
                            break
                    else:
                        self.log.debug("No matching radio option found")
                    continue
            except Exception as e:
                self.log.debug("Radio answer selection failed", exc_info=True)

            # checkbox
            try:
                if checkboxes and (ans.lower() in ("yes", "no")):
                    for cb in checkboxes:
                        if cb.get_attribute("checked") != "true" and ans.lower() == "yes" \
                                or cb.get_attribute("checked") == "true" and ans.lower() == "no":
                            self.driver.execute_script("arguments[0].click();", cb)
                            self.log.debug("Selected checkbox option")
                            break
                    else:
                        self.log.debug("No matching checkbox option found")
                    continue
            except Exception as e:
                self.log.debug("Checkbox answer selection failed", exc_info=True)

            # text input fallback
            try:
                text_inputs = field.find_elements(By.CSS_SELECTOR, TEXT)
                if text_inputs:
                    text_inputs[0].clear()
                    self._simulate_typing(text_inputs[0], ans)
                    self.log.debug("Filled text input")
                    continue
            except Exception as e:
                self.log.debug("Text input answer failed", exc_info=True)

    def _simulate_typing(self, element: WebElement, text: str) -> None:
        # for char in text:
        #     print(f"Typing char: {char}")  # Debug print
        #     match char:
        #         case " ":
        #             element.send_keys(Keys.SPACE)
        #         case "/":
        #             element.send_keys(Keys.ALT, "/")
        #         case _:
        #             element.send_keys(char)
        #     time.sleep(random.uniform(0.02, 0.07))
        words = text.split('/')
        for i, word in enumerate(words):
            element.send_keys(word)
            if i < len(words) - 1:
                element.send_keys(Keys.ALT, "/")
            time.sleep(random.uniform(0.05, 0.15))

    def _notify_application_result(self, job_title: str, job_id: str, company: str) -> None:
        """Send a notification (e.g., via Discord) summarizing the application."""
        # Build message body with Q/A pairs
        lines: List[str] = [f"Submitted Application"]
        lines.append(f"**{job_title}** at **{company}**")
        lines.append(f"https://www.linkedin.com/jobs/view/{job_id}")
        if self.current_answers:
            lines.append("")
            lines.append("Answered Questions:")
            for q, a in self.current_answers:
                # Simple bullet list, trim overly long content
                q_short = (q[:300] + "…") if len(q) > 300 else q
                a_short = (a[:500] + "…") if len(a) > 500 else a
                lines.append(f"• {q_short}\n  → {a_short}\n")
        else:
            lines.append("")
            lines.append("No questions were answered for this application.")

        message = "\n".join(lines)
        # Discord has a 2000 char message limit. Truncate cautiously.
        if len(message) > 1980:
            message = message[:1980] + "\n…(truncated)"

        if self.notifier:
            self.log.debug("Sending application notification via notifier...")
            self.notifier.send(message)

    def _write_result(self, job_id: str, job_title: str, company: str) -> None:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = [timestamp, job_id, job_title, company, "Yes", "Submitted"]
        out_path = Path(self.cfg.output_filename)
        header_needed = (not out_path.exists()) or (out_path.stat().st_size == 0)
        with open(self.cfg.output_filename, 'a+', newline='') as f:
            w = writer(f)
            if header_needed:
                w.writerow(["timestamp", "job_id", "job_title", "company", "attempted", "result"])
            w.writerow(row)
        self.log.debug(f"Wrote result row: {row}")

# VibeApply Bot

Automates applying to LinkedIn Easy Apply jobs using Selenium. Configure your search and account settings once, then let it run with human-like pacing and simple auto-answers.

Note: Use responsibly and in accordance with LinkedIn's Terms of Service. This tool is provided for educational purposes.

## Features

- Modular architecture (plug in your own LLM Q&A strategy)
- CLI to initialize a config and run the bot
- Signs in to LinkedIn and searches by title, location, experience level, and time posted
- Applies only to Easy Apply postings
- Auto-fills basic questions (phone, work authorization, etc.) and uploads your resume/cover letter
- Randomized delays and active-hours window, daily limits
- Logs activity and appends applied jobs to a CSV file

- Discord bot integration to report applied jobs and prompt user for question answers before applying. (Add a timeout such that if the user does not respond/confirm in time, the application proceeds with LLM answers.)

## Requirements

- Python 3.10+ (3.11+ recommended)
- Google Chrome installed

Install dependencies using either `pyproject.toml` or `requirements.txt`.

## Quick start

1) Create a config file

```fish
python main.py init --path config.yaml
```

2) Edit `config.yaml` and fill in your LinkedIn credentials, search terms, and file paths.

Supply your own CVs and profile image; personal documents are not included. Keep
them in the ignored `personal/` directory (or outside this repository) and update
the paths in `config.yaml`. Copy `cover_letter.txt` there before personalizing it.
Keep credentials in the ignored `config.yaml` and `.env` files, never in the
tracked example config.

3) Run the bot

```fish
python main.py run --config config.yaml
```

## Configuration

See `config.example.yaml` for all available options. Key fields:

- `username`, `password`, `phone_number`
- `positions`: list of keywords
- `locations`: list of locations
- `experience_level`: list of numeric levels (1 Entry, 2 Associate, 3 Mid-Senior, 4 Director, 5 Executive, 6 Internship)
- `time_posted`: hour|day|week|month filter
- `uploads.Resume`, `uploads.Cover Letter`: absolute paths to files
	- `uploads.Resume` can also be a list of file paths. When multiple are provided, the bot will automatically choose the most relevant resume based on keywords in the job description (falling back to the first entry on ties).
- `blackListTitles`: words to skip if found in job title; `blacklist`: company names to skip
- `daily_limit`, `min_delay`, `max_delay`, `active_hours`, `headless`

Output CSV is controlled by `output_filename` and is appended to after each attempt.

## Notes on LLM-based answers

You can plug a custom answer strategy by implementing the `AnswerStrategy` Protocol (see `vibeapply/qna/base.py`). Then pass your implementation instead of `SimpleAnswerStrategy` in `main.py`. Keep secrets (API keys) in environment variables or a `.env` file.

### Resume / context ingestion

`GeminiAnswerStrategy` accepts either a single `context_file` (plain text or `.pdf`) or a list `context_files` of mixed text/PDF paths. Text files are loaded and wrapped with markers `[CTX i START] ... [CTX i END]` then merged (length capped). PDF files are no longer text‑extracted; instead their raw bytes are attached directly to the Gemini request as binary parts (`mime_type=application/pdf`) so the model can leverage the full document.

In `config.yaml` you can point `context_file` or adapt initialization code to pass `context_files=["/path/resume.pdf", "/path/skills.txt"]` when constructing `GeminiAnswerStrategy`.

## Troubleshooting

- If ChromeDriver download fails, ensure internet access and that Chrome is installed.
- If LinkedIn changes its UI, some selectors may need updates in `vibeapply/selectors.py`.
- Avoid running at inhuman speeds; keep delays and active-hours to reduce risk.

## Legal and ethical

Using automation may violate site terms. You are responsible for how you use this tool. Prefer transparency and consent, and avoid abusing platforms.

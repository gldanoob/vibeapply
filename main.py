import argparse
import os
import shutil
import time
from pathlib import Path

from dotenv import load_dotenv

from easyapply.config import load_config
from easyapply.linkedin import ApplyClient
from easyapply.logging_setup import setup_logger
from easyapply.qna.gemini import GeminiAnswerStrategy
from easyapply.qna.prompter import DiscordPrompt
from easyapply.webdriver_setup import build_chrome

load_dotenv()


def write_example_config(dest: Path) -> None:
    example = Path(__file__).parent / "config.example.yaml"
    shutil.copyfile(example, dest)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LinkedIn Easy Apply Bot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create config.yaml from example")
    p_init.add_argument("--path", default="config.yaml")
    p_init.add_argument("--force", action="store_true")

    p_run = sub.add_parser("run", help="Run bot with config.yaml")
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--headless", action="store_true")
    p_run.add_argument("--count", type=int, default=1, help="Number of applications to send")

    p_run.add_argument("--test-id", type=int, help="Run in test mode with a specific job ID")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        dest = Path(args.path)
        if dest.exists() and not args.force:
            print(f"{dest} already exists. Use --force to overwrite.")
            return 1
        write_example_config(dest)
        print(f"Wrote {dest}")
        return 0

    if args.cmd == "run":
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            print(f"Config file not found: {cfg_path}")
            return 1
        cfg = load_config(cfg_path)
        logger = setup_logger("easyapply")
        logger.info("EasyApply run starting")
        logger.info("Loaded config")
        context = build_chrome(headless=args.headless)
        with context as driver:
            try:
                prompter = DiscordPrompt(testing=cfg.test)
                logger.info("Discord prompter enabled for manual answers")

                answerer = GeminiAnswerStrategy(
                    qa_file="qna.csv" if not cfg.test else "qa-test.csv",
                    logger=logger,
                    context_files=cfg.context_files,
                    resume_files=cfg.uploads.Resume if isinstance(cfg.uploads.Resume, list) else [cfg.uploads.Resume],
                    model_name="gemini-2.5-flash",
                    prompter=prompter,
                )
                client = ApplyClient(driver, logger, cfg, answerer, notifier=prompter)
                client.login()

                client.run_for(limit=args.count, test_job=args.test_id)
            except KeyboardInterrupt:
                logger.info("Interrupted by user; exiting.")
            except Exception as e:
                logger.exception(f"Fatal error: {e}")


if __name__ == "__main__":
    raise SystemExit(main())

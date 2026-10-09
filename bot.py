#!/usr/bin/env python3
# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import logging
from pathlib import Path

from tgposter.app import PostingBot
from tgposter.config import Settings


PROJECT_DIR = Path(__file__).resolve().parent


def main() -> None:
    settings = Settings.load(PROJECT_DIR)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    PostingBot(settings).run()


if __name__ == "__main__":
    main()

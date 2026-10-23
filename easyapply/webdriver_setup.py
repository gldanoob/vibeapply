from __future__ import annotations

import os
import shutil
from typing import Any, Tuple

from selenium.webdriver.support.ui import WebDriverWait
from seleniumbase import Driver, undetected
from seleniumbase import SB



def build_chrome(headless: bool = False):
    """Build a Chrome driver using SeleniumBase Driver.

    Uses undetected-chromedriver mode and a persistent user_data_dir to reduce
    bot detection. Falls back to standard defaults where applicable.
    """
    context = SB(
        browser="chrome",
        uc=True,
        headless=headless,
        user_data_dir="./user_data",
        window_size="1920,1080" if headless else None,
        xvfb=None,
        
    )
    return context

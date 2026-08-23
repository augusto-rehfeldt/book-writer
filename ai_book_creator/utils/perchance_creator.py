"""Generate cover artwork through Perchance's browser UI."""

from __future__ import annotations

import base64
import os
from pathlib import Path


PERCHANCE_URL = "https://perchance.org/ai-text-to-image-generator"


def generate_perchance_background(
    prompt: str,
    output_path: str | os.PathLike[str],
) -> str:
    """Use visible Chrome, allowing the user to complete any site challenge."""
    try:
        from selenium import webdriver
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.select import Select
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise RuntimeError("Perchance automation needs Selenium: pip install -r requirements.txt") from exc

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    configured_profile = os.getenv("AI_BOOK_BROWSER_PROFILE", "").strip()
    profile = Path(
        configured_profile or output.parents[1] / "browser_profile" / "perchance"
    ).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    challenge_timeout = int(os.getenv("AI_BOOK_BROWSER_LOGIN_TIMEOUT", "300"))
    generation_timeout = int(os.getenv("AI_BOOK_PERCHANCE_TIMEOUT", "600"))

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    try:
        driver.set_page_load_timeout(90)
        driver.get(PERCHANCE_URL)
        print("Perchance opened in Chrome. Complete any visible verification if requested.")
        try:
            WebDriverWait(driver, challenge_timeout).until(
                lambda browser: browser.find_element(By.ID, "outputIframeEl")
            )
            driver.switch_to.frame(driver.find_element(By.ID, "outputIframeEl"))
        except TimeoutException as exc:
            raise RuntimeError(
                "Perchance did not expose its generator. Complete the visible site verification and retry."
            ) from exc

        wait = WebDriverWait(driver, 60)
        try:
            prompt_box = wait.until(
                lambda browser: next(
                    (element for element in browser.find_elements(By.TAG_NAME, "textarea") if element.is_displayed()),
                    False,
                )
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "Perchance loaded but did not expose its prompt box. Complete any visible verification and retry."
            ) from exc
        style = os.getenv("AI_BOOK_PERCHANCE_STYLE", "Cinematic").strip()
        if style:
            for element in driver.find_elements(By.TAG_NAME, "select"):
                selector = Select(element)
                match = next(
                    (option for option in selector.options if option.text.strip().lower() == style.lower()),
                    None,
                )
                if match:
                    selector.select_by_visible_text(match.text)
                    break

        baseline = set(
            driver.execute_script(
                """
                return [...document.images]
                  .filter(img => img.complete && img.naturalWidth >= 256 && img.naturalHeight >= 256)
                  .map(img => img.currentSrc || img.src);
                """
            )
        )
        prompt_box.clear()
        prompt_box.send_keys(prompt)
        wait.until(lambda browser: browser.find_element(By.ID, "generateButtonEl")).click()

        def new_image(browser):
            return browser.execute_script(
                """
                const old = new Set(arguments[0]);
                const images = [...document.images].filter(img => {
                  const src = img.currentSrc || img.src;
                  const box = img.getBoundingClientRect();
                  return src && !old.has(src) && img.complete &&
                    img.naturalWidth >= 256 && img.naturalHeight >= 256 &&
                    box.width >= 100 && box.height >= 100;
                });
                return images.length ? images[images.length - 1] : null;
                """,
                list(baseline),
            )

        try:
            image = WebDriverWait(driver, generation_timeout, poll_frequency=2).until(new_image)
        except TimeoutException as exc:
            raise RuntimeError(
                f"Perchance did not finish an image within {generation_timeout} seconds; retry the cover step."
            ) from exc

        driver.set_script_timeout(60)
        data_url = driver.execute_async_script(
            """
            const img = arguments[0], done = arguments[arguments.length - 1];
            fetch(img.currentSrc || img.src)
              .then(response => response.blob())
              .then(blob => {
                const reader = new FileReader();
                reader.onloadend = () => done(reader.result);
                reader.onerror = () => done(null);
                reader.readAsDataURL(blob);
              })
              .catch(() => done(null));
            """,
            image,
        )
        if data_url and "," in data_url:
            output.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
        else:
            output.write_bytes(image.screenshot_as_png)
        return str(output)
    finally:
        driver.quit()

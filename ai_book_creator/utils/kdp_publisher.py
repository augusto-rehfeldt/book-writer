"""Publish a prepared Kindle eBook package through KDP's web form."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


KDP_ROOT = "https://kdp.amazon.com/en_US"


class KdpPublishError(RuntimeError):
    pass


def _log(message: str) -> None:
    path = Path(os.getenv("AI_BOOK_KDP_LOG", "book_output/kdp_publish.log")).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message.replace(chr(10), ' ')}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{line}\n")


def _save(package_file: Path, package: dict, status: str, url: str = "") -> None:
    package["kdp_status"] = status
    if url:
        package["kdp_resume_url"] = url
    package["kdp_updated_at"] = datetime.now().isoformat()
    temp_file = package_file.with_suffix(package_file.suffix + ".tmp")
    temp_file.write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(package_file)
    _log(f"status={status} url={url or '-'}")


def _split_author(author: str) -> tuple[str, str]:
    parts = author.strip().rsplit(maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def _price(package: dict) -> str:
    raw = str(package.get("suggested_list_price_usd") or "0.99")
    try:
        return str(max(Decimal("0.99"), Decimal(raw)).quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise KdpPublishError(f"Invalid KDP price: {raw}") from exc


def _driver(headless: bool) -> webdriver.Chrome:
    profile = Path(
        os.getenv("AI_BOOK_KDP_PROFILE", "book_output/browser_profile/kdp")
    ).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--no-first-run")
    if headless:
        options.add_argument("--headless=new")
    try:
        return webdriver.Chrome(options=options)
    except Exception as exc:
        raise KdpPublishError(
            f"Could not open the KDP Chrome profile at {profile}. "
            "Close any Chrome window using that profile and retry."
        ) from exc


def _text(driver, css: str, value: str, timeout: int) -> None:
    element = WebDriverWait(driver, timeout).until(
        lambda current: next(
            (
                item
                for item in current.find_elements(By.CSS_SELECTOR, css)
                if item.is_displayed() and item.is_enabled()
            ),
            False,
        )
    )
    element.clear()
    element.send_keys(value)


def _click(driver, css: str, timeout: int) -> None:
    element = WebDriverWait(driver, timeout).until(
        lambda current: next(
            (
                item
                for item in current.find_elements(By.CSS_SELECTOR, css)
                if item.is_displayed() and item.is_enabled()
            ),
            False,
        )
    )
    try:
        element.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click()", element)


def _select(driver, element, value: str) -> None:
    found = any(option.get_attribute("value") == value for option in element.find_elements(By.TAG_NAME, "option"))
    if not found:
        raise KdpPublishError(f"KDP option is no longer available: {value}")
    driver.execute_script(
        """
        const setter = Object.getOwnPropertyDescriptor(
            HTMLSelectElement.prototype, "value"
        ).set;
        setter.call(arguments[0], arguments[1]);
        arguments[0].dispatchEvent(new Event("change", {bubbles: true}));
        """,
        element,
        value,
    )


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _select_label(driver, element, label: str) -> None:
    wanted = _normal(label)
    options = [
        (option, _normal(option.text))
        for option in element.find_elements(By.TAG_NAME, "option")
        if option.get_attribute("value")
    ]
    match = next((option for option, text in options if text == wanted), None)
    match = match or next(
        (
            option
            for option, text in options
            if set(wanted.split()).issubset(set(text.split()))
        ),
        None,
    )
    if match is None:
        raise KdpPublishError(f"KDP category not found: {label}")
    _select(driver, element, match.get_attribute("value"))


def _category_selects(driver) -> list:
    return [
        element
        for element in driver.find_elements(By.TAG_NAME, "select")
        if element.is_displayed()
        and any(
            re.fullmatch(r"\d{6,}", option.get_attribute("value") or "")
            or '"nodeId"' in (option.get_attribute("value") or "")
            for option in element.find_elements(By.TAG_NAME, "option")
        )
    ]


def _ai_category_choices(
    ai_service,
    package: dict,
    choices: list[str],
    count: int,
    path: list[str],
) -> list[str]:
    prompt = ai_service.build_sectioned_prompt(
        instruction=(
            f"Choose the {count} most accurate Kindle eBook categor"
            f"{'y' if count == 1 else 'ies'} from the live KDP choices below. "
            'Return only JSON in the form {"choices": [number]}. '
            "Use each number at most once; never invent or rename a choice."
        ),
        sections=[
            ("Title", str(package.get("title", ""))),
            ("Description", str(package.get("description", ""))[:1500]),
            ("Keywords", ", ".join(map(str, package.get("keywords", [])))),
            ("Current path", " > ".join(path) or "[root]"),
            (
                "Live KDP choices",
                "\n".join(f"{index}. {choice}" for index, choice in enumerate(choices, 1)),
            ),
        ],
        max_prompt_tokens=3000,
    )
    try:
        raw = ai_service.generate_content(prompt, max_completion_tokens=100)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        indexes = json.loads(match.group(0) if match else raw)["choices"]
        indexes = [int(index) - 1 for index in indexes]
        if (
            len(indexes) != count
            or len(set(indexes)) != count
            or any(index < 0 or index >= len(choices) for index in indexes)
        ):
            raise ValueError
        return [choices[index] for index in indexes]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise KdpPublishError("AI did not choose valid live KDP categories.") from exc


def _category_placements(driver) -> list[tuple[object, str]]:
    placements = []
    for label in driver.find_elements(By.TAG_NAME, "label"):
        checkboxes = label.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
        if (
            label.is_displayed()
            and label.text.strip()
            and any(checkbox.is_enabled() and not checkbox.is_selected() for checkbox in checkboxes)
        ):
            placements.append((label, label.text.strip()))
    return placements


def _ai_categories(driver, package: dict, ai_service, category_timeout: int, button) -> list[list[str]]:
    shared_path = []
    for depth in range(2):
        select = WebDriverWait(driver, category_timeout).until(
            lambda current: (
                found[depth]
                if len(found := _category_selects(current)) > depth
                else False
            )
        )
        options = [
            (option.text.strip(), option.get_attribute("value"))
            for option in select.find_elements(By.TAG_NAME, "option")
            if option.text.strip() and option.get_attribute("value")
        ]
        option_labels = list(dict.fromkeys(label for label, _ in options))
        choice = _ai_category_choices(
            ai_service,
            package,
            option_labels,
            1,
            shared_path,
        )[0]
        _select(driver, select, next(value for label, value in options if label == choice))
        shared_path.append(choice)

    placements = WebDriverWait(driver, category_timeout).until(
        lambda current: _category_placements(current) or False
    )
    leaf_labels = list(dict.fromkeys(label for _, label in placements))
    leaves = _ai_category_choices(
        ai_service,
        package,
        leaf_labels,
        min(3, len(leaf_labels)),
        shared_path,
    )
    paths = []
    for index, leaf in enumerate(leaves):
        if index:
            add = WebDriverWait(driver, category_timeout).until(
                lambda current: button(current, "add another category")
            )
            driver.execute_script("arguments[0].click()", add)
            for depth, part in enumerate(shared_path):
                selects = WebDriverWait(driver, category_timeout).until(
                    lambda current: (
                        found
                        if len(found := _category_selects(current)) > depth
                        else False
                    )
                )
                _select_label(driver, selects[depth], part)
        placement = WebDriverWait(driver, category_timeout).until(
            lambda current: next(
                (
                    label
                    for label, text in _category_placements(current)
                    if _normal(text) == _normal(leaf)
                ),
                False,
            )
        )
        driver.execute_script("arguments[0].click()", placement)
        WebDriverWait(driver, category_timeout).until(
            lambda current: any(
                checkbox.is_selected()
                for checkbox in placement.find_elements(
                    By.CSS_SELECTOR, "input[type='checkbox']"
                )
            )
        )
        paths.append([*shared_path, leaf])
    return paths


def _categories(
    driver,
    hints: list[str],
    timeout: int,
    ai_service=None,
    package: dict | None = None,
) -> list[str]:
    paths = [
        [part.strip() for part in hint.split(">") if part.strip()]
        for hint in hints
        if ">" in hint
    ]
    for path in paths:
        while path and _normal(path[0]) in {"kindle store", "kindle ebooks"}:
            path.pop(0)
    paths = [path for path in paths if path]
    if not ai_service and not paths:
        raise KdpPublishError(
            "KDP automation needs category paths such as "
            "'Science Fiction & Fantasy > Science Fiction > Dystopian'."
        )
    _click(driver, "#categories-modal-button", timeout)
    category_timeout = min(timeout, 30)

    def button(current, label):
        return next(
            (
                item
                for item in current.find_elements(By.TAG_NAME, "button")
                if item.is_displayed() and item.is_enabled() and _normal(item.text) == label
            ),
            False,
        )

    try:
        if ai_service:
            paths = _ai_categories(
                driver,
                package or {},
                ai_service,
                category_timeout,
                button,
            )
        else:
            for index, path in enumerate(paths[:3]):
                for depth, part in enumerate(path[:-1]):
                    selects = WebDriverWait(driver, category_timeout).until(
                        lambda current: (
                            found
                            if len(found := _category_selects(current)) > depth
                            else False
                        )
                    )
                    _select_label(driver, selects[depth], part)
                wanted = _normal(path[-1])
                placement = WebDriverWait(driver, category_timeout).until(
                    lambda current: next(
                        (
                            item
                            for item in current.find_elements(By.TAG_NAME, "label")
                            if item.is_displayed() and _normal(item.text) == wanted
                        ),
                        False,
                    )
                )
                driver.execute_script("arguments[0].click()", placement)
                WebDriverWait(driver, category_timeout).until(
                    lambda current: any(
                        checkbox.is_selected()
                        for item in current.find_elements(By.TAG_NAME, "label")
                        if item.is_displayed() and _normal(item.text) == wanted
                        for checkbox in item.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                    )
                )
                if index < min(len(paths), 3) - 1:
                    add = WebDriverWait(driver, category_timeout).until(
                        lambda current: button(current, "add another category")
                    )
                    driver.execute_script("arguments[0].click()", add)
    except TimeoutException as exc:
        if ai_service:
            raise KdpPublishError("KDP did not expose enough live category choices.") from exc
        raise KdpPublishError(
            f"KDP category path is incomplete: {' > '.join(path)}"
        ) from exc

    save = WebDriverWait(driver, category_timeout).until(
        lambda current: button(current, "save categories")
    )
    driver.execute_script("arguments[0].click()", save)
    WebDriverWait(driver, timeout).until(lambda current: not _category_selects(current))
    return [" > ".join(path) for path in paths[:3]]


def _errors(driver) -> str:
    messages = []
    for element in driver.find_elements(By.CSS_SELECTOR, ".a-alert-error, [role='alert']"):
        if element.is_displayed() and element.text.strip():
            messages.append(element.text.strip())
    return " | ".join(dict.fromkeys(messages))


def _continue(driver, button_css: str, next_path: str, timeout: int) -> None:
    for attempt in range(2):
        _click(driver, button_css, timeout)
        try:
            WebDriverWait(driver, min(timeout, 10)).until(
                lambda current: next_path in current.current_url or _errors(current)
            )
        except TimeoutException:
            if not attempt:
                _log(f"navigation click ignored next={next_path}; retrying")
                continue
        if next_path in driver.current_url:
            return
        raise KdpPublishError(_errors(driver) or f"KDP did not advance to {next_path}.")


def _details(driver, package: dict, timeout: int, ai_service=None) -> None:
    first_name, last_name = _split_author(str(package["author"]))
    _text(driver, "#data-title", str(package["title"]), timeout)
    _text(driver, "#data-primary-author-first-name", first_name, timeout)
    _text(driver, "#data-primary-author-last-name", last_name, timeout)

    frame = WebDriverWait(driver, timeout).until(
        lambda current: next(
            (item for item in current.find_elements(By.CSS_SELECTOR, "iframe.cke_wysiwyg_frame") if item.is_displayed()),
            False,
        )
    )
    driver.switch_to.frame(frame)
    body = driver.find_element(By.TAG_NAME, "body")
    body.click()
    body.clear()
    body.send_keys(str(package.get("description", ""))[:4000])
    driver.switch_to.default_content()

    _click(driver, "#non-public-domain", timeout)
    _click(driver, "input[name='data[is_adult_content]-radio'][value='false']", timeout)
    for index, keyword in enumerate(package.get("keywords", [])[:7]):
        _text(driver, f"#data-keywords-{index}", str(keyword), timeout)
    package["category_hints"] = _categories(
        driver,
        list(package.get("category_hints", [])),
        timeout,
        ai_service,
        package,
    )
    _continue(driver, "button#save-and-continue-announce", "/content", timeout)


def _upload_inputs(driver) -> tuple:
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
    manuscript = next(
        (
            item
            for item in inputs
            if "cover" not in f"{item.get_attribute('id')} {item.get_attribute('name')}".lower()
        ),
        None,
    )
    cover = next(
        (
            item
            for item in inputs
            if "cover" in f"{item.get_attribute('id')} {item.get_attribute('name')}".lower()
        ),
        None,
    )
    if manuscript is None or cover is None:
        raise KdpPublishError("KDP manuscript or cover upload control was not found.")
    return manuscript, cover


def _role_radio(driver, section_text: str, answer: str, timeout: int) -> None:
    wanted = _normal(answer)
    for radio in driver.find_elements(By.CSS_SELECTOR, "[role='radio']"):
        if not radio.is_displayed():
            continue
        nearby = driver.execute_script(
            """
            let node = arguments[0], answer = '', section = '';
            if (node.parentElement) answer = node.parentElement.innerText || '';
            for (let i = 0; node && i < 7; i++, node = node.parentElement) {
                section += ' ' + (node.innerText || '');
            }
            return [answer, section];
            """,
            radio,
        )
        if wanted in _normal(nearby[0]) and section_text in _normal(nearby[1]):
            if radio.get_attribute("aria-checked") == "true":
                return
            links = [
                item
                for item in radio.find_elements(By.TAG_NAME, "a")
                if item.is_displayed() and item.is_enabled()
            ]
            driver.execute_script("arguments[0].click()", links[0] if links else radio)
            try:
                WebDriverWait(driver, min(timeout, 30)).until(
                    lambda current: radio.get_attribute("aria-checked") == "true"
                )
            except TimeoutException as exc:
                raise KdpPublishError(
                    f"KDP did not accept answer '{answer}' in the {section_text} section."
                ) from exc
            return
    raise KdpPublishError(f"KDP answer '{answer}' was not found in the {section_text} section.")


def _ai_disclosure(driver, package: dict, timeout: int) -> None:
    _role_radio(driver, "artificial intelligence", "yes", timeout)

    def disclosure_selects(current):
        matches = []
        for element in current.find_elements(By.TAG_NAME, "select"):
            values = {option.get_attribute("value") for option in element.find_elements(By.TAG_NAME, "option")}
            if element.is_displayed() and (
                "ENTIRE_AND_MINIMAL" in values or "FEW_AND_MINIMAL" in values
            ):
                matches.append(element)
        return matches if len(matches) >= 3 else False

    selects = WebDriverWait(driver, timeout).until(disclosure_selects)
    _select(driver, selects[0], "ENTIRE_AND_MINIMAL")
    image_value = "FEW_AND_MINIMAL" if package.get("ai_disclosure", {}).get("images") != "None" else "NONE"
    _select(driver, selects[1], image_value)
    _select(driver, selects[2], "NONE")
    tools = package.get("ai_tools", {})
    _text(
        driver,
        "input[aria-labelledby='generative-ai-questionnaire-text-tools-prompt']",
        str(tools.get("text") or "OpenAI"),
        timeout,
    )
    if image_value != "NONE":
        _text(
            driver,
            "input[aria-labelledby='generative-ai-questionnaire-images-tools-prompt']",
            str(tools.get("images") or "AI image generator"),
            timeout,
        )


def _preview(driver, content_url: str, timeout: int) -> bool:
    original = driver.current_window_handle
    handles = set(driver.window_handles)
    _click(driver, "button#digital-preview-announce", timeout)
    WebDriverWait(driver, timeout).until(
        lambda current: set(current.window_handles) != handles or current.current_url != content_url
    )
    opened = set(driver.window_handles) - handles
    if opened:
        driver.switch_to.window(opened.pop())
    WebDriverWait(driver, timeout).until(
        lambda current: current.execute_script("return document.readyState") == "complete"
    )
    page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    if "unable to open" in page_text or "preview failed" in page_text:
        raise KdpPublishError("KDP Online Previewer could not open the uploaded book.")
    if driver.current_window_handle != original:
        driver.close()
        driver.switch_to.window(original)
        return False
    elif driver.current_url != content_url:
        driver.get(content_url)
        return True
    return False


def _content(driver, package: dict, timeout: int) -> None:
    manuscript, cover = _upload_inputs(driver)
    _log("content uploads started")
    manuscript.send_keys(str(Path(package["manuscript_file"]).resolve()))
    cover.send_keys(str(Path(package["cover_file"]).resolve()))

    def uploads_busy(current):
        text = current.find_element(By.TAG_NAME, "body").text
        return "Uploading..." in text or "Processing your file..." in text

    def uploads_ready(current):
        text = current.find_element(By.TAG_NAME, "body").text
        return (
            "File processing complete. Manuscript check complete." in text
            and "Cover uploaded successfully!" in text
            and "Uploading..." not in text
            and "Processing your file..." not in text
        )

    WebDriverWait(driver, min(timeout, 60)).until(uploads_busy)
    WebDriverWait(driver, timeout).until(uploads_ready)
    _log("content uploads processed")
    _click(driver, "input[name*='drm'][value='true']", timeout)
    _ai_disclosure(driver, package, timeout)
    confirmations = [
        item
        for item in driver.find_elements(By.CSS_SELECTOR, "[role='checkbox']")
        if item.is_displayed()
    ]
    if confirmations and confirmations[0].get_attribute("aria-checked") != "true":
        confirmations[0].click()
        WebDriverWait(driver, min(timeout, 10)).until(
            lambda current: confirmations[0].get_attribute("aria-checked") == "true"
        )
    _log("content DRM and AI disclosure configured")

    # Keep KDP's publishable "I don't know" accessibility default; do not invent claims.
    content_url = driver.current_url
    if _preview(driver, content_url, timeout):
        _ai_disclosure(driver, package, timeout)
    _log("content preview completed")
    _continue(driver, "button#save-and-continue-announce", "/pricing", timeout)


def _price_input(driver):
    for element in driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='number']"):
        marker = f"{element.get_attribute('id')} {element.get_attribute('name')}".lower()
        if element.is_displayed() and "price" in marker:
            if "[us]" in marker:
                return element
            context = driver.execute_script(
                "return arguments[0].parentElement.parentElement.parentElement.innerText || ''",
                element,
            )
            if "amazon.com" in context.lower() or "usd" in context.lower():
                return element
    return None


def _pricing(driver, package: dict, timeout: int) -> None:
    for checkbox in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
        context = driver.execute_script(
            "return arguments[0].parentElement.parentElement.innerText || ''",
            checkbox,
        )
        if "kdp select" in context.lower() and checkbox.is_selected():
            driver.execute_script("arguments[0].click()", checkbox)
    worldwide = driver.find_elements(By.CSS_SELECTOR, "#data-digital-worldwide-rights")
    if worldwide and worldwide[0].get_attribute("value") != "true":
        _click(
            driver,
            "#data-digital-worldwide-rights-accordion "
            "[data-a-accordion-row-name='on'] a[data-action='a-accordion']",
            timeout,
        )
        WebDriverWait(driver, timeout).until(
            lambda current: current.find_element(
                By.CSS_SELECTOR,
                "#data-digital-worldwide-rights",
            ).get_attribute("value")
            == "true"
        )
    royalty = WebDriverWait(driver, timeout).until(
        lambda current: next(
            (
                item
                for item in current.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='radio'][value='35_PERCENT']",
                )
                if item.is_enabled()
            ),
            False,
        )
    )
    driver.execute_script("arguments[0].click()", royalty)
    WebDriverWait(driver, timeout).until(lambda current: royalty.is_selected())

    price = WebDriverWait(driver, timeout).until(lambda current: _price_input(current))
    price_value = _price(package)
    price.clear()
    price.send_keys(price_value)
    price.send_keys("\t")
    WebDriverWait(driver, timeout).until(
        lambda current: not _errors(current)
        and "Checking cost factors..."
        not in current.find_element(By.TAG_NAME, "body").text
    )
    converted_css = "input[name$='[converted]']"

    def missing_conversions(current):
        return [
            item
            for item in current.find_elements(By.CSS_SELECTOR, converted_css)
            if "[US]" not in (item.get_attribute("name") or "")
            and item.get_attribute("value") != "true"
        ]

    def action_requests(current, action):
        return current.execute_script(
            "return performance.getEntriesByType('resource')"
            ".filter(item => item.name.includes(arguments[0])).length",
            f"/pricing/action/{action}",
        )

    missing = missing_conversions(driver)
    if missing:
        marketplaces = ", ".join(
            (item.get_attribute("name") or "").split("[amazon][", 1)[-1].split("]", 1)[0]
            for item in missing
        )
        _log(f"pricing repairing unconverted marketplaces={marketplaces}")
        _click(
            driver,
            "[data-action='potter-pricing-grid-base-all'] a",
            timeout,
        )
        WebDriverWait(driver, timeout).until(
            lambda current: not missing_conversions(current)
            and "Checking cost factors..."
            not in current.find_element(By.TAG_NAME, "body").text
        )

    _log(f"pricing validated price_usd={price_value} royalty=35_PERCENT conversions=ready")
    save_requests = action_requests(driver, "save?")
    _click(driver, "#save-announce", timeout)
    try:
        WebDriverWait(driver, timeout).until(
            lambda current: _errors(current)
            or action_requests(current, "save?") > save_requests
        )
    except TimeoutException as exc:
        raise KdpPublishError("KDP did not confirm Save as Draft.") from exc
    if error := _errors(driver):
        raise KdpPublishError(f"KDP could not save pricing: {error}")
    WebDriverWait(driver, min(timeout, 10)).until(
        lambda current: "Save Successful!"
        in current.find_element(By.TAG_NAME, "body").text
    )
    _log("pricing draft saved")

    publish_requests = action_requests(driver, "save-and-publish")
    for attempt in range(2):
        _click(driver, "button#save-and-publish-announce", timeout)
        try:
            WebDriverWait(driver, min(timeout, 10)).until(
                lambda current: "publishedId=" in current.current_url
                or _errors(current)
                or action_requests(current, "save-and-publish") > publish_requests
            )
            break
        except TimeoutException:
            if attempt:
                raise KdpPublishError("KDP ignored the Publish button click.")
            _log("pricing publish click ignored; retrying")
    _log("pricing publish request sent")
    try:
        WebDriverWait(driver, min(timeout, 60)).until(
            lambda current: "publishedId=" in current.current_url or _errors(current)
        )
    except TimeoutException as exc:
        raise KdpPublishError(_errors(driver) or "KDP did not confirm the publication submission.") from exc
    if "publishedId=" not in driver.current_url:
        raise KdpPublishError(_errors(driver) or "KDP rejected the publication submission.")


def _login(driver, headless: bool, timeout: int, url: str | None = None) -> None:
    target = url or f"{KDP_ROOT}/bookshelf"
    target_url = urlparse(target)
    driver.get(target)
    if driver.current_url == target:
        return
    if urlparse(driver.current_url).hostname != "kdp.amazon.com":
        if headless:
            raise KdpPublishError(
                "The KDP profile is not signed in. Run once with --publish-kdp --kdp-visible, sign in, and retry."
            )
        print("Sign in to KDP in the opened Chrome window; publishing will continue automatically.")
        WebDriverWait(driver, int(os.getenv("AI_BOOK_BROWSER_LOGIN_TIMEOUT", "300"))).until(
            lambda current: urlparse(current.current_url).hostname == "kdp.amazon.com"
        )
    driver.get(target)
    WebDriverWait(driver, timeout).until(
        lambda current: (
            urlparse(current.current_url).hostname,
            urlparse(current.current_url).path,
            urlparse(current.current_url).query,
        )
        == (target_url.hostname, target_url.path, target_url.query)
    )


def publish_package(
    package_path: str | os.PathLike[str],
    headless: bool = True,
    ai_service=None,
) -> dict:
    """Publish one prepared package, resuming from its last completed KDP page."""
    package_file = Path(package_path).resolve()
    package = json.loads(package_file.read_text(encoding="utf-8"))
    if package.get("kdp_status") == "submitted":
        return package
    for key in ("title", "author", "manuscript_file", "cover_file"):
        if not package.get(key):
            raise KdpPublishError(f"KDP package is missing {key}.")
    for key in ("manuscript_file", "cover_file"):
        if not Path(package[key]).is_file():
            raise KdpPublishError(f"KDP file not found: {package[key]}")

    timeout = int(os.getenv("AI_BOOK_KDP_TIMEOUT", "900"))
    _log(
        f"start title={package['title']!r} status={package.get('kdp_status', 'ready')} "
        f"browser={'headless' if headless else 'visible'}"
    )
    driver = None
    try:
        driver = _driver(headless)
        status = package.get("kdp_status", "ready")
        resume_url = package.get("kdp_resume_url", "")
        resuming = status in {"content", "pricing"} and bool(resume_url)
        _login(
            driver,
            headless,
            timeout,
            resume_url if resuming else f"{KDP_ROOT}/bookshelf",
        )
        authenticated_url = urlparse(driver.current_url)
        _log(
            f"authenticated url={authenticated_url.scheme}://"
            f"{authenticated_url.netloc}{authenticated_url.path}"
        )
        if not resuming:
            driver.get(f"{KDP_ROOT}/create")
            _click(
                driver,
                "[data-test-id='create-new-title-format-card-create-new-ebook']",
                timeout,
            )
            WebDriverWait(driver, timeout).until(lambda current: "/details" in current.current_url)
            _details(driver, package, timeout, ai_service)
            _save(package_file, package, "content", driver.current_url)

        if package.get("kdp_status") == "content":
            _content(driver, package, timeout)
            _save(package_file, package, "pricing", driver.current_url)

        if package.get("kdp_status") == "pricing":
            _pricing(driver, package, timeout)
            query = parse_qs(urlparse(driver.current_url).query)
            package["kdp_published_id"] = (query.get("publishedId") or [""])[0]
            package["kdp_submission_url"] = driver.current_url
            package["kdp_submitted_at"] = datetime.now().isoformat()
            _save(package_file, package, "submitted", driver.current_url)
            print(f"✅ Submitted to KDP at {_price(package)} USD: {package['title']}")
        return package
    except Exception as exc:
        if driver and "/title-setup/" in driver.current_url:
            try:
                _click(driver, "button#save-announce", min(timeout, 30))
                WebDriverWait(driver, min(timeout, 30)).until(
                    lambda current: "/bookshelf" in current.current_url
                    or "Save Successful!" in current.find_element(By.TAG_NAME, "body").text
                )
                _log("draft saved after publishing failure")
            except Exception as save_exc:
                _log(f"draft save after publishing failure failed: {type(save_exc).__name__}")
        detail = next(
            (line.strip() for line in str(exc).splitlines() if line.strip()),
            type(exc).__name__,
        )
        _log(
            f"failed status={package.get('kdp_status', 'ready')} "
            f"error={type(exc).__name__}: {detail}"
        )
        raise
    finally:
        if driver:
            driver.quit()

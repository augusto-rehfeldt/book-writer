from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ai_book_creator.cli import run
from ai_book_creator.core.project_manager import ProjectManager
from ai_book_creator.steps.step_5_publish import PublishStep
from ai_book_creator.utils.cover_creator import _generate_pollinations_background
from ai_book_creator.utils.kdp_publisher import (
    KdpPublishError,
    _ai_category_choices,
    _ai_disclosure,
    _category_selects,
    _click,
    _content,
    _login,
    _log,
    _price,
    _price_input,
    _preview,
    _pricing,
    _role_radio,
    _select,
    _split_author,
    publish_package,
)


class PublishStepTests(unittest.TestCase):
    def test_kdp_failure_saves_the_open_draft_before_closing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manuscript = root / "book.epub"
            cover = root / "cover.jpg"
            manuscript.write_bytes(b"book")
            cover.write_bytes(b"cover")
            package = root / "package.json"
            package.write_text(
                json.dumps(
                    {
                        "title": "Book",
                        "author": "Author",
                        "manuscript_file": str(manuscript),
                        "cover_file": str(cover),
                        "kdp_status": "pricing",
                        "kdp_resume_url": "https://kdp.amazon.com/title-setup/book/pricing",
                    }
                ),
                encoding="utf-8",
            )
            driver = unittest.mock.Mock()
            driver.current_url = "https://kdp.amazon.com/title-setup/book/pricing"
            driver.find_element.return_value.text = "Save Successful!"

            with (
                patch("ai_book_creator.utils.kdp_publisher._driver", return_value=driver),
                patch("ai_book_creator.utils.kdp_publisher._login"),
                patch(
                    "ai_book_creator.utils.kdp_publisher._pricing",
                    side_effect=KdpPublishError("rejected"),
                ),
                patch("ai_book_creator.utils.kdp_publisher._click") as click,
                patch("ai_book_creator.utils.kdp_publisher._log"),
                self.assertRaisesRegex(KdpPublishError, "rejected"),
            ):
                publish_package(package)

        click.assert_called_once_with(driver, "button#save-announce", 30)
        driver.quit.assert_called_once_with()

    def test_continuous_mode_keeps_going_after_kdp_failure(self):
        completed = unittest.mock.Mock()
        completed.create_book.return_value = True
        completed.project_manager.get_step_data.return_value = {"package_file": "book.json"}
        stopped = unittest.mock.Mock()
        stopped.create_book.return_value = False

        with (
            patch("ai_book_creator.cli.AIBookCreator", side_effect=[completed, stopped]) as creator,
            patch("ai_book_creator.cli._save_last_provider"),
            patch("ai_book_creator.cli._has_previous_generated_artifacts", return_value=False),
            patch("ai_book_creator.cli._stash_previous_ebook_files", return_value=[]),
            patch("ai_book_creator.cli._clear_project_output"),
            patch(
                "ai_book_creator.utils.kdp_publisher.publish_package",
                side_effect=KdpPublishError("rejected"),
            ),
        ):
            self.assertFalse(run("google", "auto", continuous=True, publish_kdp=True))

        self.assertEqual(creator.call_count, 2)

    def test_pollinations_is_the_default_and_is_composed_into_a_kdp_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ebook = root / "sample.epub"
            ebook.write_bytes(b"epub")
            step = PublishStep(None, object(), root)

            with (
                patch.dict(os.environ, {"AI_BOOK_MODE": "auto"}, clear=True),
                patch(
                    "ai_book_creator.utils.cover_creator._generate_pollinations_background",
                    return_value=Image.new("RGB", (600, 900), "#34506b"),
                ) as generate,
            ):
                cover = step._make_cover(ebook, "Text-free scene", "A Title", "An Author")

            self.assertTrue(Path(cover).is_file())
            generate.assert_called_once()

    def test_pollinations_background_needs_no_api_key(self):
        payload = io.BytesIO()
        Image.new("RGB", (600, 900), "#34506b").save(payload, "JPEG")
        response = unittest.mock.Mock(content=payload.getvalue())
        response.raise_for_status.return_value = None

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("ai_book_creator.utils.cover_creator.requests.get", return_value=response) as get,
        ):
            image = _generate_pollinations_background("Text-free scene")

        self.assertEqual(image.size, (600, 900))
        self.assertTrue(get.call_args.args[0].startswith("https://image.pollinations.ai/prompt/"))
        self.assertEqual(get.call_args.kwargs["headers"], {})

    def test_perchance_failure_stops_packaging_and_remains_resumable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ebook = root / "sample.epub"
            prompt = root / "sample_cover_prompt.txt"
            package = root / "sample_kdp.json"
            checklist = root / "sample_KDP_CHECKLIST.txt"
            ebook.write_bytes(b"epub")
            prompt.write_text("A text-free atmospheric scene.", encoding="utf-8")

            pm = ProjectManager(str(root))
            pm.book_data = {
                "init": {"book_title": "A Title", "author_name": "An Author"},
                "ebook": {"output_file": str(ebook), "prompt_file": str(prompt)},
            }
            step = PublishStep(None, pm, root)

            with (
                patch.dict(os.environ, {"AI_BOOK_COVER_SOURCE": "perchance"}, clear=False),
                patch(
                    "ai_book_creator.steps.step_5_publish.generate_perchance_background",
                    side_effect=RuntimeError("generator unavailable"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Perchance cover generation failed"):
                    step.execute()

                self.assertFalse(package.exists())
                package.write_text("{}", encoding="utf-8")
                checklist.write_text("", encoding="utf-8")
                pm.book_data["publishing"] = {
                    "completed": True,
                    "package_file": str(package),
                    "checklist_file": str(checklist),
                    "cover_file": "",
                    "source_ebook_file": str(ebook),
                }
                self.assertTrue(step.should_execute())

    def test_builds_cover_and_kdp_package_from_manual_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ebook = root / "sample.epub"
            prompt = root / "sample_cover_prompt.txt"
            background = root / "background.png"
            ebook.write_bytes(b"epub")
            prompt.write_text("A text-free atmospheric scene.", encoding="utf-8")
            Image.new("RGB", (600, 900), "#34506b").save(background)

            pm = ProjectManager(str(root))
            pm.book_data = {
                "init": {
                    "book_title": "The Unquiet Map",
                    "author_name": "Ada Example",
                    "book_idea": "A cartographer finds a city that moves every night.",
                    "layout_content": "Genre: Speculative mystery",
                },
                "ebook": {
                    "output_file": str(ebook),
                    "prompt_file": str(prompt),
                    "description": "A map that refuses to stay still.",
                    "completed": True,
                },
            }
            step = PublishStep(None, pm, root)

            with patch.dict(
                os.environ,
                {
                    "AI_BOOK_MODE": "auto",
                    "AI_BOOK_COVER_SOURCE": "manual",
                    "AI_BOOK_COVER_BACKGROUND": str(background),
                },
                clear=False,
            ):
                result = step.execute()

            package = json.loads(Path(result["package_file"]).read_text(encoding="utf-8"))
            self.assertEqual(package["status"], "ready_for_kdp_upload")
            self.assertEqual(package["title"], "The Unquiet Map")
            self.assertEqual(package["suggested_list_price_usd"], "0.99")
            self.assertEqual(package["kdp_defaults"]["royalty"], "35_PERCENT")
            self.assertTrue(package["kdp_defaults"]["headless"])
            self.assertEqual(len(package["keywords"]), 7)
            with Image.open(result["cover_file"]) as cover:
                self.assertEqual(cover.size, (1600, 2560))
                self.assertEqual(cover.format, "JPEG")
            self.assertFalse(step.should_execute())

    def test_kdp_money_and_author_inputs(self):
        self.assertEqual(_price({"suggested_list_price_usd": "0.10"}), "0.99")
        self.assertEqual(_split_author("Augusto Rehfeldt"), ("Augusto", "Rehfeldt"))

    def test_kdp_current_json_category_values(self):
        option = unittest.mock.Mock()
        option.get_attribute.return_value = '{"level":0,"nodeId":"668010011"}'
        select = unittest.mock.Mock()
        select.is_displayed.return_value = True
        select.find_elements.return_value = [option]
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = [select]

        self.assertIs(_category_selects(driver)[0], select)

    def test_kdp_ai_categories_are_limited_to_live_choices(self):
        ai = unittest.mock.Mock()
        ai.build_sectioned_prompt.return_value = "prompt"
        ai.generate_content.return_value = '{"choices": [2, 1]}'
        package = {
            "title": "The Unquiet Map",
            "description": "A literary speculative mystery.",
            "keywords": ["literary mystery"],
        }

        choices = _ai_category_choices(
            ai,
            package,
            ["Action & Adventure", "Literary"],
            2,
            ["Literature & Fiction", "Genre Fiction"],
        )

        self.assertEqual(choices, ["Literary", "Action & Adventure"])

    def test_kdp_select_updates_hidden_native_control(self):
        option = unittest.mock.Mock()
        option.get_attribute.return_value = "ENTIRE_AND_MINIMAL"
        select = unittest.mock.Mock()
        select.find_elements.return_value = [option]
        driver = unittest.mock.Mock()

        _select(driver, select, "ENTIRE_AND_MINIMAL")

        self.assertEqual(
            driver.execute_script.call_args.args[1:],
            (select, "ENTIRE_AND_MINIMAL"),
        )
        option.click.assert_not_called()

    def test_kdp_click_uses_a_native_user_event(self):
        element = unittest.mock.Mock()
        element.is_displayed.return_value = True
        element.is_enabled.return_value = True
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = [element]

        _click(driver, "#save-and-continue-announce", 30)

        element.click.assert_called_once_with()
        driver.execute_script.assert_not_called()

    def test_kdp_continue_retries_an_ignored_click(self):
        driver = unittest.mock.Mock()
        driver.current_url = "https://kdp.amazon.com/details"

        def click(_driver, _css, _timeout):
            if click.call_count == 2:
                driver.current_url = "https://kdp.amazon.com/content"

        click.call_count = 0

        def counted_click(*args):
            click.call_count += 1
            click(*args)

        with (
            patch("ai_book_creator.utils.kdp_publisher._click", side_effect=counted_click),
            patch("ai_book_creator.utils.kdp_publisher._errors", return_value=""),
            patch("ai_book_creator.utils.kdp_publisher._log"),
        ):
            from ai_book_creator.utils.kdp_publisher import _continue

            _continue(driver, "button", "/content", 0)

        self.assertEqual(click.call_count, 2)

    def test_kdp_content_leaves_accessibility_at_kdp_default(self):
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = []
        manuscript = unittest.mock.Mock()
        cover = unittest.mock.Mock()
        package = {"manuscript_file": "book.epub", "cover_file": "cover.jpg"}

        with (
            patch(
                "ai_book_creator.utils.kdp_publisher._upload_inputs",
                return_value=(manuscript, cover),
            ),
            patch("ai_book_creator.utils.kdp_publisher.WebDriverWait") as wait,
            patch("ai_book_creator.utils.kdp_publisher._log"),
            patch("ai_book_creator.utils.kdp_publisher._click"),
            patch("ai_book_creator.utils.kdp_publisher._ai_disclosure"),
            patch("ai_book_creator.utils.kdp_publisher._preview", return_value=True),
            patch("ai_book_creator.utils.kdp_publisher._continue"),
        ):
            wait.return_value.until.return_value = True
            _content(driver, package, 30)

        driver.find_elements.assert_called_once_with("css selector", "[role='checkbox']")

    def test_kdp_resume_redirect_reports_expired_login(self):
        driver = unittest.mock.Mock()
        driver.current_url = "https://www.amazon.com/ap/signin"
        target = "https://kdp.amazon.com/en_US/title-setup/kindle/example/content"

        with self.assertRaisesRegex(KdpPublishError, "--kdp-visible"):
            _login(driver, True, 30, target)

        driver.get.assert_called_once_with(target)

    def test_kdp_login_removes_openid_callback_query(self):
        target = "https://kdp.amazon.com/en_US/title-setup/kindle/example/pricing"
        driver = unittest.mock.Mock()
        driver.current_url = f"{target}?openid.mode=id_res&openid.sig=secret"
        driver.get.side_effect = lambda _url: (
            setattr(driver, "current_url", target)
            if driver.get.call_count == 2
            else None
        )

        _login(driver, False, 30, target)

        self.assertEqual(driver.get.call_args_list, [unittest.mock.call(target)] * 2)
        self.assertEqual(driver.current_url, target)

    def test_kdp_role_radio_clicks_the_interactive_link(self):
        link = unittest.mock.Mock()
        link.is_displayed.return_value = True
        link.is_enabled.return_value = True
        radio = unittest.mock.Mock()
        radio.is_displayed.return_value = True
        radio.find_elements.return_value = [link]
        radio.get_attribute.side_effect = ["false", "true"]
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = [radio]
        driver.execute_script.side_effect = [
            ["Yes", "Artificial Intelligence Yes No"],
            None,
        ]

        _role_radio(driver, "artificial intelligence", "yes", 30)

        self.assertEqual(driver.execute_script.call_args_list[1].args[1], link)

    def test_kdp_stage_logs_to_console_and_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "kdp.log"
            with (
                patch.dict(os.environ, {"AI_BOOK_KDP_LOG": str(log)}),
                patch("builtins.print") as console,
            ):
                _log("content uploads processed")

            self.assertIn("content uploads processed", console.call_args.args[0])
            self.assertTrue(console.call_args.kwargs["flush"])
            self.assertIn("content uploads processed", log.read_text(encoding="utf-8"))

    def test_kdp_preview_returns_from_url_containing_content_return_url(self):
        content_url = "https://kdp.amazon.com/en_US/title/example/content"
        driver = unittest.mock.Mock()
        driver.current_window_handle = "original"
        driver.window_handles = ["original"]
        driver.current_url = (
            "https://kdp.amazon.com/preview/kindle/index.html"
            f"?returnUrl={content_url}"
        )
        driver.execute_script.return_value = "complete"
        driver.find_element.return_value.text = "Preview ready"

        with patch("ai_book_creator.utils.kdp_publisher._click"):
            self.assertTrue(_preview(driver, content_url, 30))

        driver.get.assert_called_once_with(content_url)

    def test_kdp_ai_tool_names_use_stable_labels(self):
        values = ("ENTIRE_AND_MINIMAL", "FEW_AND_MINIMAL", "ENTIRE_AND_MINIMAL")
        selects = []
        for value in values:
            option = unittest.mock.Mock()
            option.get_attribute.return_value = value
            select = unittest.mock.Mock()
            select.is_displayed.return_value = True
            select.find_elements.return_value = [option]
            selects.append(select)
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = selects
        package = {
            "ai_disclosure": {"images": "AI-generated"},
            "ai_tools": {"text": "OpenAI", "images": "Pollinations"},
        }

        with (
            patch("ai_book_creator.utils.kdp_publisher._role_radio"),
            patch("ai_book_creator.utils.kdp_publisher._select"),
            patch("ai_book_creator.utils.kdp_publisher._text") as text,
        ):
            _ai_disclosure(driver, package, 30)

        text.assert_any_call(
            driver,
            "input[aria-labelledby='generative-ai-questionnaire-text-tools-prompt']",
            "OpenAI",
            30,
        )
        text.assert_any_call(
            driver,
            "input[aria-labelledby='generative-ai-questionnaire-images-tools-prompt']",
            "Pollinations",
            30,
        )

    def test_kdp_us_price_uses_stable_input_name(self):
        price = unittest.mock.Mock()
        price.is_displayed.return_value = True
        price.get_attribute.side_effect = lambda name: {
            "id": "",
            "name": "data[digital][channels][amazon][US][price_vat_inclusive]",
        }.get(name)
        driver = unittest.mock.Mock()
        driver.find_elements.return_value = [price]

        self.assertIs(_price_input(driver), price)
        driver.execute_script.assert_not_called()

    def test_kdp_pricing_clicks_hidden_native_royalty_radio(self):
        royalty = unittest.mock.Mock()
        royalty.is_enabled.return_value = True
        royalty.is_selected.return_value = True
        converted = unittest.mock.Mock()
        converted.get_attribute.side_effect = lambda name: {
            "name": "data[digital][channels][amazon][IN][converted]",
            "value": "true",
        }.get(name)
        price = unittest.mock.Mock()
        driver = unittest.mock.Mock()
        driver.find_elements.side_effect = lambda by, css: {
            "input[type='checkbox']": [],
            "#data-digital-worldwide-rights": [],
            "input[type='radio'][value='35_PERCENT']": [royalty],
            "input[name$='[converted]']": [converted],
        }.get(css, [])
        driver.find_element.return_value.text = ""
        package = {"suggested_list_price_usd": "0.99"}

        with (
            patch("ai_book_creator.utils.kdp_publisher._price_input", return_value=price),
            patch("ai_book_creator.utils.kdp_publisher._errors", return_value=""),
            patch("ai_book_creator.utils.kdp_publisher._log"),
            patch("ai_book_creator.utils.kdp_publisher._click", side_effect=KdpPublishError("stop")),
            self.assertRaisesRegex(KdpPublishError, "stop"),
        ):
            _pricing(driver, package, 30)

        self.assertIn(
            unittest.mock.call("arguments[0].click()", royalty),
            driver.execute_script.call_args_list,
        )

    def test_kdp_pricing_repairs_unconverted_marketplace(self):
        royalty = unittest.mock.Mock()
        royalty.is_enabled.return_value = True
        royalty.is_selected.return_value = True
        converted = unittest.mock.Mock()
        converted.get_attribute.side_effect = lambda name: {
            "name": "data[digital][channels][amazon][IN][converted]",
            "value": converted.value,
        }.get(name)
        converted.value = "false"
        price = unittest.mock.Mock()
        driver = unittest.mock.Mock()
        driver.current_url = "https://kdp.amazon.com/en_US/title/pricing"
        driver.find_elements.side_effect = lambda by, css: {
            "input[type='checkbox']": [],
            "#data-digital-worldwide-rights": [],
            "input[type='radio'][value='35_PERCENT']": [royalty],
            "input[name$='[converted]']": [converted],
        }.get(css, [])
        driver.find_element.return_value.text = ""

        def click(_driver, css, _timeout):
            if css == "[data-action='potter-pricing-grid-base-all'] a":
                converted.value = "true"
            else:
                raise KdpPublishError("stop")

        with (
            patch("ai_book_creator.utils.kdp_publisher._price_input", return_value=price),
            patch("ai_book_creator.utils.kdp_publisher._errors", return_value=""),
            patch("ai_book_creator.utils.kdp_publisher._log"),
            patch("ai_book_creator.utils.kdp_publisher._click", side_effect=click) as click_mock,
            self.assertRaisesRegex(KdpPublishError, "stop"),
        ):
            _pricing(driver, {"suggested_list_price_usd": "0.99"}, 30)

        click_mock.assert_any_call(
            driver,
            "[data-action='potter-pricing-grid-base-all'] a",
            30,
        )

    def test_kdp_pricing_persists_draft_before_publish(self):
        royalty = unittest.mock.Mock()
        royalty.is_enabled.return_value = True
        royalty.is_selected.return_value = True
        converted = unittest.mock.Mock()
        converted.get_attribute.side_effect = lambda name: {
            "name": "data[digital][channels][amazon][IN][converted]",
            "value": "true",
        }.get(name)
        driver = unittest.mock.Mock()
        driver.current_url = "https://kdp.amazon.com/en_US/title/pricing"
        driver.find_elements.side_effect = lambda by, css: {
            "input[type='checkbox']": [],
            "#data-digital-worldwide-rights": [],
            "input[type='radio'][value='35_PERCENT']": [royalty],
            "input[name$='[converted]']": [converted],
        }.get(css, [])
        driver.find_element.return_value.text = "Save Successful!"
        driver.request_counts = {"save?": 0, "save-and-publish": 0}
        driver.publish_clicks = 0
        driver.execute_script.side_effect = lambda script, *args: (
            driver.request_counts[args[0].rsplit("/", 1)[-1]]
            if "getEntriesByType" in script
            else None
        )

        def click(_driver, css, _timeout):
            if css == "#save-announce":
                driver.request_counts["save?"] = 1
            elif css == "button#save-and-publish-announce":
                driver.publish_clicks += 1
                if driver.publish_clicks == 2:
                    driver.request_counts["save-and-publish"] = 1
                    driver.current_url = "https://kdp.amazon.com/en_US/bookshelf?publishedId=TEST"

        with (
            patch("ai_book_creator.utils.kdp_publisher._price_input", return_value=unittest.mock.Mock()),
            patch("ai_book_creator.utils.kdp_publisher._errors", return_value=""),
            patch("ai_book_creator.utils.kdp_publisher._log"),
            patch("ai_book_creator.utils.kdp_publisher._click", side_effect=click) as click_mock,
        ):
            _pricing(driver, {"suggested_list_price_usd": "0.99"}, 0)

        self.assertEqual(
            [call.args[1] for call in click_mock.call_args_list[-3:]],
            [
                "#save-announce",
                "button#save-and-publish-announce",
                "button#save-and-publish-announce",
            ],
        )


if __name__ == "__main__":
    unittest.main()

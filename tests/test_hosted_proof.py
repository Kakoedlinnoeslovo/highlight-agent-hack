"""Exercise hosted proof interactions against the exported sample and real media."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DOG = "Dog grooming salon"
LUXURY = "Luxury watch boutique"
MIXED = "Dog-friendly luxury showroom"


def profile(*keys):
    return {
        "customers": "Customers selected by the deterministic browser-test profile.",
        "weights": [
            {"key": key, "weight": 1, "why": "Explicit test audience."} for key in keys
        ],
    }


PROFILES = {
    DOG: profile("dog"),
    LUXURY: profile("premium_car"),
    MIXED: profile("dog", "premium_car"),
}


@pytest.fixture(scope="module")
def hosted_url():
    if not shutil.which("node"):
        pytest.skip("Hosted browser tests require Node.js.")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        ["node", "dev.mjs"],
        cwd=WEB,
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"Hosted dev server exited: {process.communicate()[0]}")
            try:
                with urllib.request.urlopen(base, timeout=0.2) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            pytest.fail("Hosted dev server did not become ready within ten seconds.")
        yield base
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        bundled = Path(playwright.chromium.executable_path)
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if bundled.is_file():
            executable = bundled
        elif chrome.is_file():
            executable = chrome
        else:
            pytest.skip("Install Playwright Chromium or Google Chrome for hosted browser tests.")
        instance = playwright.chromium.launch(executable_path=str(executable))
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def snapshot():
    return json.loads((WEB / "public" / "corners.json").read_text())


@pytest.fixture
def proof_page(browser: Browser, hosted_url):
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def mock_profile(route):
        business = route.request.post_data_json["business"]
        result = PROFILES.get(business)
        route.fulfill(
            status=200 if result else 400,
            content_type="application/json",
            body=json.dumps(result or {"error": "Unknown browser-test business."}),
        )

    page.route("**/api/profile", mock_profile)
    page.goto(hosted_url)
    expect(page.locator("#cs-go")).to_be_enabled()
    yield page
    context.close()
    assert not errors, f"Unexpected browser JavaScript errors: {errors}"


def search(page: Page, business: str):
    page.locator("#business").fill(business)
    page.locator("#cs-go").click()
    expect(page.locator("#results")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#result-title")).to_be_visible()
    expect(page.locator("#cs-proof")).to_be_visible()


def corner(snapshot, camera):
    return next(item for item in snapshot["corners"] if item["camera"] == camera)


def sample(corner_data, seconds):
    return next(frame for frame in corner_data["frames"] if frame["sampledAt"] == seconds)


def matching(corner_data, audience):
    return [frame for frame in corner_data["frames"] if frame["counts"].get(audience, 0) > 0]


def expect_loaded_image(page: Page, expected):
    image = page.locator("#proof-image")
    expect(image).to_have_attribute("src", expected["image"])
    expect(image).to_be_visible()
    page.wait_for_function(
        """() => { const image = document.querySelector('#proof-image');
        return image.complete && image.naturalWidth > 0; }"""
    )


def expect_released(video):
    assert video.evaluate("video => video.paused && !video.hasAttribute('src')")


def play_context(page: Page, expected):
    page.locator("#proof-context").click()
    video = page.locator("#proof-context-video")
    expect(video).to_be_visible()
    expect(video).to_have_attribute("src", expected["clip"])
    page.wait_for_function(
        """() => { const video = document.querySelector('#proof-context-video');
        return video.readyState >= 2 && video.duration > 0; }"""
    )
    assert 0 < video.evaluate("video => video.duration") <= 6
    expect(page.locator("#proof-context")).to_have_text("Back to frame")
    return video.element_handle()


def test_dog_proof_connects_claim_crop_negative_source_and_real_context(proof_page, snapshot):
    page = proof_page
    camera = corner(snapshot, "Camera 5")
    focal = sample(camera, 7.5)
    negative = sample(camera, 17.5)
    search(page, DOG)
    expect(page.locator("#result-title")).to_have_text("Best corner: Camera 5")
    expect(page.locator("[data-count-audience='dog']")).to_contain_text(
        "Dogs detected in 3 of 36 sampled frames"
    )
    expect(page.locator("#proof-claim")).to_have_text("Dogs detected in 3 of 36 sampled frames")
    expect(page.locator("#proof-time")).to_have_text("0:07.5")
    expect_loaded_image(page, focal)
    expect(page.locator("#proof-view-label")).to_have_text("Detail crop")
    assert page.locator("#proof-image").evaluate("image => image.style.width") == "200%"
    page.locator("#proof-detail").click()
    expect(page.locator("#proof-view-label")).to_have_text("Full frame")
    assert page.locator("#proof-image").evaluate("image => image.style.width") == ""
    page.locator("#proof-detail").click()
    expect(page.locator("#proof-view-label")).to_have_text("Detail crop")
    assert page.locator("#proof-thumbnails [data-proof-frame]").evaluate_all(
        "buttons => buttons.map(button => button.dataset.proofFrame)"
    ) == [frame["segment_id"] for frame in matching(camera, "dog")]
    expect(page.locator("#proof-markers button")).to_have_count(36)
    expect(page.locator("#proof-markers .cs-proof-positive")).to_have_count(3)

    page.locator("#proof-markers [data-sample-seconds='17.5']").click()
    expect_loaded_image(page, negative)
    expect(page.locator("#proof-time")).to_have_text("0:17.5")
    expect(page.locator("#proof-selection")).to_have_text("Dogs not detected in this sample.")
    expect(page.locator("#proof-detail")).to_be_hidden()
    expect(page.locator("#proof-view-label")).to_have_text("Full frame")
    expect(page.locator("#proof-source")).to_have_text(
        f"0:17.5 sampled from {negative['source']}"
    )
    expect(page.locator("#proof-recorded")).to_contain_text("Camera 5 · Recorded")
    video = play_context(page, negative)
    expect(page.locator("#proof-time")).to_have_text("0:15.0–0:20.0")
    page.locator("#proof-context").click()
    expect_released(video)
    expect_loaded_image(page, negative)
    expect(page.locator("#proof-time")).to_have_text("0:17.5")


def test_all_luxury_matches_are_reachable_by_keyboard_and_enlargement(proof_page, snapshot):
    page = proof_page
    camera = corner(snapshot, "Camera 1")
    matches = matching(camera, "premium_car")
    search(page, LUXURY)
    expect(page.locator("#proof-heading")).to_have_text("Frame evidence · Camera 1")
    expect(page.locator("#proof-claim")).to_have_text(
        "Premium cars detected in 28 of 36 sampled frames"
    )
    thumbs = page.locator("#proof-thumbnails [data-proof-frame]")
    expect(thumbs).to_have_count(28)
    assert thumbs.evaluate_all("buttons => buttons.map(button => button.dataset.proofFrame)") == [
        frame["segment_id"] for frame in matches
    ]
    thumbs.first.focus()
    page.keyboard.press("End")
    expect_loaded_image(page, matches[-1])
    expect(thumbs.last).to_be_focused()
    expect(thumbs.last).to_have_attribute("aria-pressed", "true")
    assert page.locator("#proof-thumbnails").evaluate("rail => rail.scrollLeft > 0")
    assert thumbs.last.evaluate(
        """button => { const b = button.getBoundingClientRect();
        const rail = button.parentElement.getBoundingClientRect();
        return b.left >= rail.left && b.right <= rail.right + 1; }"""
    )
    page.keyboard.press("ArrowLeft")
    expect_loaded_image(page, matches[-2])
    page.locator("#proof-enlarge").click()
    expect(page.locator("#proof-enlargement")).to_be_visible()
    expect(page.locator("#proof-enlarged-image")).to_have_attribute("src", matches[-2]["image"])
    expect(page.locator("#proof-enlarged-caption")).to_contain_text("Camera 1 · Full sampled frame")
    page.keyboard.press("Escape")
    expect(page.locator("#proof-enlargement")).not_to_be_visible()
    expect(page.locator("#proof-enlarge")).to_be_focused()


def test_core_switching_and_zero_runner_up_use_the_selected_corner_only(proof_page, snapshot):
    page = proof_page
    top = corner(snapshot, "Camera 5")
    runner = corner(snapshot, "Camera 1")
    search(page, MIXED)
    expect(page.locator("#proof-time")).to_have_text("0:07.5")
    expect(page.locator("[data-proof-audience='dog']")).to_have_attribute("aria-pressed", "true")
    video = play_context(page, sample(top, 7.5))
    page.locator("[data-proof-audience='premium_car']").click()
    expect_released(video)
    expect(page.locator("#proof-heading")).to_have_text("Frame evidence · Camera 5")
    expect(page.locator("#proof-claim")).to_have_text(
        "Premium cars detected in 20 of 36 sampled frames"
    )
    expect(page.locator("#proof-view-label")).to_have_text("Full frame")
    expect(page.locator("#proof-detail")).to_be_hidden()
    expect_loaded_image(page, matching(top, "premium_car")[0])

    page.locator("[data-count-audience='dog']").click()
    expect_loaded_image(page, sample(top, 7.5))
    page.locator(f"[data-proof-corner='{runner['id']}']").click()
    expect(page.locator("#proof-heading")).to_have_text("Frame evidence · Camera 1")
    expect(page.locator("#proof-claim")).to_have_text("Dogs detected in 0 of 36 sampled frames")
    expect(page.locator("#proof-thumbnails button")).to_have_count(0)
    expect(page.locator("#proof-thumbnails")).to_contain_text("No dogs detected")
    expect(page.locator("#proof-markers .cs-proof-positive")).to_have_count(0)
    expect_loaded_image(page, runner["frames"][0])
    expect(page.locator("#proof-selection")).to_have_text("Dogs not detected in this sample.")
    page.locator("#proof-markers [data-sample-seconds='17.5']").click()
    expect_loaded_image(page, sample(runner, 17.5))
    page.locator("[data-proof-audience='premium_car']").click()
    expect(page.locator("#proof-heading")).to_have_text("Frame evidence · Camera 1")
    expect(page.locator("#proof-thumbnails button")).to_have_count(28)
    expect_loaded_image(page, matching(runner, "premium_car")[0])


def test_new_search_and_reset_release_the_previous_context_video(proof_page, snapshot):
    page = proof_page
    search(page, DOG)
    dog_video = play_context(page, sample(corner(snapshot, "Camera 5"), 7.5))
    search(page, LUXURY)
    expect_released(dog_video)
    expect(page.locator("#proof-heading")).to_have_text("Frame evidence · Camera 1")
    expect(page.locator("video")).to_have_count(1)
    luxury_video = play_context(page, matching(corner(snapshot, "Camera 1"), "premium_car")[0])
    page.locator("#edit-business").click()
    page.locator("#reset").click()
    expect_released(luxury_video)
    expect(page.locator("#cs-proof")).to_have_count(0)
    expect(page.locator("#results video")).to_have_count(0)
    expect(page.locator("#business")).to_have_value("")
    expect(page.locator("#business")).to_be_focused()
    expect(page.locator("#results")).to_contain_text("See who walks past.")


def test_missing_still_and_context_show_explicit_errors_then_recover(proof_page, snapshot):
    page = proof_page
    focal = sample(corner(snapshot, "Camera 5"), 7.5)
    unavailable = sample(corner(snapshot, "Camera 5"), 17.5)
    search(page, DOG)
    expect_loaded_image(page, focal)
    image_route = f"**{unavailable['image']}"
    page.route(image_route, lambda route: route.fulfill(status=404, body="Image unavailable"))
    page.locator("#proof-markers [data-sample-seconds='17.5']").click()
    expect(page.locator("#proof-image-error")).to_be_visible()
    expect(page.locator("#proof-enlarge")).to_be_disabled()
    expect(page.locator("#proof-claim")).to_have_text("Dogs detected in 3 of 36 sampled frames")
    play_context(page, unavailable)
    expect(page.locator("#proof-image-error")).to_be_hidden()
    page.locator("#proof-context-video").evaluate(
        "video => { video.src = '/media/missing-proof-browser-test.mp4'; video.load(); }"
    )
    expect(page.locator("#proof-media-status")).to_contain_text("could not be loaded")
    expect(page.locator(".cs-ranking-row")).to_have_count(5)
    page.unroute(image_route)
    page.locator("#proof-context").click()
    expect_loaded_image(page, unavailable)
    expect(page.locator("#proof-image-error")).to_be_hidden()
    expect(page.locator("#proof-media-status")).to_have_text("")


def test_delayed_replacement_never_shows_old_pixels_or_covers_active_video(proof_page, snapshot):
    page = proof_page
    camera = corner(snapshot, "Camera 5")
    replacement = sample(camera, 17.5)
    search(page, DOG)
    expect_loaded_image(page, sample(camera, 7.5))
    pending = []
    # Deliberately hold one real image request to reproduce a slow or failing media origin.
    page.route(f"**{replacement['image']}", lambda route: pending.append(route))
    page.locator("#proof-markers [data-sample-seconds='17.5']").click()
    page.wait_for_function("document.querySelector('#proof-image').getAttribute('src').includes('.jpg')")
    assert pending, "The replacement image request must still be pending during these assertions."
    expect(page.locator("#proof-image")).to_be_hidden()
    expect(page.locator("#proof-enlarge")).to_be_disabled()
    expect(page.locator("#proof-time")).to_have_text("0:17.5")
    video = play_context(page, replacement)
    pending.pop().fulfill(status=404, content_type="text/plain", body="Image unavailable")
    page.wait_for_function("document.querySelector('#proof-image').complete")
    expect(page.locator("#proof-image-error")).to_be_hidden()
    expect(page.locator("#proof-context-video")).to_be_visible()
    assert video.evaluate("video => video.readyState >= 2")
    expect(page.locator("#proof-view-label")).to_have_text("Surrounding footage")


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}])
def test_proof_layout_is_bounded_and_respects_reduced_motion(proof_page, viewport):
    page = proof_page
    page.set_viewport_size(viewport)
    page.emulate_media(reduced_motion="reduce")
    search(page, LUXURY)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator("#cs-hero")).to_be_hidden()
    expect(page.locator("#proof-thumbnails button")).to_have_count(28)
    for selector in ["#cs-proof", ".cs-recommendation", ".cs-chip", ".cs-ranking-row"]:
        assert page.locator(selector).first.evaluate(
            "element => getComputedStyle(element).animationName"
        ) == "none"
    proof = page.locator("#cs-proof").bounding_box()
    ranking = page.locator(".cs-ranking").bounding_box()
    assert proof["x"] >= 0 and proof["x"] + proof["width"] <= viewport["width"]
    if viewport["width"] == 390:
        assert proof["y"] + proof["height"] <= ranking["y"]
    else:
        image = page.locator("#proof-viewport").bounding_box()
        claim = page.locator("#proof-claim").bounding_box()
        assert claim["y"] + claim["height"] < viewport["height"]
        assert min(image["height"], viewport["height"] - image["y"]) >= 150
    page.locator("#proof-thumbnails button").last.focus()
    page.keyboard.press("End")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

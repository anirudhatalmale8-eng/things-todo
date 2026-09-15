#!/usr/bin/env python3
"""
End-to-end proof that the to-do app works, driven through real Chromium.

Boots its own server instance on an unused port with a throwaway data file, so
running the tests never touches your real list. Screenshots land in artifacts/.

    python3 tests/test_todo.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
TIMEOUT = 5_000  # ms — generous for a local server

# Chromium needs libnspr4/libnss3, which on this box live in a side-loaded
# bundle rather than in the default loader path. Point the loader at whichever
# candidate exists *before* Playwright spawns the browser process.
_LIB_CANDIDATES = [
    Path.home() / "pwlibs/usr/lib/x86_64-linux-gnu",
    Path("/usr/lib/playwright-deps"),
]
for _libs in _LIB_CANDIDATES:
    if (_libs / "libnspr4.so").exists():
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        if str(_libs) not in existing.split(":"):
            os.environ["LD_LIBRARY_PATH"] = f"{_libs}:{existing}".rstrip(":")
        break

from playwright.sync_api import expect, sync_playwright  # noqa: E402 - after env setup


# --------------------------------------------------------------------- harness

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(base_url: str, deadline: float = 15.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/todos", timeout=1) as res:
                if res.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    raise RuntimeError(f"server at {base_url} never became ready")


class Results:
    """Collects pass/fail per check so one failure doesn't hide the rest."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn) -> bool:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - we report every failure kind
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            self.rows.append((name, False, first_line))
            print(f"  FAIL  {name}\n        {first_line}")
            return False
        self.rows.append((name, True, ""))
        print(f"  PASS  {name}")
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.rows if not ok)

    def report(self) -> str:
        width = max(len(n) for n, _, _ in self.rows) + 2
        lines = [f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}{msg}" for n, ok, msg in self.rows]
        passed = len(self.rows) - self.failed
        lines.append("")
        lines.append(f"{passed}/{len(self.rows)} checks passed")
        return "\n".join(lines)


# ----------------------------------------------------------------------- tests

def run_checks(page, base_url: str, r: Results) -> None:
    todos = page.get_by_test_id("todo")
    new_todo = page.get_by_test_id("new-todo")

    # -- 1. page loads and talks to the API ---------------------------------
    page.goto(base_url, wait_until="domcontentloaded")

    r.check(
        "page loads and finishes its initial API fetch",
        lambda: expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=TIMEOUT),
    )
    r.check("title is correct", lambda: expect(page).to_have_title("Things — a to-do list"))
    r.check("starts empty", lambda: expect(todos).to_have_count(0))
    r.check(
        "empty-state message is visible",
        lambda: expect(page.get_by_test_id("empty")).to_have_text("Nothing here yet."),
    )

    # -- 2. ADD -------------------------------------------------------------
    for text in ("Buy oat milk", "Renew passport", "Water the ferns"):
        new_todo.fill(text)
        page.get_by_test_id("add-btn").click()

    r.check("adding three items renders three rows", lambda: expect(todos).to_have_count(3))
    r.check(
        "rows show the exact text that was typed, in order",
        lambda: expect(page.get_by_test_id("title")).to_have_text(
            ["Buy oat milk", "Renew passport", "Water the ferns"]
        ),
    )
    r.check(
        "counter reads '3 items left · 0 done'",
        lambda: expect(page.get_by_test_id("summary")).to_have_text("3 items left · 0 done"),
    )
    r.check("input clears after adding", lambda: expect(new_todo).to_have_value(""))

    # Enter key should submit too.
    new_todo.fill("Added with the Enter key")
    new_todo.press("Enter")
    r.check("pressing Enter adds an item", lambda: expect(todos).to_have_count(4))

    # Blank input must not create a row.
    new_todo.fill("   ")
    page.get_by_test_id("add-btn").click()
    page.wait_for_timeout(250)
    r.check("whitespace-only input is rejected", lambda: expect(todos).to_have_count(4))

    page.screenshot(path=ARTIFACTS / "01-four-items-added.png", full_page=True)

    # -- 3. COMPLETE --------------------------------------------------------
    second = todos.nth(1)
    second.get_by_test_id("toggle").check()

    r.check(
        "checkbox stays checked after completing",
        lambda: expect(second.get_by_test_id("toggle")).to_be_checked(),
    )
    r.check(
        "completed row gets the is-done class (strike-through styling)",
        lambda: expect(second).to_have_class("row is-done"),
    )
    r.check(
        "completed row actually renders with line-through",
        lambda: expect(second.get_by_test_id("title")).to_have_css(
            "text-decoration-line", "line-through"
        ),
    )
    r.check(
        "counter updates to '3 items left · 1 done'",
        lambda: expect(page.get_by_test_id("summary")).to_have_text("3 items left · 1 done"),
    )

    page.screenshot(path=ARTIFACTS / "02-item-completed.png", full_page=True)

    # Filters should reflect completion state.
    page.get_by_test_id("filter-done").click()
    r.check(
        "'Done' filter shows only the completed item",
        lambda: expect(page.get_by_test_id("title")).to_have_text(["Renew passport"]),
    )
    page.get_by_test_id("filter-active").click()
    r.check(
        "'Active' filter hides the completed item",
        lambda: expect(page.get_by_test_id("title")).to_have_text(
            ["Buy oat milk", "Water the ferns", "Added with the Enter key"]
        ),
    )
    page.get_by_test_id("filter-all").click()
    r.check("'All' filter shows everything again", lambda: expect(todos).to_have_count(4))

    # Un-completing works.
    second.get_by_test_id("toggle").uncheck()
    r.check(
        "un-completing restores the active count",
        lambda: expect(page.get_by_test_id("summary")).to_have_text("4 items left · 0 done"),
    )
    second.get_by_test_id("toggle").check()  # re-complete for the persistence check

    # -- 4. DELETE, now behind a confirmation step --------------------------
    dialog = page.get_by_test_id("confirm-dialog")
    ferns = todos.filter(has_text="Water the ferns")
    all_four = ["Buy oat milk", "Renew passport", "Water the ferns", "Added with the Enter key"]

    r.check("the confirm dialog is not shown until a × is clicked", lambda: expect(dialog).to_be_hidden())

    ferns.get_by_test_id("delete").click()

    r.check("clicking × opens a confirmation dialog", lambda: expect(dialog).to_be_visible(timeout=TIMEOUT))
    r.check(
        'the dialog asks "Delete this to-do?"',
        lambda: expect(page.get_by_test_id("confirm-title")).to_have_text("Delete this to-do?"),
    )
    r.check(
        "the dialog names the item it is about to delete",
        lambda: expect(page.get_by_test_id("confirm-item")).to_have_text("Water the ferns"),
    )
    r.check(
        "the dialog offers a Cancel button",
        lambda: expect(page.get_by_test_id("confirm-cancel")).to_have_text("Cancel"),
    )
    r.check(
        "the dialog offers a Delete button",
        lambda: expect(page.get_by_test_id("confirm-delete")).to_have_text("Delete"),
    )
    r.check(
        "the × alone deletes nothing while the dialog is open",
        lambda: expect(page.get_by_test_id("title")).to_have_text(all_four),
    )
    r.check(
        "Cancel takes the initial focus, so a stray Enter cannot delete",
        lambda: expect(page.get_by_test_id("confirm-cancel")).to_be_focused(),
    )

    page.screenshot(path=ARTIFACTS / "03-delete-confirmation.png")

    # -- 4a. Cancel must leave everything exactly as it was -----------------
    page.get_by_test_id("confirm-cancel").click()

    r.check("Cancel closes the dialog", lambda: expect(dialog).to_be_hidden(timeout=TIMEOUT))
    r.check(
        "Cancel keeps the item that was nearly deleted",
        lambda: expect(page.get_by_test_id("title")).to_have_text(all_four),
    )
    r.check(
        "Cancel returns focus to the × that opened the dialog",
        lambda: expect(ferns.get_by_test_id("delete")).to_be_focused(),
    )

    after_cancel = page.evaluate("() => fetch('/api/todos').then(r => r.json())")
    r.check(
        "Cancel deletes nothing server-side either",
        lambda: _assert(
            [t["title"] for t in after_cancel] == all_four,
            f"API returned {[t['title'] for t in after_cancel]}",
        ),
    )

    # -- 4b. Escape and backdrop clicks are also 'no' -----------------------
    ferns.get_by_test_id("delete").click()
    expect(dialog).to_be_visible(timeout=TIMEOUT)
    page.keyboard.press("Escape")

    r.check("Escape dismisses the dialog", lambda: expect(dialog).to_be_hidden(timeout=TIMEOUT))
    r.check(
        "Escape does not delete the item",
        lambda: expect(page.get_by_test_id("title")).to_have_text(all_four),
    )

    ferns.get_by_test_id("delete").click()
    expect(dialog).to_be_visible(timeout=TIMEOUT)
    page.mouse.click(6, 6)  # the backdrop, well outside the dialog panel

    r.check("clicking the backdrop dismisses the dialog", lambda: expect(dialog).to_be_hidden(timeout=TIMEOUT))
    r.check(
        "clicking the backdrop does not delete the item",
        lambda: expect(page.get_by_test_id("title")).to_have_text(all_four),
    )

    # -- 4c. Confirming actually deletes ------------------------------------
    ferns.get_by_test_id("delete").click()
    expect(dialog).to_be_visible(timeout=TIMEOUT)
    page.get_by_test_id("confirm-delete").click()

    r.check("confirming with Delete closes the dialog", lambda: expect(dialog).to_be_hidden(timeout=TIMEOUT))
    r.check("deleting removes one row", lambda: expect(todos).to_have_count(3))
    r.check(
        "the deleted item is gone and the others remain",
        lambda: expect(page.get_by_test_id("title")).to_have_text(
            ["Buy oat milk", "Renew passport", "Added with the Enter key"]
        ),
    )

    page.screenshot(path=ARTIFACTS / "04-item-deleted.png", full_page=True)

    # -- 5. PERSISTENCE (server-side, not just DOM state) -------------------
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=TIMEOUT)

    r.check("state survives a full page reload", lambda: expect(todos).to_have_count(3))
    r.check(
        "the completed item is still completed after reload",
        lambda: expect(todos.nth(1).get_by_test_id("toggle")).to_be_checked(),
    )
    r.check(
        "titles are intact after reload",
        lambda: expect(page.get_by_test_id("title")).to_have_text(
            ["Buy oat milk", "Renew passport", "Added with the Enter key"]
        ),
    )

    # Confirm the browser is hitting a real API, not localStorage.
    api_payload = page.evaluate("() => fetch('/api/todos').then(r => r.json())")
    r.check(
        "GET /api/todos returns the same 3 items the UI shows",
        lambda: _assert(
            [t["title"] for t in api_payload]
            == ["Buy oat milk", "Renew passport", "Added with the Enter key"],
            f"API returned {[t['title'] for t in api_payload]}",
        ),
    )
    r.check(
        "the completed flag is stored server-side",
        lambda: _assert(
            api_payload[1]["done"] is True and api_payload[0]["done"] is False,
            f"done flags were {[t['done'] for t in api_payload]}",
        ),
    )

    # -- 6. CLEAR COMPLETED -------------------------------------------------
    page.get_by_test_id("clear-done").click()
    r.check("'Clear completed' removes only completed items", lambda: expect(todos).to_have_count(2))
    r.check(
        "'Clear completed' button hides when nothing is completed",
        lambda: expect(page.get_by_test_id("clear-done")).to_be_hidden(),
    )

    # -- 7. XSS / escaping --------------------------------------------------
    payload = "<img src=x onerror=alert(1)>"
    new_todo.fill(payload)
    new_todo.press("Enter")
    r.check(
        "HTML in a to-do is escaped as text, not injected",
        lambda: expect(todos.last.get_by_test_id("title")).to_have_text(payload),
    )
    r.check(
        "no element was created from the injected markup",
        lambda: _assert(page.locator("img[src='x']").count() == 0, "injected <img> was rendered"),
    )

    # The dialog echoes the title back, so it has to escape it too. This also
    # proves rows rendered *after* a reload still get the confirmation wiring.
    todos.last.get_by_test_id("delete").click()
    expect(dialog).to_be_visible(timeout=TIMEOUT)
    r.check(
        "HTML is escaped in the confirm dialog as well",
        lambda: expect(page.get_by_test_id("confirm-item")).to_have_text(payload),
    )
    r.check(
        "no element was created from the markup echoed into the dialog",
        lambda: _assert(page.locator("img[src='x']").count() == 0, "injected <img> was rendered"),
    )
    page.get_by_test_id("confirm-cancel").click()
    expect(dialog).to_be_hidden(timeout=TIMEOUT)

    page.screenshot(path=ARTIFACTS / "05-final-state.png", full_page=True)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ------------------------------------------------------------------------ main

def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    data_file = Path(tempfile.mkdtemp(prefix="todo-test-")) / "data.json"

    print(f"starting server on {base_url} (data file: {data_file})")
    server = subprocess.Popen(
        ["node", str(ROOT / "server.js")],
        env={**os.environ, "PORT": str(port), "DATA_FILE": str(data_file)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    results = Results()
    console_errors: list[str] = []

    try:
        wait_for_server(base_url)
        print("server ready\n")

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 900, "height": 820})
            page = context.new_page()
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )
            page.on("pageerror", lambda err: console_errors.append(str(err)))

            print(f"Chromium {browser.version}\n")
            try:
                run_checks(page, base_url, results)
            finally:
                results.check(
                    "no JavaScript errors in the browser console",
                    lambda: _assert(not console_errors, "; ".join(console_errors[:3])),
                )
                context.close()
                browser.close()
    except Exception:
        traceback.print_exc()
        results.rows.append(("test harness ran to completion", False, "see traceback above"))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    report = results.report()
    print("\n" + "=" * 62)
    print(report)
    print("=" * 62)
    (ARTIFACTS / "test-report.txt").write_text(report + "\n")

    return 1 if results.failed else 0


if __name__ == "__main__":
    sys.exit(main())

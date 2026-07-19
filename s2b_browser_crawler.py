# coding: utf-8
import argparse
import os
import shutil
import tempfile
import random
import re
import sys
import time
from datetime import datetime

from s2b_local_crawler import (
    BASE_URL,
    KEYWORD_DELAY_RANGE,
    KEYWORDS,
    LIST_URL,
    MAX_PAGES_BY_KEYWORD,
    MAX_PAGES_PER_KEYWORD,
    PAGE_DELAY_RANGE,
    display_date,
    get_date_range_from_user,
    is_captcha,
    is_excluded_contract_name,
    parse_page,
    publish_to_github,
    save_cumulative_html,
    select_keywords,
    update_cumulative_json,
    validate_delay_range,
)


def sleep_random(delay_range, label="wait"):
    seconds = random.uniform(*delay_range)
    print("    " + label + ": " + str(round(seconds, 1)) + "s")
    time.sleep(seconds)


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed.\n"
            "Install it with:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def find_browser_executable(browser_name):
    candidates = []
    if browser_name in ("chrome", "auto"):
        candidates.extend([
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ])
    if browser_name in ("edge", "auto"):
        candidates.extend([
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ])

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def get_user_data_dir():
    base_dir = os.path.join(tempfile.gettempdir(), "s2b_browser_profiles")
    os.makedirs(base_dir, exist_ok=True)
    return tempfile.mkdtemp(prefix="profile_", dir=base_dir)


def cleanup_user_data_dir(path):
    if path and os.path.isdir(path):
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def close_context(context):
    profile_dir = getattr(context, "s2b_user_data_dir", "")
    try:
        context.close()
    except Exception:
        pass
    cleanup_user_data_dir(profile_dir)


def launch_browser(playwright, args):
    executable_path = find_browser_executable(args.browser)
    launch_args = {
        "headless": args.headless,
        "slow_mo": args.slow_mo,
    }
    if executable_path:
        launch_args["executable_path"] = executable_path
        print("[browser] using: " + executable_path)
    else:
        print("[browser] using Playwright-managed Chromium")

    try:
        return playwright.chromium.launch(**launch_args)
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            raise SystemExit(
                "Playwright browser executable was not found.\n"
                "Run this once, then retry:\n"
                "  python -m playwright install chromium\n"
                "Or install Chrome/Edge and run with --browser auto."
            ) from exc
        raise


def create_context_page(playwright, args):
    executable_path = find_browser_executable(args.browser)
    launch_args = {
        "headless": args.headless,
        "slow_mo": args.slow_mo,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "viewport": {"width": 1365, "height": 900},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    if executable_path:
        launch_args["executable_path"] = executable_path
        print("[browser] using: " + executable_path)
    else:
        print("[browser] using Playwright-managed Chromium")

    user_data_dir = get_user_data_dir()
    try:
        context = playwright.chromium.launch_persistent_context(user_data_dir, **launch_args)
    except Exception:
        cleanup_user_data_dir(user_data_dir)
        raise
    context.s2b_user_data_dir = user_data_dir
    page = context.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
    return context, page


def submit_search(page, keyword, date_from, date_to, page_no, timeout_ms):
    fields = {
        "forwardName": "list03",
        "pageNo": str(page_no),
        "tender_num": "",
        "tender_step_code": "",
        "page_flag": "",
        "excelSection": "N",
        "process_yn": "Y",
        "search_yn": "Y",
        "tender_sep1": "1",
        "tender_name": keyword,
        "company_name_s": "",
        "tender_sep2": "2",
        "tender_date_start": date_from,
        "tender_date_end": date_to,
        "tender_item": "",
        "estimate_kind": "",
        "areaKind": "전국",
    }

    page.goto(LIST_URL + "?forwardName=list03", wait_until="domcontentloaded", timeout=timeout_ms)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
        page.evaluate(
            """(fields) => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '%s';
                form.acceptCharset = 'EUC-KR';
                for (const [name, value] of Object.entries(fields)) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = name;
                    input.value = value;
                    form.appendChild(input);
                }
                document.body.appendChild(form);
                form.submit();
            }"""
            % LIST_URL,
            fields,
        )


def go_result_page(page, page_no, timeout_ms, PlaywrightTimeoutError):
    has_go_list = page.evaluate("() => typeof window.goList === 'function'")
    if not has_go_list:
        raise RuntimeError("S2B pagination function goList() was not found.")

    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
            page.evaluate("(pageNo) => window.goList(pageNo)", page_no)
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)


def wait_after_navigation(page, timeout_ms, PlaywrightTimeoutError):
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
    except PlaywrightTimeoutError:
        pass


def page_content_bytes(page):
    return page.content().encode("euc-kr", errors="replace")


def save_debug_page(page, keyword, page_no, reason):
    os.makedirs("debug", exist_ok=True)
    safe_keyword = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", keyword).strip("_") or "keyword"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join("debug", "s2b_" + reason + "_" + safe_keyword + "_p" + str(page_no) + "_" + stamp)
    html_path = base + ".html"
    png_path = base + ".png"
    with open(html_path, "w", encoding="utf-8") as file:
        file.write(page.content())
    try:
        page.screenshot(path=png_path, full_page=True)
        print("    [debug] saved: " + html_path + ", " + png_path)
    except Exception:
        print("    [debug] saved: " + html_path)


def current_active_page(page):
    pages = [item for item in page.context.pages if not item.is_closed()]
    return pages[-1] if pages else page


def is_page_closed_error(exc):
    return "Target page, context or browser has been closed" in str(exc)


def wait_for_manual_captcha(page, keyword, page_no, PlaywrightTimeoutError):
    if not is_captcha(page_content_bytes(page)):
        return page, False

    print("    [!] CAPTCHA detected. Solve it in the browser window.")
    input("    After CAPTCHA is accepted, press Enter here...")
    page = current_active_page(page)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
    except PlaywrightTimeoutError:
        pass
    print("    [captcha] continuing with url: " + page.url)

    if is_captcha(page_content_bytes(page)):
        print("    [captcha] this page still looks like CAPTCHA. Saving debug and continuing once.")
        save_debug_page(page, keyword, page_no, "captcha_after_enter")
    return page, True


def fetch_by_keyword_browser(page, keyword, date_from, date_to, page_delay_range, timeout_ms, PlaywrightTimeoutError):
    results = []
    max_pages = MAX_PAGES_BY_KEYWORD.get(keyword, MAX_PAGES_PER_KEYWORD)

    page_no = 1
    saw_data_page = False
    while max_pages is None or page_no <= max_pages:
        if page_no > 1:
            sleep_random(page_delay_range, "request delay")

        try:
            if page_no == 1:
                submit_search(page, keyword, date_from, date_to, page_no, timeout_ms)
            else:
                go_result_page(page, page_no, timeout_ms, PlaywrightTimeoutError)
            wait_after_navigation(page, timeout_ms, PlaywrightTimeoutError)
        except Exception as exc:
            print("    browser error: " + str(exc))
            if is_page_closed_error(exc) and results:
                print("    page closed after partial results. keeping this keyword's collected records.")
                return results, page
            raise

        try:
            page, captcha_was_solved = wait_for_manual_captcha(page, keyword, page_no, PlaywrightTimeoutError)
            if captcha_was_solved:
                records, has_data = parse_page(page_content_bytes(page))
                if has_data:
                    print("    [captcha] result table loaded after CAPTCHA.")
                else:
                    try:
                        print("    [captcha] no result table after CAPTCHA. retrying the search in the same browser session...")
                        submit_search(page, keyword, date_from, date_to, page_no, timeout_ms)
                        wait_after_navigation(page, timeout_ms, PlaywrightTimeoutError)
                    except Exception as exc:
                        print("    browser error after CAPTCHA: " + str(exc))
                        if is_page_closed_error(exc) and results:
                            print("    page closed after CAPTCHA. keeping this keyword's collected records.")
                            return results, page
                        raise

                    page, captcha_again = wait_for_manual_captcha(page, keyword, page_no, PlaywrightTimeoutError)
                    if captcha_again:
                        print("    [captcha] CAPTCHA appeared again after retry. Keeping collected records and stopping this keyword.")
                        save_debug_page(page, keyword, page_no, "captcha_repeated")
                        break
                    records, has_data = parse_page(page_content_bytes(page))
            else:
                records, has_data = parse_page(page_content_bytes(page))
        except Exception as exc:
            print("    browser error while reading page: " + str(exc))
            if is_page_closed_error(exc) and results:
                print("    page closed after partial results. keeping this keyword's collected records.")
                return results, page
            raise

        if not has_data:
            if page_no > 1 and saw_data_page:
                print("    page " + str(page_no) + ": no more results")
                break
            print("    no parsable result table. url: " + page.url)
            save_debug_page(page, keyword, page_no, "no_table")
            break

        saw_data_page = True
        keyword_matched = [record for record in records if keyword in record["계약명"]]
        filtered = [
            record for record in keyword_matched
            if not is_excluded_contract_name(record["계약명"])
        ]
        excluded_count = len(keyword_matched) - len(filtered)
        keyword_miss_count = len(records) - len(keyword_matched)
        results.extend(filtered)
        print(
            "    page " + str(page_no) + ": " + str(len(records)) + " recv, "
            + str(len(filtered)) + " matched"
            + ", " + str(excluded_count) + " excluded"
            + ", " + str(keyword_miss_count) + " keyword-miss"
        )

        if len(records) == 0:
            break
        page_no += 1

    return results, page


def fetch_all_browser(date_from, date_to, keywords, args):
    print("[period] " + display_date(date_from) + " ~ " + display_date(date_to))
    print("[keywords] " + ", ".join(keywords) + "\n")

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    seen_nos = set()
    all_results = []
    keyword_map = {}

    with sync_playwright() as playwright:
        context, page = create_context_page(playwright, args)

        for keyword in keywords:
            if page.is_closed():
                print("[browser] page was closed. opening a new page.")
                try:
                    page = context.new_page()
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                except Exception:
                    close_context(context)
                    context, page = create_context_page(playwright, args)

            print("[" + keyword + "] searching in browser...")
            items = []
            fatal_error = False
            for attempt in range(2):
                try:
                    items, page = fetch_by_keyword_browser(
                        page,
                        keyword,
                        date_from,
                        date_to,
                        args.page_delay_range,
                        args.timeout * 1000,
                        PlaywrightTimeoutError,
                    )
                    break
                except Exception as exc:
                    page_was_closed = is_page_closed_error(exc)
                    if not page_was_closed:
                        print("[browser] stopped: " + str(exc))
                        fatal_error = True
                        break
                    if attempt == 1:
                        print("[browser] closed again. skipping this keyword and continuing.")
                        close_context(context)
                        context, page = create_context_page(playwright, args)
                        break
                    print("[browser] closed unexpectedly. reopening and retrying this keyword once.")
                    close_context(context)
                    context, page = create_context_page(playwright, args)

            if fatal_error:
                break
            if page.is_closed():
                print("[browser] page is closed. opening a new page for the next keyword.")
                try:
                    page = context.new_page()
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                except Exception:
                    close_context(context)
                    context, page = create_context_page(playwright, args)
            print("  -> " + str(len(items)) + " found\n")

            for item in items:
                contract_no = item["계약번호"]
                if contract_no not in seen_nos:
                    seen_nos.add(contract_no)
                    all_results.append(item)
                    keyword_map[contract_no] = [keyword]
                elif contract_no in keyword_map and keyword not in keyword_map[contract_no]:
                    keyword_map[contract_no].append(keyword)

            if keyword != keywords[-1]:
                sleep_random(args.keyword_delay_range, "keyword delay")

        close_context(context)

    for item in all_results:
        item["매칭키워드"] = keyword_map.get(item["계약번호"], [])

    print("=" * 55)
    print("이번 검색 결과: " + str(len(all_results)) + "건(중복 제거)")
    return all_results


def get_keywords_from_user(args):
    if args.keywords is not None:
        return select_keywords(args)

    print('\nRegistered keywords:')
    for index, keyword in enumerate(KEYWORDS, 1):
        print('  ' + str(index).rjust(2) + '. ' + keyword)
    print('\nTip: enter numbers to avoid Korean input issues. Example: 5 or 1,5,9 or 5-7')
    typed = input('Keyword numbers/names separated by comma (Enter=all): ').strip()
    if typed:
        args.keywords = typed
    return select_keywords(args)


def parse_args():
    parser = argparse.ArgumentParser(description="S2B browser-based cumulative crawler")
    parser.add_argument("--from", dest="date_from", help="검색 시작일: YYYYMMDD 또는 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="검색 종료일: YYYYMMDD 또는 YYYY-MM-DD")
    parser.add_argument("--keywords", help="검색할 키워드를 쉼표로 지정합니다. 예: 국어,수학,영어")
    parser.add_argument("--batch-size", type=int, default=0, help="키워드를 몇 개씩 나눠 실행할지 지정합니다.")
    parser.add_argument("--batch-index", type=int, default=1, help="실행할 키워드 묶음 번호입니다. 1부터 시작합니다.")
    parser.add_argument("--page-delay-min", type=float, default=PAGE_DELAY_RANGE[0], help="Minimum delay between page requests in seconds.")
    parser.add_argument("--page-delay-max", type=float, default=PAGE_DELAY_RANGE[1], help="Maximum delay between page requests in seconds.")
    parser.add_argument("--keyword-delay-min", type=float, default=KEYWORD_DELAY_RANGE[0], help="Minimum delay between keyword searches in seconds.")
    parser.add_argument("--keyword-delay-max", type=float, default=KEYWORD_DELAY_RANGE[1], help="Maximum delay between keyword searches in seconds.")
    parser.add_argument("--timeout", type=int, default=60, help="Browser navigation timeout in seconds.")
    parser.add_argument("--slow-mo", type=int, default=0, help="Playwright slow motion delay in milliseconds.")
    parser.add_argument("--headless", action="store_true", help="Run without showing the browser window. CAPTCHA handling requires visible mode.")
    parser.add_argument("--browser", choices=("auto", "chrome", "edge", "playwright"), default="auto", help="Browser executable to use.")
    parser.add_argument("--no-github-upload", action="store_false", dest="github_upload", default=True, help="Disable automatic GitHub upload after saving cumulative files.")
    parser.add_argument("--github-upload", action="store_true", dest="github_upload", help="Enable automatic GitHub upload after saving cumulative files.")
    return parser.parse_args()


def main():
    print("=" * 55)
    print("  S2B browser cumulative crawler")
    print("=" * 55)
    args = parse_args()
    try:
        args.page_delay_range = validate_delay_range(args.page_delay_min, args.page_delay_max, "--page-delay")
        args.keyword_delay_range = validate_delay_range(args.keyword_delay_min, args.keyword_delay_max, "--keyword-delay")
        date_from, date_to = get_date_range_from_user(args)
        keywords = get_keywords_from_user(args)
    except ValueError as exc:
        print("[error] " + str(exc))
        return

    results = fetch_all_browser(date_from, date_to, keywords, args)
    data = update_cumulative_json(results, date_from, date_to)
    save_cumulative_html(data)
    try:
        publish_to_github(date_from, date_to, args.github_upload)
    except Exception as exc:
        print("[github] upload failed, but local files were saved: " + str(exc))
    print("done.")
    if getattr(sys, "frozen", False):
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()

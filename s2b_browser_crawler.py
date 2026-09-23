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
    DEFAULT_CHUNK_DAYS,
    KEYWORD_DELAY_RANGE,
    KEYWORDS,
    LIST_URL,
    MAX_PAGES_BY_KEYWORD,
    MAX_PAGES_PER_KEYWORD,
    PAGE_DELAY_RANGE,
    STATUS_CAPTCHA,
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_PARTIAL,
    build_jobs,
    display_date,
    filter_records,
    finalize_run,
    get_date_range_from_user,
    is_captcha,
    job_kind,
    load_checkpoint,
    merge_job_results,
    parse_page,
    parse_backfill_terms,
    pending_checkpoint_results,
    print_coverage,
    print_job_plan,
    record_ledger,
    save_checkpoint,
    select_keywords,
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


def close_context(context, cleanup_profile=True):
    profile_dir = getattr(context, "s2b_user_data_dir", "")
    try:
        context.close()
    except Exception:
        pass
    if cleanup_profile:
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


def create_context_page(playwright, args, user_data_dir=None):
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

    if user_data_dir is None:
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
    if page.is_closed():
        raise RuntimeError("Target page, context or browser has been closed")
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


def fetch_by_keyword_browser(page, search_term, date_from, date_to, page_delay_range, timeout_ms, PlaywrightTimeoutError, match_keyword=None, backfill_terms=None, checkpoint=None, kind="normal"):
    # 반환: (results, page, status). 페이지마다 체크포인트를 남기므로 중간에 끊겨도 이어서 수집한다.
    match_keyword = match_keyword or search_term
    backfill_terms = backfill_terms or []
    max_pages = MAX_PAGES_BY_KEYWORD.get(match_keyword, MAX_PAGES_PER_KEYWORD)

    results = list(checkpoint.get("records", [])) if checkpoint else []
    page_no = int(checkpoint.get("next_page", 1)) if checkpoint else 1
    saw_data_page = page_no > 1
    if checkpoint:
        print("    [checkpoint] " + str(page_no) + "페이지부터 이어서 수집 (저장된 " + str(len(results)) + "건)")
    status = STATUS_COMPLETE

    def save_progress(next_page, state):
        save_checkpoint(search_term, match_keyword, date_from, date_to, kind, results, next_page, state)

    first_request = True
    while max_pages is None or page_no <= max_pages:
        if not first_request:
            sleep_random(page_delay_range, "request delay")

        try:
            # 첫 요청(또는 이어서 수집하는 첫 페이지)은 검색 폼을 pageNo와 함께 새로 제출하고,
            # 그 뒤로는 결과 화면의 goList()로 넘어간다.
            if first_request:
                submit_search(page, search_term, date_from, date_to, page_no, timeout_ms)
            else:
                go_result_page(page, page_no, timeout_ms, PlaywrightTimeoutError)
            first_request = False
            wait_after_navigation(page, timeout_ms, PlaywrightTimeoutError)
        except Exception as exc:
            print("    browser error: " + str(exc))
            if is_page_closed_error(exc) and results:
                print("    page closed after partial results. keeping this keyword's collected records.")
                save_progress(page_no, STATUS_PARTIAL)
                return results, page, STATUS_PARTIAL
            raise

        try:
            page, captcha_was_solved = wait_for_manual_captcha(page, search_term, page_no, PlaywrightTimeoutError)
            if captcha_was_solved:
                records, has_data = parse_page(page_content_bytes(page))
                if has_data:
                    print("    [captcha] result table loaded after CAPTCHA.")
                else:
                    try:
                        print("    [captcha] no result table after CAPTCHA. retrying the search in the same browser session...")
                        submit_search(page, search_term, date_from, date_to, page_no, timeout_ms)
                        wait_after_navigation(page, timeout_ms, PlaywrightTimeoutError)
                    except Exception as exc:
                        print("    browser error after CAPTCHA: " + str(exc))
                        if is_page_closed_error(exc) and results:
                            print("    page closed after CAPTCHA. keeping this keyword's collected records.")
                            save_progress(page_no, STATUS_PARTIAL)
                            return results, page, STATUS_PARTIAL
                        raise

                    page, captcha_again = wait_for_manual_captcha(page, search_term, page_no, PlaywrightTimeoutError)
                    if captcha_again:
                        print("    [captcha] CAPTCHA appeared again after retry. Keeping collected records and stopping this keyword.")
                        save_debug_page(page, search_term, page_no, "captcha_repeated")
                        status = STATUS_CAPTCHA
                        break
                    records, has_data = parse_page(page_content_bytes(page))
            else:
                records, has_data = parse_page(page_content_bytes(page))
        except Exception as exc:
            print("    browser error while reading page: " + str(exc))
            if is_page_closed_error(exc) and results:
                print("    page closed after partial results. keeping this keyword's collected records.")
                save_progress(page_no, STATUS_PARTIAL)
                return results, page, STATUS_PARTIAL
            raise

        if not has_data:
            if page_no > 1 and saw_data_page:
                print("    page " + str(page_no) + ": no more results")
                break
            print("    no parsable result table. url: " + page.url)
            save_debug_page(page, search_term, page_no, "no_table")
            break

        saw_data_page = True
        keyword_matched = [record for record in records if match_keyword in record["계약명"]]
        filtered = filter_records(records, match_keyword, backfill_terms)
        excluded_count = len(keyword_matched) - len(filtered)
        keyword_miss_count = len(records) - len(keyword_matched)
        results.extend(filtered)
        print(
            "    page " + str(page_no) + ": " + str(len(records)) + " recv, "
            + str(len(filtered)) + " matched"
            + ", " + str(excluded_count) + " excluded"
            + ", " + str(keyword_miss_count) + " keyword-miss"
        )
        save_progress(page_no + 1, STATUS_PARTIAL)

        if len(records) == 0:
            break
        page_no += 1

    save_progress(page_no, status)
    return results, page, status


MAX_CONSECUTIVE_JOB_FAILURES = 3


def browser_search_jobs(keywords, date_from, date_to, args):
    # (\uae30\uac04 \uc870\uac01 x \ud0a4\uc6cc\ub4dc x \uc811\ub450\uc5b4) \uc791\uc5c5 \ubaa9\ub85d. \uc6d0\uc7a5\uc5d0 \uc644\ub8cc \uae30\ub85d\uc774 \uc788\ub294 \uc870\uac01\uc740 \uac74\ub108\ub6f4\ub2e4.
    chunk_days = getattr(args, "chunk_days", DEFAULT_CHUNK_DAYS)
    recrawl = getattr(args, "recrawl", False)
    search_prefixes = getattr(args, "search_prefixes", None) or []
    backfill_terms = getattr(args, "backfill_terms", None) or []

    if search_prefixes:
        jobs, skipped, chunks = [], [], []
        for prefix in search_prefixes:
            prefix_jobs, prefix_skipped, chunks = build_jobs(
                keywords, date_from, date_to, chunk_days, recrawl, job_kind(search_prefix=prefix), prefix
            )
            for job in prefix_jobs:
                job["backfill_terms"] = [prefix]
            jobs.extend(prefix_jobs)
            skipped.extend(prefix_skipped)
        return jobs, skipped, chunks

    jobs, skipped, chunks = build_jobs(keywords, date_from, date_to, chunk_days, recrawl, job_kind(backfill_terms))
    for job in jobs:
        job["backfill_terms"] = backfill_terms
    return jobs, skipped, chunks


def fetch_all_browser(date_from, date_to, keywords, args):
    print("[period] " + display_date(date_from) + " ~ " + display_date(date_to))
    print("[keywords] " + ", ".join(keywords))
    if getattr(args, "search_prefixes", None):
        print("[search prefixes] " + ", ".join(args.search_prefixes))
    if getattr(args, "backfill_terms", None):
        print("[backfill excluded terms] " + ", ".join(args.backfill_terms))
    jobs, skipped, chunks = browser_search_jobs(keywords, date_from, date_to, args)
    print_job_plan(jobs, skipped, chunks, keywords)
    recrawl = getattr(args, "recrawl", False)

    collected = []
    summary = {STATUS_COMPLETE: 0, STATUS_PARTIAL: 0, STATUS_CAPTCHA: 0, STATUS_ERROR: 0}
    recovered = pending_checkpoint_results()
    if recovered:
        recovered_count = sum(len(records) for _, records in recovered)
        print("[checkpoint] \uc774\uc804 \uc2e4\ud589\uc758 \uc644\ub8cc \uccb4\ud06c\ud3ec\uc778\ud2b8 " + str(len(recovered)) + "\uac1c(" + str(recovered_count) + "\uac74)\ub97c \ud568\uaed8 \ubc18\uc601\ud569\ub2c8\ub2e4.")
        collected.extend(recovered)

    if not jobs:
        print("[jobs] \uc2e4\ud589\ud560 \uc791\uc5c5\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.")
        return merge_job_results(collected)

    sync_playwright, PlaywrightTimeoutError = import_playwright()
    session_profile_dir = get_user_data_dir()
    print("[browser] session profile: " + session_profile_dir)
    consecutive_failures = 0

    with sync_playwright() as playwright:
        context, page = create_context_page(playwright, args, session_profile_dir)
        try:
            for job_index, job in enumerate(jobs, 1):
                search_term = job["search_term"]
                match_keyword = job["match_keyword"]
                backfill_terms = job["backfill_terms"]
                chunk_from, chunk_to = job["from"], job["to"]
                kind = job["kind"]
                if page.is_closed():
                    print("[browser] page was closed. opening a new page.")
                    try:
                        page = context.new_page()
                        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                    except Exception:
                        close_context(context, cleanup_profile=False)
                        context, page = create_context_page(playwright, args, session_profile_dir)

                chunk_label = display_date(chunk_from) + ("~" + display_date(chunk_to) if chunk_from != chunk_to else "")
                print("[" + search_term + "] " + chunk_label + " searching in browser... (" + str(job_index) + "/" + str(len(jobs)) + ", keyword=" + match_keyword + ")")
                checkpoint = None if recrawl else load_checkpoint(search_term, chunk_from, chunk_to, kind)
                items = list(checkpoint.get("records", [])) if checkpoint else []
                status = STATUS_ERROR
                note = ""
                for attempt in range(3):
                    try:
                        items, page, status = fetch_by_keyword_browser(
                            page,
                            search_term,
                            chunk_from,
                            chunk_to,
                            args.page_delay_range,
                            args.timeout * 1000,
                            PlaywrightTimeoutError,
                            match_keyword,
                            backfill_terms,
                            checkpoint,
                            kind,
                        )
                        break
                    except Exception as exc:
                        note = str(exc)[:200]
                        if not is_page_closed_error(exc):
                            # \uc774 \uad6c\uac04\ub9cc \uc624\ub958\ub85c \uae30\ub85d\ud558\uace0 \ub2e4\uc74c \uc791\uc5c5\uc73c\ub85c \ub118\uc5b4\uac04\ub2e4. \uc5f0\uc18d \uc2e4\ud328\uac00 \uc313\uc774\uba74 \uc911\ub2e8.
                            print("[browser] error on this job: " + str(exc))
                            break
                        if attempt == 2:
                            print("[browser] closed repeatedly. skipping this job and continuing.")
                            close_context(context, cleanup_profile=False)
                            context, page = create_context_page(playwright, args, session_profile_dir)
                            break
                        print("[browser] closed unexpectedly. reopening with the same session profile and retrying this job.")
                        close_context(context, cleanup_profile=False)
                        context, page = create_context_page(playwright, args, session_profile_dir)
                        checkpoint = load_checkpoint(search_term, chunk_from, chunk_to, kind)

                if status == STATUS_ERROR:
                    # 예외 직전까지 페이지 단위로 저장된 최신 체크포인트를 유지하고 상태만 error로 바꾼다.
                    latest = load_checkpoint(search_term, chunk_from, chunk_to, kind)
                    items = list(latest.get("records", [])) if latest else items
                    next_page = int(latest.get("next_page", 1)) if latest else 1
                    save_checkpoint(search_term, match_keyword, chunk_from, chunk_to, kind, items, next_page, STATUS_ERROR)
                latest = load_checkpoint(search_term, chunk_from, chunk_to, kind)
                pages_done = max(0, int(latest.get("next_page", 1)) - 1) if latest else 0
                record_ledger(search_term, chunk_from, chunk_to, status, pages_done, len(items), kind, "browser", note)
                summary[status] = summary.get(status, 0) + 1
                collected.append((match_keyword, items))
                print("  -> " + str(len(items)) + " found, " + status + "\n")

                consecutive_failures = consecutive_failures + 1 if status == STATUS_ERROR else 0
                if consecutive_failures >= MAX_CONSECUTIVE_JOB_FAILURES:
                    print("[browser] " + str(MAX_CONSECUTIVE_JOB_FAILURES) + "\uac1c \uc791\uc5c5\uc774 \uc5f0\uc18d\uc73c\ub85c \uc2e4\ud328\ud574 \uc911\ub2e8\ud569\ub2c8\ub2e4. \uc9c0\uae08\uae4c\uc9c0 \uc218\uc9d1\ud55c \uacb0\uacfc\ub294 \uc800\uc7a5\ud569\ub2c8\ub2e4.")
                    break

                if page.is_closed():
                    print("[browser] page is closed. opening a new page for the next job.")
                    try:
                        page = context.new_page()
                        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                    except Exception:
                        close_context(context, cleanup_profile=False)
                        context, page = create_context_page(playwright, args, session_profile_dir)

                if job_index != len(jobs):
                    sleep_random(args.keyword_delay_range, "keyword delay")
        finally:
            close_context(context, cleanup_profile=False)
            cleanup_user_data_dir(session_profile_dir)

    all_results = merge_job_results(collected)
    print("=" * 55)
    print("\uc774\ubc88 \uac80\uc0c9 \uacb0\uacfc: " + str(len(all_results)) + "\uac74 (\uc911\ubcf5 \uc81c\uac70)")
    print("\uad6c\uac04 \uacb0\uacfc: \uc644\ub8cc " + str(summary[STATUS_COMPLETE]) + ", \ubd80\ubd84 " + str(summary[STATUS_PARTIAL])
          + ", CAPTCHA " + str(summary[STATUS_CAPTCHA]) + ", \uc624\ub958 " + str(summary[STATUS_ERROR]))
    if summary[STATUS_CAPTCHA] or summary[STATUS_ERROR] or summary[STATUS_PARTIAL]:
        print("\ubbf8\uc644\ub8cc \uad6c\uac04\uc740 \uac19\uc740 \uba85\ub839\uc744 \ub2e4\uc2dc \uc2e4\ud589\ud558\uba74 \ub9c8\uc9c0\ub9c9 \ud398\uc774\uc9c0\ubd80\ud130 \uc774\uc5b4\uc11c \uc218\uc9d1\ud569\ub2c8\ub2e4.")
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
    parser.add_argument("--high-school-backfill", action="store_true", help="고등학교를 각 키워드 앞에 붙여 누락 가능성이 있는 계약을 브라우저로 다시 수집합니다.")
    parser.add_argument("--search-prefix", help="쉼표로 지정한 단어를 각 키워드 앞에 붙여 S2B 검색어를 좁힙니다. 예: 고등학교")
    parser.add_argument("--backfill-excluded", help="쉼표로 지정한 단어 때문에 과거에 제외됐을 가능성이 있는 계약명만 다시 수집합니다. 예: 고등학교,체육")
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS, help="긴 기간을 며칠 단위로 잘라 수집할지 지정합니다. 0이면 자르지 않습니다. 기본 " + str(DEFAULT_CHUNK_DAYS))
    parser.add_argument("--recrawl", action="store_true", help="원장에 완료 기록이 있어도 건너뛰지 않고 다시 수집합니다. 체크포인트도 무시합니다.")
    parser.add_argument("--coverage", action="store_true", help="키워드별 수집 완료 기간을 출력하고 종료합니다.")
    parser.add_argument("--no-github-upload", action="store_false", dest="github_upload", default=True, help="Disable automatic GitHub upload after saving cumulative files.")
    parser.add_argument("--github-upload", action="store_true", dest="github_upload", help="Enable automatic GitHub upload after saving cumulative files.")
    return parser.parse_args()


def main():
    print("=" * 55)
    print("  S2B browser cumulative crawler")
    print("=" * 55)
    args = parse_args()
    if args.coverage:
        try:
            print_coverage(select_keywords(args))
        except ValueError as exc:
            print("[error] " + str(exc))
        return
    try:
        args.page_delay_range = validate_delay_range(args.page_delay_min, args.page_delay_max, "--page-delay")
        args.keyword_delay_range = validate_delay_range(args.keyword_delay_min, args.keyword_delay_max, "--keyword-delay")
        args.search_prefixes = parse_backfill_terms(args.search_prefix)
        if args.high_school_backfill and "고등학교" not in args.search_prefixes:
            args.search_prefixes.insert(0, "고등학교")
        args.backfill_terms = parse_backfill_terms(args.backfill_excluded)
        date_from, date_to = get_date_range_from_user(args)
        keywords = get_keywords_from_user(args)
    except ValueError as exc:
        print("[error] " + str(exc))
        return

    results = fetch_all_browser(date_from, date_to, keywords, args)
    if (args.backfill_terms or args.search_prefixes) and not results:
        print("[backfill] no matching records found; cumulative files were not changed.")
        print("done.")
        if getattr(sys, "frozen", False):
            input("Press Enter to exit...")
        return
    finalize_run(results, date_from, date_to, args.github_upload)
    print("done.")
    if getattr(sys, "frozen", False):
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()

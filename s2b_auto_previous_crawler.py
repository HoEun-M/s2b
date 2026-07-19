# coding: utf-8
import sys
from types import SimpleNamespace

from s2b_browser_crawler import fetch_all_browser
from s2b_local_crawler import (
    KEYWORDS,
    PAGE_DELAY_RANGE,
    display_date,
    normalize_date,
    previous_workday_range,
    publish_to_github,
    save_cumulative_html,
    update_cumulative_json,
    validate_delay_range,
)

GROUPS = {
    1: [1, 2],
    2: [3, 4, 5, 6, 7],
    3: [8, 9, 10, 11, 12, 13, 14],
    4: [15, 16, 17, 18, 19, 20],
    5: [21, 22, 23, 24, 25, 26, 27, 28],
}

KEYWORD_DELAY_AROUND_3_MINUTES = (150.0, 210.0)


def keywords_for_group(group_no):
    indexes = GROUPS[group_no]
    return [KEYWORDS[index - 1] for index in indexes]


def make_args():
    return SimpleNamespace(
        browser="auto",
        headless=False,
        slow_mo=0,
        timeout=60,
        github_upload=True,
        page_delay_min=PAGE_DELAY_RANGE[0],
        page_delay_max=PAGE_DELAY_RANGE[1],
        keyword_delay_min=KEYWORD_DELAY_AROUND_3_MINUTES[0],
        keyword_delay_max=KEYWORD_DELAY_AROUND_3_MINUTES[1],
        page_delay_range=validate_delay_range(PAGE_DELAY_RANGE[0], PAGE_DELAY_RANGE[1], "--page-delay"),
        keyword_delay_range=validate_delay_range(
            KEYWORD_DELAY_AROUND_3_MINUTES[0],
            KEYWORD_DELAY_AROUND_3_MINUTES[1],
            "--keyword-delay",
        ),
    )


def run_group(group_no):
    if group_no not in GROUPS:
        raise ValueError("group_no must be 1 through 5")

    date_from, date_to, holidays = previous_workday_range()
    date_from = normalize_date(date_from)
    date_to = normalize_date(date_to)
    keywords = keywords_for_group(group_no)
    args = make_args()

    print("=" * 55)
    print("  S2B automatic previous-workday crawler #" + str(group_no))
    print("=" * 55)
    print("[auto period] " + display_date(date_from) + " ~ " + display_date(date_to))
    if holidays:
        print("[holidays] " + ", ".join(holidays))
    print("[keyword group " + str(group_no) + "] " + ", ".join(keywords))
    print("[page delay] " + str(PAGE_DELAY_RANGE[0]) + "~" + str(PAGE_DELAY_RANGE[1]) + "s")
    print("[keyword delay] 150~210s")
    print("")

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
    run_group(1)

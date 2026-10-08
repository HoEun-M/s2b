# coding: utf-8
"""manual_contracts.json의 계약을 s2b_cumulative.json에 반영한다 (키워드 수집으로 잡히지 않는 계약 보정용, 여러 번 실행해도 안전)."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2b_local_crawler as local  # noqa: E402

MANUAL_FILE = os.path.join(local.APP_DIR, "manual_contracts.json")


def main():
    with io.open(MANUAL_FILE, encoding="utf-8") as file:
        items = json.load(file)
    results = []
    for item in items:
        result = {key: value for key, value in item.items() if key != "비고"}
        result["매칭키워드"] = local.tag_keywords(result["계약명"])
        results.append(result)
    dates = sorted(item["계약체결일"].replace("-", "") for item in items)
    data = local.update_cumulative_json(results, dates[0], dates[-1])

    notes = {str(item["계약번호"]): item.get("비고", "") for item in items}
    changed = 0
    for record in data["records"]:
        note = notes.get(str(record.get("tender_no") or record.get("id")))
        if note and record.get("manual_note") != note:
            record["manual_note"] = note
            changed += 1
    if changed:
        with io.open(local.CUMULATIVE_JSON_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    local.save_cumulative_html(local.load_cumulative_json())
    print("[manual] 반영:", len(results), "건")


if __name__ == "__main__":
    main()

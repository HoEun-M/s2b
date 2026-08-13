# coding: utf-8
import json
from pathlib import Path

import s2b_local_crawler as local


def region_from_business_place(value):
    return local.short_region(value or "")


def region_from_school_name(value):
    direct = local.region_from_institution_name(value or "")
    return direct.get("region", "") if direct else ""


def main():
    json_path = Path(local.CUMULATIVE_JSON_FILE)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    filled_business = 0
    filled_name = 0
    support_filled = 0

    for row in data.get("records", []):
        if (row.get("region") or "").strip():
            continue

        region = region_from_business_place(row.get("business_place", ""))
        source = "business_place" if region else ""
        if not region:
            region = region_from_school_name(row.get("institution", ""))
            source = "institution_name" if region else ""

        if not region:
            continue

        row["region"] = region
        row["region_status"] = "matched"
        row["region_source"] = source
        if source == "business_place":
            filled_business += 1
        else:
            filled_name += 1

        support_office = local.infer_support_office(
            region,
            row.get("institution", ""),
            row.get("region_candidates", []),
            row.get("business_place", ""),
        )
        if support_office and not row.get("support_office"):
            row["support_office"] = support_office
            support_filled += 1

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    local.save_cumulative_html(data)
    print("[done] business_place=" + str(filled_business) + ", institution_name=" + str(filled_name) + ", support_office=" + str(support_filled))


if __name__ == "__main__":
    main()

import csv
import json
import sys
import time
from collections import Counter

import s2b_local_crawler as crawler

sys.stdout.reconfigure(encoding="utf-8")


def is_school_name(value):
    name = crawler.normalize_school_name(value or "")
    return any(suffix in name for suffix in crawler.SCHOOL_SUFFIXES)


def choose_candidate(row, candidates):
    if not candidates:
        return None, "not_found"
    current_region = row.get("region", "")
    exact_name = crawler.normalize_school_name(row.get("institution", ""))
    exact = [candidate for candidate in candidates if crawler.normalize_school_name(candidate.get("school_name", "")) == exact_name]
    pool = exact or candidates
    if current_region:
        region_pool = [candidate for candidate in pool if candidate.get("region", "") == current_region]
        if len(region_pool) == 1:
            return region_pool[0], "matched_current_region"
        if len(region_pool) > 1:
            return None, "ambiguous_current_region"
    unique_regions = sorted({candidate.get("region", "") for candidate in pool if candidate.get("region", "")})
    unique_names = sorted({crawler.normalize_school_name(candidate.get("school_name", "")) for candidate in pool if candidate.get("school_name", "")})
    if len(pool) == 1:
        return pool[0], "matched_unique"
    if len(unique_regions) == 1 and len(unique_names) == 1:
        return pool[0], "matched_same_school"
    return None, "ambiguous"


data = crawler.load_cumulative_json()
records = data.get("records", [])
pre_filled = 0
for row in records:
    if row.get("subregion", ""):
        continue
    school_override = crawler.SCHOOL_DISTRICT_OVERRIDES.get(crawler.normalize_school_name(row.get("institution", "")))
    if school_override:
        region = school_override[0]
        support = crawler.support_office_from_region_district(region, school_override[1])
    else:
        region, support = crawler.normalize_record_region_support(row.get("region", ""), row.get("support_office", ""))
    subregion = crawler.infer_subregion(
        region,
        support,
        row.get("institution", ""),
        row.get("region_candidates", []),
        row.get("business_place", ""),
    )
    if subregion:
        row["region"] = region
        row["support_office"] = support
        row["subregion"] = subregion
        pre_filled += 1
targets = [
    row for row in records
    if not row.get("subregion", "") and is_school_name(row.get("institution", ""))
]

by_school = {}
for row in targets:
    key = crawler.normalize_school_name(row.get("institution", ""))
    by_school.setdefault(key, row.get("institution", ""))

candidate_cache = {}
updated_by_school = {}
review_rows = []

for index, (school_key, school_name) in enumerate(sorted(by_school.items()), start=1):
    candidates = crawler.fetch_school_candidates(school_name)
    candidate_cache[school_key] = candidates
    print(f"[{index}/{len(by_school)}] {school_name}: {len(candidates)} candidate(s)")
    time.sleep(0.15)

changed = 0
for row in records:
    if row.get("subregion", "") or not is_school_name(row.get("institution", "")):
        continue
    key = crawler.normalize_school_name(row.get("institution", ""))
    candidate, status = choose_candidate(row, candidate_cache.get(key, []))
    if not candidate:
        review_rows.append({
            "id": row.get("id", ""),
            "institution": row.get("institution", ""),
            "contract_name": row.get("contract_name", ""),
            "current_region": row.get("region", ""),
            "current_support_office": row.get("support_office", ""),
            "status": status,
            "candidate_count": len(candidate_cache.get(key, [])),
            "candidate_summary": " | ".join(
                f"{item.get('school_name','')} / {item.get('region','')} / {item.get('district','')} / {item.get('support_office','')}"
                for item in candidate_cache.get(key, [])[:5]
            ),
        })
        continue
    old_region = row.get("region", "")
    old_support = row.get("support_office", "")
    region = candidate.get("region", "") or old_region
    support = candidate.get("support_office", "") or crawler.support_office_from_region_district(region, candidate.get("district", "")) or old_support
    region, support = crawler.normalize_record_region_support(region, support)
    subregion = crawler.infer_subregion(region, support, row.get("institution", ""), [candidate], row.get("business_place", ""))
    if not subregion:
        subregion = crawler.support_office_unit_label(region, support)
    row["region"] = region
    row["support_office"] = support
    row["subregion"] = subregion
    if subregion:
        row["region_status"] = "matched"
        row["region_source"] = "neis_school_info_subregion_backfill"
    row["region_candidates"] = candidate_cache.get(key, [])
    updated_by_school[key] = (row.get("institution", ""), region, support, subregion)
    if old_region != region or old_support != support or subregion:
        changed += 1

crawler.backfill_record_subregions(records)

with open(crawler.CUMULATIVE_JSON_FILE, "w", encoding="utf-8") as file:
    json.dump(data, file, ensure_ascii=False, indent=2)

crawler.save_cumulative_html(data)

with open("outputs/subregion_neis_review.csv", "w", encoding="utf-8-sig", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=[
        "id", "institution", "contract_name", "current_region", "current_support_office",
        "status", "candidate_count", "candidate_summary",
    ])
    writer.writeheader()
    writer.writerows(review_rows)

status_counts = Counter(row["status"] for row in review_rows)
print("target_rows", len(targets))
print("pre_filled", pre_filled)
print("unique_schools", len(by_school))
print("changed_rows", changed)
print("updated_schools", len(updated_by_school))
print("review_rows", len(review_rows), dict(status_counts))
print("review_file", "outputs/subregion_neis_review.csv")

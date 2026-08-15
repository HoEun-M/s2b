# coding: utf-8
import json
from collections import defaultdict
from pathlib import Path

import s2b_local_crawler as local


SUPPORT_OFFICE_ALIASES = {
    "동래교육지원청": ("부산", "부산광역시동래교육지원청"),
    "해운대교육지원청": ("부산", "부산광역시해운대교육지원청"),
    "서부교육지원청": ("부산", "부산광역시서부교육지원청"),
    "남부교육지원청": ("부산", "부산광역시남부교육지원청"),
    "북부교육지원청": ("부산", "부산광역시북부교육지원청"),
}

LEGACY_DISTRICT_PREFIXES = {
    "진해": ("경남", "창원시"),
    "마산": ("경남", "창원시"),
}


def normalize(value):
    return "".join(str(value or "").split())


def is_missing_region(row):
    region = (row.get("region") or "").strip()
    return not region or region == "미지정"


def build_confirmed_maps(rows):
    regions_by_institution = defaultdict(set)
    supports_by_institution_region = defaultdict(set)
    regions_by_school_text = defaultdict(set)
    supports_by_school_region = defaultdict(set)

    for row in rows:
        region = (row.get("region") or "").strip()
        support = (row.get("support_office") or "").strip()
        if not region or region == "미지정":
            continue

        institution = normalize(row.get("institution"))
        if institution:
            regions_by_institution[institution].add(region)
            if support:
                supports_by_institution_region[(institution, region)].add(support)

        for value in (row.get("institution"), row.get("business_place")):
            key = normalize(value)
            if key and any(suffix in key for suffix in local.SCHOOL_SUFFIXES):
                regions_by_school_text[key].add(region)
                if support:
                    supports_by_school_region[(key, region)].add(support)

    return (
        regions_by_institution,
        supports_by_institution_region,
        regions_by_school_text,
        supports_by_school_region,
    )


def build_unique_district_prefixes():
    by_prefix = defaultdict(set)
    for region, district_map in local.REGION_DISTRICT_PREFIXES.items():
        for prefix, district in district_map.items():
            by_prefix[prefix].add((region, district))
    return {
        prefix: next(iter(values))
        for prefix, values in by_prefix.items()
        if len(values) == 1
    }


def unique_value(values):
    cleaned = {value for value in values if value}
    return next(iter(cleaned)) if len(cleaned) == 1 else ""


def support_from_known_sets(sets, key, region):
    return unique_value(sets.get((key, region), set()))


def match_support_office_name(row):
    for value in (row.get("institution"), row.get("business_place")):
        text = value or ""
        support = local.support_office_from_institution(text)
        region = local.region_from_support_office(support)
        if region and support:
            return region, support
        for alias, result in SUPPORT_OFFICE_ALIASES.items():
            if alias in text:
                return result
    return "", ""


def match_unique_candidate(row):
    candidates = row.get("region_candidates") or []
    regions = {candidate.get("region") for candidate in candidates if candidate.get("region")}
    if len(regions) != 1:
        return "", ""
    region = next(iter(regions))
    supports = {
        candidate.get("support_office")
        for candidate in candidates
        if candidate.get("region") == region and candidate.get("support_office")
    }
    return region, unique_value(supports)


def match_unique_district_prefix(row, prefix_map):
    texts = [
        normalize(row.get("institution")),
        normalize(row.get("business_place")),
    ]
    for text in texts:
        if not text:
            continue
        for prefix, (region, district) in sorted(prefix_map.items(), key=lambda item: len(item[0]), reverse=True):
            if text.startswith(prefix):
                return region, local.support_office_from_region_district(region, district)
        for prefix, (region, district) in LEGACY_DISTRICT_PREFIXES.items():
            if text.startswith(prefix):
                return region, local.support_office_from_region_district(region, district)
    return "", ""


def set_region(row, region, support, source):
    row["region"] = region
    row["region_status"] = "matched"
    row["region_source"] = source
    if support:
        row["support_office"] = support
        row["support_office_source"] = source


def main():
    json_path = Path(local.CUMULATIVE_JSON_FILE)
    overrides_path = json_path.with_name("region_overrides.json")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    overrides = json.loads(overrides_path.read_text(encoding="utf-8-sig")) if overrides_path.exists() else {}
    if not isinstance(overrides, dict):
        overrides = {}

    rows = data.get("records", [])
    (
        regions_by_institution,
        supports_by_institution_region,
        regions_by_school_text,
        supports_by_school_region,
    ) = build_confirmed_maps(rows)
    prefix_map = build_unique_district_prefixes()

    counts = defaultdict(int)
    samples = []

    for row in rows:
        if not is_missing_region(row):
            continue

        region = support = source = ""

        region, support = match_support_office_name(row)
        if region:
            source = "support_office_name"

        institution_key = normalize(row.get("institution"))
        if not region and institution_key:
            region = unique_value(regions_by_institution.get(institution_key, set()))
            if region:
                support = support_from_known_sets(supports_by_institution_region, institution_key, region)
                source = "same_institution"

        if not region:
            for value in (row.get("business_place"), row.get("institution")):
                school_key = normalize(value)
                region = unique_value(regions_by_school_text.get(school_key, set()))
                if region:
                    support = support_from_known_sets(supports_by_school_region, school_key, region)
                    source = "same_school_name"
                    break

        if not region:
            region, support = match_unique_candidate(row)
            if region:
                source = "unique_neis_candidate"

        if not region:
            region, support = match_unique_district_prefix(row, prefix_map)
            if region:
                source = "institution_district_prefix"

        if not region:
            continue

        set_region(row, region, support, source)
        record_id = row.get("id") or row.get("tender_no")
        if record_id:
            overrides[record_id] = region
        counts[source] += 1
        if len(samples) < 30:
            samples.append((record_id, source, region, support, row.get("institution", "")))

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    overrides_path.write_text(json.dumps(dict(sorted(overrides.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    local.save_cumulative_html(data)

    print("[done] " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    for record_id, source, region, support, institution in samples:
        print(f"[sample] {record_id} {source} -> {region} / {support or '-'} | {institution}")


if __name__ == "__main__":
    main()

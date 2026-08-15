import argparse
import json
import random
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import s2b_local_crawler as local


SUPABASE_URL = "https://fozuzbszeujgskjasvzq.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_bFJbCmjIbzCEracNlI-lhA_9hYn1rdc"

REGION_ALIASES = [
    ("서울특별시", "서울"), ("서울", "서울"),
    ("부산광역시", "부산"), ("부산", "부산"),
    ("대구광역시", "대구"), ("대구", "대구"),
    ("인천광역시", "인천"), ("인천", "인천"),
    ("광주광역시", "광주"), ("광주", "광주"),
    ("대전광역시", "대전"), ("대전", "대전"),
    ("울산광역시", "울산"), ("울산", "울산"),
    ("세종특별자치시", "세종"), ("세종", "세종"),
    ("경기도", "경기"), ("경기", "경기"),
    ("강원특별자치도", "강원"), ("강원도", "강원"), ("강원", "강원"),
    ("충청북도", "충북"), ("충북", "충북"),
    ("충청남도", "충남"), ("충남", "충남"),
    ("전북특별자치도", "전북"), ("전라북도", "전북"), ("전북", "전북"),
    ("전라남도", "전남"), ("전남", "전남"),
    ("경상북도", "경북"), ("경북", "경북"),
    ("경상남도", "경남"), ("경남", "경남"),
    ("제주특별자치도", "제주"), ("제주도", "제주"), ("제주", "제주"),
]

METRO_SUPPORT_OFFICE_BY_DISTRICT = {
    ("부산", "중구"): "부산광역시서부교육지원청",
    ("부산", "서구"): "부산광역시서부교육지원청",
    ("부산", "영도구"): "부산광역시서부교육지원청",
    ("부산", "사하구"): "부산광역시서부교육지원청",
    ("부산", "남구"): "부산광역시남부교육지원청",
    ("부산", "동구"): "부산광역시남부교육지원청",
    ("부산", "부산진구"): "부산광역시남부교육지원청",
    ("부산", "북구"): "부산광역시북부교육지원청",
    ("부산", "사상구"): "부산광역시북부교육지원청",
    ("부산", "강서구"): "부산광역시북부교육지원청",
    ("부산", "동래구"): "부산광역시동래교육지원청",
    ("부산", "금정구"): "부산광역시동래교육지원청",
    ("부산", "연제구"): "부산광역시동래교육지원청",
    ("부산", "해운대구"): "부산광역시해운대교육지원청",
    ("부산", "수영구"): "부산광역시해운대교육지원청",
    ("부산", "기장군"): "부산광역시해운대교육지원청",
    ("대전", "동구"): "대전광역시동부교육지원청",
    ("대전", "중구"): "대전광역시동부교육지원청",
    ("대전", "대덕구"): "대전광역시동부교육지원청",
    ("대전", "서구"): "대전광역시서부교육지원청",
    ("대전", "유성구"): "대전광역시서부교육지원청",
    ("울산", "중구"): "울산광역시강북교육지원청",
    ("울산", "동구"): "울산광역시강북교육지원청",
    ("울산", "북구"): "울산광역시강북교육지원청",
    ("울산", "남구"): "울산광역시강남교육지원청",
    ("울산", "울주군"): "울산광역시강남교육지원청",
    ("제주", "제주시"): "제주특별자치도제주시교육지원청",
    ("제주", "서귀포시"): "제주특별자치도서귀포시교육지원청",
}


def decode_html(content):
    return content.decode("euc-kr", errors="replace")


def extract_business_place(html):
    soup = BeautifulSoup(html, "lxml")
    labels = soup.find_all(string=lambda value: value and "사업장소" in value)
    for label in labels:
        label_cell = label.parent
        row = label_cell.find_parent("tr") if label_cell else None
        if not row:
            continue
        cells = row.find_all(["td", "th"])
        for index, cell in enumerate(cells):
            if "사업장소" in cell.get_text(" ", strip=True):
                if index + 1 < len(cells):
                    return cells[index + 1].get_text(" ", strip=True)
    return ""


def region_from_address(address):
    text = " ".join((address or "").split())
    for alias, region in sorted(REGION_ALIASES, key=lambda item: len(item[0]), reverse=True):
        if text.startswith(alias):
            return region
    return ""


def district_from_address(region, address):
    text = " ".join((address or "").split())
    for alias, alias_region in sorted(REGION_ALIASES, key=lambda item: len(item[0]), reverse=True):
        if alias_region == region and text.startswith(alias):
            text = text[len(alias):].strip()
            break
    district_match = local.ADDRESS_DISTRICT_PATTERN.search(text)
    return district_match.group(1) if district_match else ""


def support_office_from_address(region, address, row):
    if not region or not address:
        return ""
    district = district_from_address(region, address)
    if region == "세종":
        return "세종특별자치시교육청"
    support = METRO_SUPPORT_OFFICE_BY_DISTRICT.get((region, district))
    if support:
        return support
    support = local.support_office_from_region_district(region, district)
    if support:
        return support
    support = local.infer_support_office(
        region,
        row.get("institution", ""),
        row.get("region_candidates", []),
        address,
    )
    if not district and support and not local.support_office_matches_region(region, support):
        return ""
    return support


def is_missing_region(row):
    region = (row.get("region") or "").strip()
    return not region or region == "미지정"


def load_region_overrides(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def supabase_headers():
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": "Bearer " + SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def upsert_supabase_regions(rows):
    if not rows:
        return
    url = SUPABASE_URL + "/rest/v1/region_overrides?on_conflict=record_id"
    response = requests.post(url, headers=supabase_headers(), data=json.dumps(rows, ensure_ascii=False).encode("utf-8"), timeout=20)
    response.raise_for_status()


def save_data(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Fill missing S2B regions from detail-page business place addresses.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of missing-region records to inspect.")
    parser.add_argument("--delay-min", type=float, default=0.8)
    parser.add_argument("--delay-max", type=float, default=1.8)
    parser.add_argument("--save-every", type=int, default=25)
    parser.add_argument("--no-supabase", action="store_true")
    parser.add_argument("--recheck-business-place", action="store_true")
    args = parser.parse_args()

    json_path = Path(local.CUMULATIVE_JSON_FILE)
    overrides_path = json_path.with_name("region_overrides.json")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    region_overrides = load_region_overrides(overrides_path)
    if args.recheck_business_place:
        candidates = [row for row in data.get("records", []) if row.get("business_place")]
    else:
        candidates = [row for row in data.get("records", []) if is_missing_region(row) and row.get("link")]
    if args.limit:
        candidates = candidates[: args.limit]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Referer": local.LIST_URL + "?forwardName=list03",
    })

    checked = filled = skipped = captcha = errors = 0
    pending_upserts = []

    for row in candidates:
        checked += 1
        record_id = row.get("id") or row.get("tender_no")
        try:
            if args.recheck_business_place and row.get("business_place"):
                address = row.get("business_place", "")
            else:
                response = session.get(row["link"], timeout=20)
                response.raise_for_status()
                content = response.content
                if local.is_captcha(content):
                    captcha += 1
                    print(f"[captcha] stopped at {record_id}")
                    break
                address = extract_business_place(decode_html(content))
            region = region_from_address(address)
            if region:
                support_office = support_office_from_address(region, address, row)
                row["region"] = region
                row["region_status"] = "matched"
                row["region_source"] = "business_place"
                row["business_place"] = address
                if support_office:
                    row["support_office"] = support_office
                    row["support_office_source"] = "business_place"
                region_overrides[record_id] = region
                pending_upserts.append({"record_id": record_id, "region": region})
                filled += 1
                print(f"[fill] {record_id} -> {region} / {support_office or '-'} | {address[:60]}")
            else:
                if address:
                    row["business_place"] = address
                skipped += 1
                print(f"[skip] {record_id} | {address[:80] if address else 'no business place'}")

            if pending_upserts and (len(pending_upserts) >= args.save_every):
                if not args.no_supabase:
                    upsert_supabase_regions(pending_upserts)
                pending_upserts = []
                save_data(json_path, data)
                save_data(overrides_path, region_overrides)
                print(f"[checkpoint] checked={checked}, filled={filled}, skipped={skipped}, errors={errors}")

            time.sleep(random.uniform(args.delay_min, args.delay_max))
        except Exception as exc:
            errors += 1
            print(f"[error] {record_id}: {exc}")

    if pending_upserts and not args.no_supabase:
        upsert_supabase_regions(pending_upserts)
    save_data(json_path, data)
    save_data(overrides_path, region_overrides)
    local.save_cumulative_html(data)
    print(f"[done] checked={checked}, filled={filled}, skipped={skipped}, captcha={captcha}, errors={errors}")


if __name__ == "__main__":
    main()

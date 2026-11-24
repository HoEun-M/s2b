# coding: utf-8
"""코스웨어 구매 내역 읽기 전용 파일 만들기.

누적 데이터(학교장터 계약 + 나라장터 교육기관 계약)에서 구매 유형이 '코스웨어'인 계약만 뽑아
    courseware_purchases.json   (프로그램이 읽는 용도)
    courseware_purchases.csv    (엑셀로 여는 용도, UTF-8 BOM)
두 파일로 저장한다. 매일 새벽 수집(daily_crawl.py)이 끝날 때 다시 만들어 GitHub Pages에 함께 올린다.
    https://hoeun-m.github.io/s2b/courseware_purchases.json
    https://hoeun-m.github.io/s2b/courseware_purchases.csv

읽는 쪽은 이 주소를 읽기만 한다 (원본은 이 저장소에서만 바뀐다).
유형 분류·업체 정리(유통사 -> 제조사)·삭제 보정·지역 보정은 통합 대시보드와 같은 기준이다.
공개 조달 계약 정보만 담는다 (개인정보 없음).

    python export_courseware.py            # 파일 만들기
    python export_courseware.py --summary  # 만들지 않고 건수만 보기
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

import s2b_local_crawler as local

APP_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(APP_DIR, "courseware_purchases.json")
CSV_FILE = os.path.join(APP_DIR, "courseware_purchases.csv")
PAGE_URL = "https://hoeun-m.github.io/s2b/"
# 통합 대시보드가 읽는 수동 보정(삭제·지역) 표. 공개 읽기 키로 조회만 한다.
SUPABASE_URL = os.environ.get("S2B_SUPABASE_URL", "https://fozuzbszeujgskjasvzq.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("S2B_SUPABASE_KEY", "sb_publishable_bFJbCmjIbzCEracNlI-lhA_9hYn1rdc")

FIELDS = [  # (JSON 키, CSV 머리글, 설명)
    ("contract_date", "계약일", "계약 체결일 (YYYY-MM-DD)"),
    ("source", "출처", "학교장터 또는 나라장터"),
    ("institution", "기관", "계약한 학교·기관 이름"),
    ("school_level", "학교급", "초·중·고·기타"),
    ("school_type", "학교구분", "크롤러가 붙인 학교 구분"),
    ("region", "시도", "시도 (없으면 미지정)"),
    ("subregion", "세부지역", "시·군·구 또는 교육지원청 단위"),
    ("support_office", "교육지원청", "관할 교육지원청"),
    ("contract_name", "계약명", "계약명 원문"),
    ("vendor", "업체", "통계 기준 업체명 (유통사 계약은 제품을 만든 제조사, 표기 통일)"),
    ("counterpart", "계약대상자", "실제 계약 상대 (원래 표기)"),
    ("amount", "금액", "계약 금액 (원)"),
    ("link", "원문링크", "학교장터·나라장터 원문"),
    ("id", "계약ID", "계약 고유 번호"),
]


def fetch_supabase(table, select):
    """수동 보정 표를 끝까지 읽는다 (한 번에 1,000행). 실패하면 None."""
    try:
        import requests
        rows = []
        for offset in range(0, 200000, 1000):
            response = requests.get(
                SUPABASE_URL + "/rest/v1/" + table,
                params={"select": select, "order": "record_id", "limit": 1000, "offset": offset},
                headers={"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY}, timeout=30)
            response.raise_for_status()
            page = response.json()
            rows.extend(page)
            if len(page) < 1000:
                break
        return rows
    except Exception as exc:
        print("[courseware] 수동 보정(" + table + ")을 읽지 못했습니다: " + str(exc)[:120])
        return None


def amount_number(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else 0


def build_records():
    """반환: (레코드 목록, 메모). 통합 대시보드의 '코스웨어' 유형과 같은 계약."""
    s2b_rows = local.load_cumulative_json().get("records", [])
    g2b_rows = [row for row in local.load_g2b_rows() if row.get("g2b_kind", "contract") == "contract"]
    rows = s2b_rows + g2b_rows
    canonical = local.build_vendor_canonical([row.get("counterpart", "") or "미지정" for row in s2b_rows + local.load_g2b_rows()])

    deleted_rows = fetch_supabase("deleted_records", "record_id")
    region_rows = fetch_supabase("region_overrides", "record_id,region")
    deleted = {row.get("record_id") for row in deleted_rows or []}
    region_override = {row.get("record_id"): row.get("region") for row in region_rows or [] if row.get("region")}
    notes = []
    if deleted_rows is None or region_rows is None:
        notes.append("수동 보정(삭제·지역)을 읽지 못해 반영하지 않았습니다.")

    records = []
    for row in rows:
        record_id = str(row.get("id") or row.get("tender_no") or "")
        if not record_id or record_id in deleted:
            continue
        source = row.get("source") or "s2b"
        name = row.get("contract_name", "")
        counterpart = row.get("counterpart", "") or "미지정"
        type_key = local.final_purchase_type_key(record_id, name, row.get("institution", ""), counterpart, source)
        if type_key != "courseware":
            continue
        region, support_office = local.normalize_record_region_support(row.get("region", ""), row.get("support_office", ""))
        region = region_override.get(record_id) or region
        subregion = row.get("subregion", "") or ""
        region, subregion = local.consolidate_gwangju_region(region, subregion)
        records.append({
            "contract_date": row.get("contract_date", ""),
            "source": "나라장터" if source == "g2b" else "학교장터",
            "institution": row.get("institution", ""),
            "school_level": row.get("school_level", "") or "",
            "school_type": row.get("school_type", "") or "",
            "region": region or "미지정",
            "subregion": subregion,
            "support_office": support_office or "",
            "contract_name": name,
            "vendor": local.vendor_alias(name, counterpart, type_key, canonical),
            "counterpart": counterpart,
            "amount": amount_number(row.get("amount", "")),
            "link": row.get("link", ""),
            "id": record_id,
        })
    records.sort(key=lambda record: (record["contract_date"], record["id"]), reverse=True)
    return records, notes


def write_files(records, notes):
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "generated_at": generated_at,
        "description": "학교장터·나라장터 교육기관 계약 중 구매 유형이 코스웨어인 계약 (읽기 전용, 매일 새벽 갱신)",
        "source_page": PAGE_URL,
        "count": len(records),
        "total_amount": sum(record["amount"] for record in records),
        "notes": notes,
        "fields": {key: text for key, _, text in FIELDS},
        "records": records,
    }
    with open(JSON_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([header for _, header, _ in FIELDS])
        for record in records:
            writer.writerow([record[key] for key, _, _ in FIELDS])
    print("[courseware] saved: " + JSON_FILE)
    print("[courseware] saved: " + CSV_FILE)


def main(argv=None):
    parser = argparse.ArgumentParser(description="코스웨어 구매 내역 읽기 전용 파일 만들기")
    parser.add_argument("--summary", action="store_true", help="파일을 만들지 않고 건수만 본다")
    args = parser.parse_args(argv)
    records, notes = build_records()
    total = sum(record["amount"] for record in records)
    print("[courseware] 코스웨어 계약 %d건, %s원 (학교장터 %d건, 나라장터 %d건)" % (
        len(records), "{:,}".format(total),
        sum(1 for record in records if record["source"] == "학교장터"), sum(1 for record in records if record["source"] == "나라장터")))
    for note in notes:
        print("[courseware] " + note)
    if not args.summary:
        write_files(records, notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

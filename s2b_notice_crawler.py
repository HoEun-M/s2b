# coding: utf-8
"""학교장터(S2B) 입찰공고 · 발주계획 수집기.

학교장터 공개 화면(로그인 불필요)을 읽어 s2b_notices.json에 누적한다.
- 발주계획: 계약현황 > 발주 (tcmp100.do). 목록 + 상세(소재지·예산액·용도·주요규격).
- 입찰공고: 계약현황 > 입찰 공고현황 (stmo001.do). 목록(지역별로 조회해 시도를 붙임) + 상세(추정가격·기초금액·마감·개찰).
양이 적어(입찰공고는 전국 한 달 몇 건, 발주계획은 하루 0~3건) 키워드로 거르지 않고 모두 저장하고,
키워드는 표시(keywords)로만 붙인다. 담당자 이름·전화는 저장하지 않는다.
"""
import argparse
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

import s2b_local_crawler as local


BASE_URL = "https://www.s2b.kr"
PLAN_URL = BASE_URL + "/S2BNCustomer/tcmp100.do"
BID_URL = BASE_URL + "/S2BNCustomer/stmo001.do"
NOTICES_FILE = os.path.join(local.APP_DIR, "s2b_notices.json")
CHUNK_DAYS = 90            # 입찰공고 화면은 한 번에 4개월까지만 조회된다
DELAY_RANGE = (1.5, 3.5)   # 요청 사이 쉬는 시간(초). 공개 화면이지만 천천히 읽는다
BID_CITIES = ("강원", "경기", "경남", "경북", "광주", "대구", "대전", "부산", "서울", "세종", "울산", "인천", "전남", "전북", "제주", "충남", "충북")
SOURCES = ("plan", "bid")


def pause():
    time.sleep(random.uniform(*DELAY_RANGE))


def new_session():
    session = local.new_session()
    session.headers["Referer"] = BASE_URL + "/S2BNCustomer/S2B/"
    return session


def post(session, url, data):
    body = {k: (v.encode("euc-kr") if isinstance(v, str) else v) for k, v in data.items()}
    for attempt in range(3):
        try:
            response = session.post(url, data=body, timeout=60)
            response.raise_for_status()
            pause()
            return local.decode_response(response.content)
        except Exception as exc:
            if attempt == 2:
                raise
            print("    [s2b] 요청 오류: " + str(exc)[:120] + " -> 10초 후 재시도")
            time.sleep(10)


def text_of(node):
    return re.sub(r"\s+", " ", html.unescape(node.get_text(" ", strip=True))).strip() if node else ""


def ymd(value):
    return value.replace("-", "")


def iso(value):
    digits = re.sub(r"[^0-9]", "", value or "")
    return digits[:4] + "-" + digits[4:6] + "-" + digits[6:8] if len(digits) >= 8 else ""


def korean_datetime(value):
    # "2026년 10월 16일 12시 00분" -> "2026-10-16 12:00"
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*(?:(\d{1,2})\D+(\d{1,2}))?", value or "")
    if not m:
        return ""
    out = m.group(1) + "-" + m.group(2).zfill(2) + "-" + m.group(3).zfill(2)
    if m.group(4):
        out += " " + m.group(4).zfill(2) + ":" + m.group(5).zfill(2)
    return out


def amount_text(value):
    digits = re.sub(r"[^0-9]", "", value or "")
    return "{:,}".format(int(digits)) if digits else ""


def detail_fields(page):
    # 상세 화면의 "항목명 | 값" 표를 사전으로. 항목명 앞의 *는 뺀다.
    soup = BeautifulSoup(page, "html.parser")
    fields = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        for i in range(0, len(cells) - 1, 2):
            key = text_of(cells[i]).lstrip("*").strip()
            if key and len(key) <= 20 and key not in fields:
                fields[key] = text_of(cells[i + 1])
    return fields


def chunks(date_from, date_to):
    start = datetime.strptime(date_from, "%Y%m%d")
    end = datetime.strptime(date_to, "%Y%m%d")
    while start <= end:
        stop = min(end, start + timedelta(days=CHUNK_DAYS - 1))
        yield start.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        start = stop + timedelta(days=1)


def is_test_post(name):
    # 학교장터 운영사·학교가 올린 시험 글 (예: "[EDU] 테스트", "[EDU] 영신중테스트", "발주계획 작성 S2B테스트")
    # "체력 테스트 기기 구입" 같은 실제 사업은 남기려고, 테스트만 있거나 [EDU]·S2B·발주계획과 함께 쓴 것만 거른다.
    text = (name or "").strip()
    if re.fullmatch(r"(\[[^\]]*\]\s*)?(테스트|test)\s*\d*", text, re.I):
        return True
    return "테스트" in text and bool(re.search(r"\[EDU\]|S2B|발주계획", text, re.I))


def enrich(record):
    record["keywords"] = local.tag_keywords(record.get("name", ""))
    record["excluded"] = local.is_excluded_contract_name(record.get("name", ""))
    institution = record.get("institution", "")
    if not record.get("region"):
        region = local.region_from_institution_name(institution) or {}
        record["region"] = region.get("region", "") or local.short_region(institution)
    record["school_level"] = local.school_level(institution)
    record["school_category"] = local.school_category(institution)
    return record


# ---------------------------------------------------------------------------
# 발주계획
# ---------------------------------------------------------------------------

def plan_list_page(session, date_from, date_to, page_no):
    page = post(session, PLAN_URL, {
        "forwardName": "list", "pageNo": str(page_no), "req_seq": "", "start_date": date_from, "end_date": date_to,
        "s_estimate_type": "", "city_name": "전국", "s_company_name": "", "s_estimate_name": "",
    })
    soup = BeautifulSoup(page, "html.parser")
    rows = []
    for link in soup.select('a[href*="f_view("]'):
        seq = re.search(r"f_view\((\d+)\)", link.get("href", ""))
        tr = link.find_parent("tr")
        if not seq or not tr:
            continue
        cells = [text_of(td) for td in tr.find_all("td")]
        nxt = tr.find_next_sibling("tr")
        more = [text_of(td) for td in nxt.find_all("td")] if nxt and not nxt.select('a[href*="f_view("]') else []
        rows.append({"seq": seq.group(1), "cells": cells, "more": more, "name": text_of(link)})
    pages = [int(n) for n in re.findall(r"goList\((\d+)\)", page)]
    return rows, max(pages + [page_no])


def plan_detail(session, seq):
    return detail_fields(post(session, PLAN_URL, {
        "forwardName": "view", "pageNo": "1", "req_seq": seq, "start_date": "", "end_date": "",
        "s_estimate_type": "", "city_name": "전국", "s_company_name": "", "s_estimate_name": "",
    }))


def normalize_plan(row, detail):
    cells = [c for c in row["cells"] + row["more"] if c]
    plan_no = detail.get("발주계획번호") or (cells[0] if cells else row["seq"])
    location = detail.get("소재지", "")
    return enrich({
        "id": "s2b_plan:" + plan_no,
        "source": "plan",
        "seq": row["seq"],
        "plan_no": plan_no,
        "name": detail.get("사업명") or row["name"],
        "institution": detail.get("기관명") or (cells[1] if len(cells) > 1 else ""),
        "location": location,
        "region": local.short_region(location.split(" ")[0]) if location else "",
        "category": detail.get("업무구분", ""),
        "method": detail.get("계약방법", ""),
        "method_reason": detail.get("수의계약사유", ""),
        "order_month": korean_datetime(detail.get("발주시기", "") + " 1일")[:7],
        "date": korean_datetime(detail.get("게시일", "")),
        "amount": amount_text(detail.get("예산액(원)", "")),
        "usage": detail.get("용도", ""),
        "spec": detail.get("주요규격", ""),
        "quantity": " ".join(x for x in (detail.get("예상수량", ""), detail.get("수량단위", "")) if x),
        "note": detail.get("비고", ""),
        "link": PLAN_URL,
    })


def collect_plans(session, date_from, date_to):
    records = []
    for chunk_from, chunk_to in chunks(date_from, date_to):
        page_no, last = 1, 1
        while page_no <= last:
            rows, last = plan_list_page(session, chunk_from, chunk_to, page_no)
            print("[발주계획] " + local.display_date(chunk_from) + "~" + local.display_date(chunk_to) + " " + str(page_no) + "/" + str(last) + "쪽 " + str(len(rows)) + "건")
            for row in rows:
                if is_test_post(row["name"]):
                    continue
                records.append(normalize_plan(row, plan_detail(session, row["seq"])))
            page_no += 1
    return records


# ---------------------------------------------------------------------------
# 입찰공고
# ---------------------------------------------------------------------------

def bid_form(date_from, date_to, city, page_no, forward="list", tender_num=""):
    return {
        "forwardName": forward, "pageNo": str(page_no), "tender_num": tender_num, "tender_step_code": "A" if tender_num else "",
        "page_flag": "2" if tender_num else "", "tender_sep1": "1", "tender_name": "", "company_name_s": "",
        "tender_sep2": "1", "tender_date_start": date_from, "tender_date_end": date_to, "tender_item": "", "city": city,
    }


def bid_list_page(session, date_from, date_to, city, page_no):
    page = post(session, BID_URL, bid_form(date_from, date_to, city, page_no))
    soup = BeautifulSoup(page, "html.parser")
    rows = []
    for link in soup.select('a[href*="f_detail("]'):
        num = re.search(r"f_detail\('([^']+)'\)", link.get("href", ""))
        tr = link.find_parent("tr")
        if not num or not tr:
            continue
        cells = [text_of(td) for td in tr.find_all("td")]
        nxt = tr.find_next_sibling("tr")
        more = [text_of(td) for td in nxt.find_all("td")] if nxt else []
        rows.append({"num": num.group(1), "cells": cells, "more": more, "name": text_of(link)})
    pages = [int(n) for n in re.findall(r"goList\((\d+)\)", page)]
    return rows, max(pages + [page_no])


def bid_detail(session, num):
    return detail_fields(post(session, BID_URL, bid_form("", "", "", 1, "view", num)))


def normalize_bid(row, city, detail):
    more = [c for c in row["more"] if c]
    name = detail.get("입찰건명") or re.sub(r"^\[취소\]\s*", "", row["name"])
    cancelled = row["name"].startswith("[취소]") or row["num"].split("-")[-1].startswith("D")
    return enrich({
        "id": "s2b_bid:" + row["num"],
        "source": "bid",
        "notice_no": row["num"],
        "name": name,
        "notice_kind": "취소공고" if cancelled else detail.get("공고분류", ""),
        "institution": detail.get("수요기관") or (more[1] if len(more) > 1 else ""),
        "region": city,
        "category": row["cells"][2] if len(row["cells"]) > 2 else "",
        "classification": detail.get("공고구분", ""),
        "date": korean_datetime(detail.get("공고일자", ""))[:10] or (more[2][:10] if len(more) > 2 else ""),
        "registered_at": korean_datetime(detail.get("공고일자", "")),
        "open_from": korean_datetime(detail.get("입찰서접수개시일시", "")),
        "close_at": korean_datetime(detail.get("입찰서접수마감일시", "")) or (more[3] if len(more) > 3 else ""),
        "open_at": korean_datetime(detail.get("개찰일시", "")),
        "amount": amount_text(detail.get("기초금액(VAT포함, 원)", "") or detail.get("추정가격(원)", "")),
        "estimated_price": amount_text(detail.get("추정가격(원)", "")),
        "method": detail.get("계약방법", ""),
        "award_method": detail.get("낙찰자결정방법", ""),
        "contract_period": detail.get("계약기간", ""),
        "delivery_place": detail.get("납품장소", ""),
        "plan_no": detail.get("발주계획번호", ""),
        "ref_no": detail.get("참조번호", ""),
        "link": BID_URL,
    })


def collect_bids(session, date_from, date_to):
    records = []
    for chunk_from, chunk_to in chunks(date_from, date_to):
        for city in BID_CITIES:
            page_no, last = 1, 1
            while page_no <= last:
                rows, last = bid_list_page(session, chunk_from, chunk_to, city, page_no)
                if rows:
                    print("[입찰공고] " + local.display_date(chunk_from) + "~" + local.display_date(chunk_to) + " " + city + " " + str(page_no) + "/" + str(last) + "쪽 " + str(len(rows)) + "건")
                for row in rows:
                    if is_test_post(re.sub(r"^\[취소\]\s*", "", row["name"])):
                        continue
                    records.append(normalize_bid(row, city, bid_detail(session, row["num"])))
                page_no += 1
    return records


# ---------------------------------------------------------------------------
# 누적 파일
# ---------------------------------------------------------------------------

def load_notices():
    if not os.path.exists(NOTICES_FILE):
        return {"exported_at": "", "meta": {}, "records": []}
    with open(NOTICES_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("records", [])
    return data


def merge_notices(records, date_from, date_to):
    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = load_notices()
    by_id = {row["id"]: row for row in data["records"] if row.get("id")}
    added = updated = 0
    for incoming in records:
        incoming["last_imported_at"] = imported_at
        if incoming["id"] in by_id:
            incoming["first_imported_at"] = by_id[incoming["id"]].get("first_imported_at") or imported_at
            updated += 1
        else:
            incoming["first_imported_at"] = imported_at
            added += 1
        by_id[incoming["id"]] = incoming
    rows = sorted(by_id.values(), key=lambda r: (r.get("date", ""), r.get("registered_at", "")), reverse=True)
    payload = {
        "exported_at": imported_at,
        "meta": {"lastImportedAt": imported_at, "lastSearchPeriod": date_from + "~" + date_to, "total": len(rows),
                 "bySource": {s: sum(1 for r in rows if r.get("source") == s) for s in SOURCES}},
        "records": rows,
    }
    with open(NOTICES_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=1)
    print("[s2b notices] saved: " + NOTICES_FILE + " | added " + str(added) + ", updated " + str(updated) + ", total " + str(len(rows)))
    return payload



def run(date_from, date_to, sources, github_upload=True):
    print("[s2b notices] " + local.display_date(date_from) + " ~ " + local.display_date(date_to) + " | " + ",".join(sources))
    session = new_session()
    session.get(BASE_URL + "/S2BNCustomer/S2B/", timeout=60)
    records = []
    if "plan" in sources:
        records += collect_plans(session, date_from, date_to)
    if "bid" in sources:
        records += collect_bids(session, date_from, date_to)
    print("=" * 55)
    print("[s2b notices] 발주계획 " + str(sum(1 for r in records if r["source"] == "plan")) + "건 · 입찰공고 " + str(sum(1 for r in records if r["source"] == "bid")) + "건")
    merge_notices(records, date_from, date_to)
    try:
        local.publish_to_github(date_from, date_to, github_upload)
    except Exception as exc:
        print("[github] upload failed, but local files were saved: " + str(exc))
    return records


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="학교장터(S2B) 입찰공고·발주계획 수집 (공개 화면)")
    parser.add_argument("--from", dest="date_from", help="시작일 YYYYMMDD (기본: 90일 전)")
    parser.add_argument("--to", dest="date_to", help="종료일 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--sources", default=",".join(SOURCES), help="plan,bid 중 선택 (기본 둘 다)")
    parser.add_argument("--no-github-upload", action="store_false", dest="github_upload", default=local.AUTO_GITHUB_UPLOAD)
    return parser.parse_args(argv)


def main(argv=None):
    print("=" * 55)
    print("  S2B (학교장터) bid notices / order plans collector")
    print("=" * 55)
    args = parse_args(argv)
    today = datetime.now().strftime("%Y%m%d")
    date_to = local.normalize_date(args.date_to) if args.date_to else today
    date_from = local.normalize_date(args.date_from) if args.date_from else (datetime.strptime(date_to, "%Y%m%d") - timedelta(days=89)).strftime("%Y%m%d")
    if date_from > date_to:
        print("[error] 시작일이 종료일보다 늦습니다.")
        return 2
    sources = [s.strip() for s in args.sources.split(",") if s.strip() in SOURCES]
    run(date_from, date_to, sources, args.github_upload)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

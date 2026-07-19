# coding: utf-8
import argparse
import hashlib
import html
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup


KEYWORDS = [
    "코스웨어", "국어", "수학", "영어", "기초학력", "교과보충", "두드림",
    "코딩", "미래엔", "초코팝", "달달", "비상교육 옥수수", "홈런", "스쿨런",
    "기출탭탭", "스마트올", "리딩앤", "자작자작", "클래스팅", "플랭", "수학대장",
    "지니아튜터", "천재교육", "천재교과서", "1HOUR", "토도수학", "토도한글", "토도영어",
]
EXCLUDE_WORDS = [
    "거치대", "건설", "경연대회", "고등학교", "공사", "공연", "공책",
    "과학고", "교구", "교재", "급식", "기기", "기자재", "기숙사",
    "기업", "논문", "노트", "노후", "대학", "대학교", "대회",
    "도서", "도서관", "도시", "로봇", "물품", "문화상품권", "보건실",
    "보드게임", "비품", "사회대", "사회복무요원", "샤워", "성인", "센서",
    "수리", "수학여행", "실험실", "안전", "어린이집", "연설대", "연필",
    "예술", "옥수수", "외국어", "외국어학교", "재료", "전자칠판", "체육",
    "체험", "축제", "취업", "캠프", "콘센트릴", "키트", "특수학교",
    "특수학급", "페스타", "페스티벌", "폐기물", "폐수통", "폐시약", "학술",
    "해외학교", "행사", "현장체험", "하루 한장", "플라스크", "간식", "워크북",
    "준비물", "용역", "스탠드", "강사", "차량", "한국어", "다국어",
    "도화지", "박철완", "개정판", "제작", "다락원", "빠작", "뿌리",
    "박연수", "오리온", "마은정", "드림디포", "황정하", "기탄", "이유민",
    "손진현", "국어사전",
]

BASE_URL = "https://www.s2b.kr"
LIST_URL = BASE_URL + "/S2BNCustomer/tcmo001.do"

PAGE_DELAY_RANGE = (18.0, 35.0)
KEYWORD_DELAY_RANGE = (20.0, 45.0)
CAPTCHA_RETRY_COUNT = 3
CAPTCHA_DELAY_RANGE = (600.0, 1800.0)
MAX_PAGES_PER_KEYWORD = None
MAX_PAGES_BY_KEYWORD = {}
HEAVY_KEYWORD_COOLDOWN = {}
REGION_ALIASES = [
    ("서울특별시", "서울"), ("부산광역시", "부산"), ("대구광역시", "대구"), ("인천광역시", "인천"),
    ("광주광역시", "광주"), ("대전광역시", "대전"), ("울산광역시", "울산"), ("세종특별자치시", "세종"),
    ("경기도", "경기"), ("강원특별자치도", "강원"), ("강원도", "강원"),
    ("충청북도", "충북"), ("충청남도", "충남"), ("전북특별자치도", "전북"), ("전라북도", "전북"),
    ("전라남도", "전남"), ("경상북도", "경북"), ("경상남도", "경남"), ("제주특별자치도", "제주"),
    ("서울", "서울"), ("부산", "부산"), ("대구", "대구"), ("인천", "인천"), ("광주", "광주"),
    ("대전", "대전"), ("울산", "울산"), ("세종", "세종"), ("경기", "경기"), ("강원", "강원"),
    ("충북", "충북"), ("충남", "충남"), ("전북", "전북"), ("전남", "전남"), ("경북", "경북"),
    ("경남", "경남"), ("제주", "제주"),
]
NEIS_SCHOOL_INFO_URL = "https://open.neis.go.kr/hub/schoolInfo"
_school_region_cache = {}

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.basename(APP_DIR).lower() == "dist":
        APP_DIR = os.path.dirname(APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CUMULATIVE_JSON_FILE = os.path.join(APP_DIR, "s2b_cumulative.json")
CUMULATIVE_HTML_FILE = os.path.join(APP_DIR, "s2b_cumulative.html")
INDEX_HTML_FILE = os.path.join(APP_DIR, "index.html")
AUTO_GITHUB_UPLOAD = os.environ.get("S2B_AUTO_GITHUB", "1").lower() not in ("0", "false", "no", "off")
GITHUB_UPLOAD_FILES = ("s2b_cumulative.json", "s2b_cumulative.html", "index.html")
HOLIDAY_API_KEY = "0ff126d5fe6324dc2b8b3b8ee7dc0ccdd9e7d2203962e065703c3c7b78ff4809"


def normalize_date(value):
    text = (value or "").strip().replace("-", "").replace(".", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", text):
        raise ValueError("날짜는 YYYYMMDD 또는 YYYY-MM-DD 형식으로 입력하세요: " + value)
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        raise ValueError("존재하지 않는 날짜입니다: " + value) from None
    return text


def display_date(value):
    if not value:
        return ""
    digits = normalize_date(value)
    return digits[:4] + "." + digits[4:6] + "." + digits[6:]


def get_date_range_from_user(args):
    date_from = args.date_from
    date_to = args.date_to
    if not date_from:
        date_from = input("검색 시작일(YYYYMMDD 또는 YYYY-MM-DD): ").strip()
    if not date_to:
        date_to = input("검색 종료일(YYYYMMDD 또는 YYYY-MM-DD, 엔터=시작일과 같음): ").strip() or date_from

    date_from = normalize_date(date_from)
    date_to = normalize_date(date_to)
    if date_from > date_to:
        raise ValueError("검색 시작일이 종료일보다 늦습니다.")
    return date_from, date_to


def make_detail_url(href_raw):
    match = re.search(r"f_detail\('([^']+)',\s*'([^']+)'\)", href_raw)
    if match:
        forward = "view03_2" if match.group(2) == "3" else "view03_1"
        return LIST_URL + "?forwardName=" + forward + "&tender_num=" + match.group(1) + "&excelSection=N"
    return ""


def new_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": LIST_URL + "?forwardName=list03",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": BASE_URL,
    })
    return session


def decode_response(html_bytes):
    try:
        return html_bytes.decode("euc-kr", errors="replace")
    except Exception:
        return html_bytes.decode("utf-8", errors="replace")


def is_captcha(html_bytes):
    text = decode_response(html_bytes)
    return "Anti Web Crawling" in text or "captchaImg" in text


def sleep_random(delay_range, label="wait"):
    seconds = random.uniform(*delay_range)
    print("    " + label + ": " + str(round(seconds, 1)) + "s")
    time.sleep(seconds)


def validate_delay_range(min_seconds, max_seconds, option_name):
    if min_seconds < 0 or max_seconds < 0:
        raise ValueError(option_name + " must be 0 or greater.")
    if min_seconds > max_seconds:
        raise ValueError(option_name + " min must be less than or equal to max.")
    return (float(min_seconds), float(max_seconds))


def is_excluded_contract_name(contract_name):
    text = contract_name or ""
    for exclude in EXCLUDE_WORDS:
        if exclude not in text:
            continue
        if exclude == "옥수수" and re.search(r"비상교육\s*옥수수", text):
            continue
        return True
    return False


def parse_page(html_bytes):
    soup = BeautifulSoup(decode_response(html_bytes), "lxml")
    tables = [table for table in soup.find_all("table") if "td_dark_line" in (table.get("class") or [])]
    if not tables:
        return [], False

    data_table = max(tables, key=lambda table: len(table.find_all("tr")))
    rows = data_table.find_all("tr")
    records = []
    i = 0
    while i < len(rows):
        cols1 = rows[i].find_all("td")
        if not cols1 or cols1[0].get_text(strip=True) in ("", "NO"):
            i += 1
            continue
        if len(cols1) < 5:
            i += 1
            continue

        contract_name = cols1[3].get_text(" ", strip=True)
        contract_no = cols1[2].get_text(strip=True)
        amount = cols1[4].get_text(strip=True)
        counterpart = cols1[5].get_text(strip=True) if len(cols1) > 5 else ""

        link = ""
        a_tag = cols1[3].find("a")
        if a_tag:
            link = make_detail_url(a_tag.get("href", "") or "")

        institution = ""
        contract_date = ""
        if i + 1 < len(rows):
            cols2 = rows[i + 1].find_all("td")
            if cols2 and len(cols2) >= 4:
                institution = cols2[1].get_text(strip=True)
                contract_date = cols2[3].get_text(strip=True)
            i += 2
        else:
            i += 1

        records.append({
            "계약명": contract_name,
            "계약번호": contract_no,
            "계약기관": institution,
            "계약대상자": counterpart,
            "금액": amount,
            "계약체결일": contract_date,
            "링크": link,
        })
    return records, len(records) > 0


def fetch_by_keyword(session, keyword, date_from, date_to):
    results = []
    keyword_euckr = quote(keyword.encode("euc-kr"))
    area_euckr = quote("전국".encode("euc-kr"))
    max_pages = MAX_PAGES_BY_KEYWORD.get(keyword, MAX_PAGES_PER_KEYWORD)

    page = 1
    while max_pages is None or page <= max_pages:
        if page > 1:
            sleep_random(PAGE_DELAY_RANGE, "request delay")

        body = (
            "forwardName=list03&pageNo=" + str(page) +
            "&tender_num=&tender_step_code=&page_flag="
            "&excelSection=N&process_yn=Y&search_yn=Y&tender_sep1=1"
            "&tender_name=" + keyword_euckr + "&company_name_s=&tender_sep2=2"
            "&tender_date_start=" + date_from + "&tender_date_end=" + date_to +
            "&tender_item=&estimate_kind=&areaKind=" + area_euckr
        )
        try:
            response = session.post(LIST_URL, data=body.encode("ascii"), timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            print("    error: " + str(exc))
            break

        if is_captcha(response.content):
            captcha_ok = False
            for retry in range(CAPTCHA_RETRY_COUNT):
                wait = random.uniform(*CAPTCHA_DELAY_RANGE)
                print("    [!] CAPTCHA 감지. " + str(round(wait, 1)) + "초 대기 후 재시도 "
                      "(" + str(retry + 1) + "/" + str(CAPTCHA_RETRY_COUNT) + ")")
                time.sleep(wait)
                session = new_session()
                try:
                    response = session.post(LIST_URL, data=body.encode("ascii"), timeout=30)
                    response.raise_for_status()
                    if not is_captcha(response.content):
                        captcha_ok = True
                        break
                except Exception:
                    continue
            if not captcha_ok:
                print("    [!] CAPTCHA 해결 실패. 이 키워드는 건너뜁니다.")
                break

        records, has_data = parse_page(response.content)
        if not has_data:
            break

        filtered = [
            record for record in records
            if keyword in record["계약명"]
            and not is_excluded_contract_name(record["계약명"])
        ]
        results.extend(filtered)
        print("    page " + str(page) + ": " + str(len(records)) + " recv, " + str(len(filtered)) + " matched")

        if len(records) == 0:
            break
        page += 1

    return results



def parse_keyword_selection(raw_keywords):
    requested = [keyword.strip() for keyword in (raw_keywords or '').split(',') if keyword.strip()]
    if not requested:
        return KEYWORDS[:]

    selected = []
    unknown = []
    for token in requested:
        lowered = token.lower()
        if lowered in ('all', '*') or token == '\uc804\uccb4':
            return KEYWORDS[:]

        match = re.fullmatch(r'(\d+)(?:\s*-\s*(\d+))?', token)
        if match:
            first = int(match.group(1))
            last = int(match.group(2) or match.group(1))
            if first > last:
                first, last = last, first
            if first < 1 or last > len(KEYWORDS):
                unknown.append(token)
                continue
            selected.extend(KEYWORDS[index - 1] for index in range(first, last + 1))
            continue

        if token not in KEYWORDS:
            unknown.append(token)
            continue
        selected.append(token)

    if unknown:
        raise ValueError('Unknown keyword or number: ' + ', '.join(unknown))

    unique_selected = []
    seen = set()
    for keyword in selected:
        if keyword in seen:
            continue
        seen.add(keyword)
        unique_selected.append(keyword)
    return unique_selected


def select_keywords(args):
    selected = KEYWORDS[:]
    if args.keywords:
        selected = parse_keyword_selection(args.keywords)

    if args.batch_size:
        if args.batch_size < 1:
            raise ValueError('--batch-size must be 1 or greater.')
        if args.batch_index < 1:
            raise ValueError('--batch-index must be 1 or greater.')
        start = (args.batch_index - 1) * args.batch_size
        end = start + args.batch_size
        if start >= len(selected):
            raise ValueError('Selected keyword batch is empty.')
        selected = selected[start:end]

    return selected


def fetch_all(date_from, date_to, keywords):
    print("[period] " + display_date(date_from) + " ~ " + display_date(date_to))
    print("[keywords] " + ", ".join(keywords))
    print("[exclude]  " + ", ".join(EXCLUDE_WORDS) + "\n")

    session = new_session()
    seen_nos = set()
    all_results = []
    keyword_map = {}

    for keyword in keywords:
        cooldown = HEAVY_KEYWORD_COOLDOWN.get(keyword, 0)
        if cooldown:
            print("[" + keyword + "] cooldown " + str(cooldown) + "s before searching...")
            time.sleep(cooldown)

        print("[" + keyword + "] searching...")
        items = fetch_by_keyword(session, keyword, date_from, date_to)
        print("  -> " + str(len(items)) + " found\n")

        for item in items:
            contract_no = item["계약번호"]
            if contract_no not in seen_nos:
                seen_nos.add(contract_no)
                all_results.append(item)
                keyword_map[contract_no] = [keyword]
            elif contract_no in keyword_map and keyword not in keyword_map[contract_no]:
                keyword_map[contract_no].append(keyword)

        sleep_random(KEYWORD_DELAY_RANGE, "keyword delay")

    for item in all_results:
        item["매칭키워드"] = keyword_map.get(item["계약번호"], [])

    print("=" * 55)
    print("이번 검색 결과: " + str(len(all_results)) + "건 (중복 제거)")
    return all_results


def stable_id(record):
    tender_no = record.get("계약번호") or record.get("tender_no") or ""
    if tender_no:
        return tender_no
    fallback = "|".join([
        record.get("계약명", ""),
        record.get("계약기관", ""),
        record.get("계약대상자", ""),
        record.get("금액", ""),
        record.get("계약체결일", ""),
    ])
    return "local-" + hashlib.md5(fallback.encode("utf-8")).hexdigest()[:12]


def school_level(institution):
    text = institution or ""
    if "초등학교" in text or re.search(r"(^|[^가-힣])초($|[^가-힣])", text):
        return "초"
    if "중학교" in text or re.search(r"(^|[^가-힣])중($|[^가-힣])", text):
        return "중"
    if "고등학교" in text or re.search(r"(^|[^가-힣])고($|[^가-힣])", text):
        return "고"
    return "기타"


def normalize_school_name(value):
    return re.sub(r"\s+", "", value or "")


def short_region(value):
    text = value or ""
    for alias, region in REGION_ALIASES:
        if alias in text:
            return region
    return ""


def region_from_institution_name(institution):
    text = institution or ""
    for alias, region in REGION_ALIASES:
        if alias in text:
            return {
                "region": region,
                "region_status": "direct",
                "region_source": "institution_name",
                "region_candidates": [],
            }
    return None


def school_candidate(row):
    address = row.get("ORG_RDNMA") or ""
    region = short_region(row.get("LCTN_SC_NM") or address)
    district = ""
    parts = address.split()
    if len(parts) >= 2:
        district = parts[1]
    return {
        "school_name": row.get("SCHUL_NM", ""),
        "region": region,
        "district": district,
        "education_office": row.get("ATPT_OFCDC_SC_NM", ""),
        "support_office": row.get("JU_ORG_NM", ""),
        "address": address,
        "school_code": row.get("SD_SCHUL_CODE", ""),
    }


def fetch_school_candidates(institution):
    name = normalize_school_name(institution)
    if not name:
        return []
    if name in _school_region_cache:
        return _school_region_cache[name]
    params = {
        "Type": "json",
        "pIndex": "1",
        "pSize": "100",
        "SCHUL_NM": institution,
    }
    try:
        url = NEIS_SCHOOL_INFO_URL + "?" + urlencode(params)
        response = requests.get(url, timeout=4)
        response.raise_for_status()
        data = response.json()
        rows = []
        for section in data.get("schoolInfo", []):
            if isinstance(section, dict) and isinstance(section.get("row"), list):
                rows.extend(section["row"])
        exact = [row for row in rows if normalize_school_name(row.get("SCHUL_NM", "")) == name]
        candidates = [school_candidate(row) for row in (exact or rows)]
    except Exception as exc:
        print("[region] school lookup failed for " + institution + ": " + str(exc))
        candidates = []
    _school_region_cache[name] = candidates
    return candidates


def resolve_region(institution):
    direct = region_from_institution_name(institution)
    if direct:
        return direct
    if "학교" not in (institution or ""):
        return {"region": "", "region_status": "unknown", "region_source": "", "region_candidates": []}
    candidates = fetch_school_candidates(institution)
    if len(candidates) == 1:
        candidate = candidates[0]
        return {
            "region": candidate.get("region", ""),
            "region_status": "matched",
            "region_source": "neis_school_info",
            "region_candidates": candidates,
        }
    if len(candidates) > 1:
        return {
            "region": "",
            "region_status": "ambiguous",
            "region_source": "neis_school_info",
            "region_candidates": candidates,
        }
    return {"region": "", "region_status": "unknown", "region_source": "neis_school_info", "region_candidates": []}


def load_cumulative_json():
    if not os.path.exists(CUMULATIVE_JSON_FILE):
        return {"exported_at": "", "meta": {}, "records": []}
    try:
        with open(CUMULATIVE_JSON_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        data.setdefault("records", [])
        return data
    except Exception as exc:
        backup = CUMULATIVE_JSON_FILE + ".broken_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        os.replace(CUMULATIVE_JSON_FILE, backup)
        print("[json] 기존 누적 파일을 읽지 못해 백업했습니다: " + backup)
        print("[json] error: " + str(exc))
        return {"exported_at": "", "meta": {}, "records": []}


def to_cumulative_record(result, date_from, date_to, imported_at):
    institution = result.get("계약기관", "")
    record = {
        "id": stable_id(result),
        "tender_no": result.get("계약번호", ""),
        "contract_name": result.get("계약명", ""),
        "institution": institution,
        "counterpart": result.get("계약대상자", ""),
        "amount": result.get("금액", ""),
        "contract_date": result.get("계약체결일", ""),
        "keywords": result.get("매칭키워드", []),
        "link": result.get("링크", ""),
        "search_period_from": display_date(date_from),
        "search_period_to": display_date(date_to),
        "last_imported_at": imported_at,
        "school_level": school_level(institution),
    }
    record.update(resolve_region(institution))
    return record


def update_cumulative_json(results, date_from, date_to):
    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = load_cumulative_json()
    by_id = {}

    for old in data.get("records", []):
        record_id = old.get("id") or old.get("tender_no") or stable_id({
            "계약명": old.get("contract_name", ""),
            "계약기관": old.get("institution", ""),
            "계약대상자": old.get("counterpart", ""),
            "금액": old.get("amount", ""),
            "계약체결일": old.get("contract_date", ""),
        })
        old["id"] = record_id
        old.setdefault("keywords", [])
        old.setdefault("first_imported_at", old.get("last_imported_at", imported_at))
        old.setdefault("import_count", 1)
        old.setdefault("school_level", school_level(old.get("institution", "")))
        old.setdefault("region", "")
        old.setdefault("region_status", "")
        old.setdefault("region_source", "")
        old.setdefault("region_candidates", [])
        by_id[record_id] = old

    added = 0
    updated = 0
    for result in results:
        incoming = to_cumulative_record(result, date_from, date_to, imported_at)
        existing = by_id.get(incoming["id"])
        if not existing:
            incoming["first_imported_at"] = imported_at
            incoming["import_count"] = 1
            by_id[incoming["id"]] = incoming
            added += 1
            continue

        keywords = sorted(set(existing.get("keywords", [])) | set(incoming.get("keywords", [])))
        first_imported_at = existing.get("first_imported_at") or imported_at
        import_count = int(existing.get("import_count") or 0) + 1
        existing.update(incoming)
        existing["keywords"] = keywords
        existing["first_imported_at"] = first_imported_at
        existing["import_count"] = import_count
        updated += 1

    records = sorted(
        by_id.values(),
        key=lambda row: (row.get("contract_date", ""), row.get("last_imported_at", ""), row.get("contract_name", "")),
        reverse=True,
    )
    payload = {
        "exported_at": imported_at,
        "meta": {
            "lastImportedAt": imported_at,
            "lastSearchPeriod": date_from + "~" + date_to,
            "total": len(records),
        },
        "records": records,
    }
    with open(CUMULATIVE_JSON_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    print("[json] saved: " + CUMULATIVE_JSON_FILE)
    print("[json] added " + str(added) + ", updated " + str(updated) + ", total " + str(len(records)))
    return payload



def get_holidays(year, months=None):
    holidays = set()
    try:
        url = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
        for month in (months or range(1, 13)):
            params = {
                "serviceKey": HOLIDAY_API_KEY,
                "solYear": str(year),
                "solMonth": str(month).zfill(2),
                "numOfRows": "20",
                "_type": "json",
            }
            response = requests.get(url, params=params, timeout=3)
            data = response.json()
            items = data.get("response", {}).get("body", {}).get("items", {})
            if not items:
                continue
            item_list = items.get("item", [])
            if isinstance(item_list, dict):
                item_list = [item_list]
            for item in item_list:
                locdate = str(item.get("locdate", ""))
                if locdate:
                    holidays.add(locdate)
    except Exception as exc:
        print("[holiday] API error: " + str(exc))
    return holidays


def previous_workday_range(today=None):
    today = today or datetime.now()
    date_to = today - timedelta(days=1)
    month_map = {today.year: {today.month, date_to.month}}
    if date_to.year != today.year:
        month_map.setdefault(date_to.year, set()).add(date_to.month)
    holidays = set()
    for year, months in month_map.items():
        holidays |= get_holidays(year, sorted(months))
    if today.month == 1:
        holidays |= get_holidays(today.year - 1, [12])
    if today.month == 12:
        holidays |= get_holidays(today.year + 1, [1])

    cursor = date_to
    date_from = date_to
    for _ in range(14):
        locdate = cursor.strftime("%Y%m%d")
        if cursor.weekday() < 5 and locdate not in holidays:
            date_from = cursor
            break
        cursor -= timedelta(days=1)
    return date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d"), sorted(holidays)
def esc(value):
    return html.escape(str(value or ""), quote=True)


def build_cumulative_html(data):
    records = data.get("records", [])
    exported_at = data.get("exported_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    ref_url = LIST_URL + "?forwardName=list03"
    regions = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
    default_date_from, default_date_to, holidays = previous_workday_range()
    default_range_label = default_date_from if default_date_from == default_date_to else default_date_from + " ~ " + default_date_to


    all_keywords = []
    for keyword in KEYWORDS:
        if any(keyword in row.get("keywords", []) for row in records):
            all_keywords.append(keyword)

    rows_html = ""
    default_visible_count = 0
    for index, row in enumerate(records, 1):
        keywords = row.get("keywords", [])
        keywords_joined = ",".join(keywords)
        contract_date = row.get("contract_date", "")
        in_default_range = bool(contract_date) and default_date_from <= contract_date <= default_date_to
        if in_default_range:
            default_visible_count += 1
        display_number = str(default_visible_count) if in_default_range else ""
        row_display_attr = "" if in_default_range else ' style="display:none"'
        tag_html = "".join('<span class="tag">' + esc(keyword) + "</span>" for keyword in keywords)
        contract_name = esc(row.get("contract_name", ""))
        if row.get("link"):
            name_html = (
                '<a href="' + esc(row.get("link")) + '" target="_blank" rel="noopener" '
                'class="contract-link">' + contract_name + " &#8599;</a>"
            )
        else:
            name_html = contract_name

        record_id = esc(row.get("id") or row.get("tender_no") or str(index))
        region = row.get("region", "")
        candidates = row.get("region_candidates", []) or []
        if region:
            region_html = '<span class="region-text fixed-region" data-region-id="' + record_id + '">' + esc(region) + '</span>'
        else:
            options_html = '<option value="">지역 선택</option>'
            seen_options = set()
            for candidate in candidates:
                cand_region = candidate.get("region", "")
                if not cand_region:
                    continue
                label_parts = [cand_region]
                school_name = candidate.get("school_name", "")
                district = candidate.get("district", "")
                address = candidate.get("address", "")
                if school_name:
                    label_parts.append(school_name)
                if district:
                    label_parts.append(district)
                elif address:
                    label_parts.append(address)
                label = " · ".join(label_parts)
                key = cand_region + "|" + label
                if key in seen_options:
                    continue
                seen_options.add(key)
                options_html += '<option value="' + esc(cand_region) + '">' + esc(label) + '</option>'
            if candidates:
                options_html += '<option value="" disabled>──────────</option>'
            for region_name in regions:
                if region_name not in [candidate.get("region", "") for candidate in candidates]:
                    options_html += '<option value="' + esc(region_name) + '">' + esc(region_name) + '</option>'
            region_html = (
                '<div class="region-editor" data-region-id="' + record_id + '">'
                '<select class="region-select" aria-label="지역 선택">' + options_html + '</select>'
                '<button type="button" class="region-save" onclick="saveRegion(this)">저장</button>'
                '</div>'
            )

        rows_html += (
            '<tr data-record-id="' + record_id + '" data-keywords="' + esc(keywords_joined) + '" data-level="' + esc(row.get("school_level", "")) + '" data-contract-date="' + esc(contract_date) + '"' + row_display_attr + '>'
            '<td class="tc select-cell"><input type="checkbox" class="row-check" aria-label="삭제할 공고 선택"></td>'
            '<td class="tc row-no">' + display_number + '</td>'
            '<td>' + name_html + '</td>'
            '<td><div class="tags">' + tag_html + '</div></td>'
            '<td class="tc region-cell">' + region_html + '</td>'
            '<td>' + esc(row.get("institution", "")) + '</td>'
            '<td>' + esc(row.get("counterpart", "")) + '</td>'
            '<td class="tr">' + esc(row.get("amount", "")) + '</td>'
            '<td class="tc">' + esc(row.get("contract_date", "")) + '</td>'
            '</tr>\n'
        )

    keyword_buttons = (
        '<button class="btn active" data-kind="keyword" onclick="filterTable(this,\'keyword\',\'all\')">'
        '전체 <span class="cnt">' + str(len(records)) + '</span></button>\n'
    )
    for keyword in all_keywords:
        count = sum(1 for row in records if keyword in row.get("keywords", []))
        keyword_buttons += (
            '<button class="btn" data-kind="keyword" onclick="filterTable(this,\'keyword\',\'' + esc(keyword) + '\')">'
            + esc(keyword) + ' <span class="cnt">' + str(count) + '</span></button>\n'
        )

    css = """
*{box-sizing:border-box}body{margin:0;font-family:'Malgun Gothic',Arial,sans-serif;font-size:13px;color:#2f343b;background:#f4f6f8}.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}.header{background:#245a92;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:16px}.header h1{font-size:19px;margin:0 0 7px}.meta{font-size:12px;opacity:.88}.panel,.toolbar{background:#fff;border:1px solid #dce4ec;border-radius:8px;margin-bottom:14px}.panel{padding:14px 16px}.panel h2{font-size:12px;color:#69727d;margin:0 0 10px}.filters,.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.btn{border:1px solid #2f6fa8;color:#245a92;background:#fff;border-radius:18px;padding:6px 12px;font-size:12px;cursor:pointer;font-family:inherit}.btn:hover{background:#eef5fb}.btn.active{background:#245a92;color:#fff}.cnt{background:rgba(36,90,146,.1);border-radius:10px;padding:1px 6px;margin-left:3px}.btn.active .cnt{background:rgba(255,255,255,.25)}.summary{display:flex;justify-content:space-between;gap:12px;align-items:center;background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:12px 16px;margin-bottom:14px}.summary strong{font-size:17px;color:#c0392b}.s2b-link{color:#245a92;text-decoration:none}.toolbar{padding:10px 12px}.toolbar-spacer{flex:1}.action-btn{height:30px;border:1px solid #245a92;border-radius:6px;background:#245a92;color:#fff;font-family:inherit;font-size:12px;padding:0 10px;cursor:pointer}.action-btn.danger{border-color:#b33a3a;background:#b33a3a}.action-btn.secondary{background:#fff;color:#245a92}.action-btn.filter-toggle{position:relative;border-color:#245a92;background:#fff;color:#245a92}.action-btn.filter-toggle.active{background:#245a92;color:#fff;box-shadow:0 0 0 3px rgba(36,90,146,.14)}.action-btn.filter-toggle.active::before{content:'ON';font-weight:700;margin-right:5px}.filter-state{display:inline-flex;align-items:center;height:30px;border:1px solid #d4e2ef;border-radius:15px;background:#f7fbff;color:#5c6670;padding:0 10px;font-size:12px}.filter-state.active{border-color:#245a92;background:#e8f1fa;color:#245a92;font-weight:600}.action-btn:hover{filter:brightness(.95)}.date-tools{gap:10px}.date-toggle{display:inline-flex;align-items:center;gap:5px;height:30px;border:1px solid #bfd0df;border-radius:16px;padding:0 10px;background:#fff;color:#263442;font-size:12px}.date-toggle input{margin:0}.date-input{height:30px;border:1px solid #b9c7d6;border-radius:6px;padding:0 8px;font-family:inherit;font-size:12px}.range-note{font-size:12px;color:#5c6670}.sync-status{font-size:12px;color:#5c6670}.sync-status.ok{color:#1d7a38}.sync-status.error{color:#b33a3a}.table-wrap{background:#fff;border:1px solid #e2e6ea;border-radius:8px;overflow:auto}table{width:100%;border-collapse:collapse;min-width:1120px}thead tr{background:#245a92;color:#fff}th{padding:11px 9px;font-size:12px;font-weight:600;white-space:nowrap}td{padding:10px 9px;border-bottom:1px solid #edf0f2;vertical-align:middle}tbody tr{content-visibility:auto;contain-intrinsic-size:52px}tbody tr:hover td{background:#f8fbff}.tc{text-align:center}.tr{text-align:right}.select-cell{width:36px}.row-no{color:#89939e;width:42px}.contract-link{color:#1769aa;text-decoration:none}.contract-link:hover{text-decoration:underline}.tags{margin-top:5px}.tag{display:inline-block;background:#e8f1fa;color:#245a92;border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px 0 0}.region-cell{min-width:210px}.region-editor{display:flex;gap:6px;justify-content:center;align-items:center}.region-select{height:28px;max-width:160px;border:1px solid #b9c7d6;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:12px;padding:0 6px}.region-save{height:28px;border:1px solid #245a92;border-radius:6px;background:#245a92;color:#fff;font-family:inherit;font-size:12px;padding:0 8px;cursor:pointer}.region-save:disabled,.action-btn:disabled{opacity:.65;cursor:wait}.region-text{font-weight:600;color:#263442}td:nth-child(8){font-weight:600;white-space:nowrap}td:nth-child(9){font-size:12px;color:#5c6670;white-space:nowrap}.no-result{text-align:center;padding:54px 20px;color:#8a94a0;display:none}.footer{text-align:center;color:#9aa3ad;font-size:11px;margin-top:18px}@media(max-width:720px){.wrap{padding:12px 8px}.header{padding:16px}.summary,.toolbar{align-items:flex-start;flex-direction:column}.panel{padding:12px}.btn{padding:6px 10px}.region-editor{flex-direction:column}.region-select,.region-save{width:100%;max-width:none}}
""".strip()

    js = """
var activeKeyword='all';
var unsavedOnly=false;
var dateMode='previous';
var selectedDateFrom='';
var selectedDateTo='';
var defaultDateFrom='__DEFAULT_DATE_FROM__';
var defaultDateTo='__DEFAULT_DATE_TO__';
var regionStorageKey='s2b-region-overrides-v1';
var deletedStorageKey='s2b-deleted-records-v1';
var supabaseUrlKey='s2b-supabase-url-v1';
var supabaseAnonKey='s2b-supabase-anon-key-v1';
var supabaseDefaultUrl='https://fozuzbszeujgskjasvzq.supabase.co';
var supabaseDefaultAnonKey='sb_publishable_bFJbCmjIbzCEracNlI-lhA_9hYn1rdc';
function readJsonStorage(key,fallback){try{return JSON.parse(localStorage.getItem(key)||JSON.stringify(fallback));}catch(e){return fallback;}}
function writeJsonStorage(key,value){localStorage.setItem(key,JSON.stringify(value));}
function getRegionOverrides(){return readJsonStorage(regionStorageKey,{});}
function getDeletedRecords(){return readJsonStorage(deletedStorageKey,[]);}
function setStatus(message,type){var el=document.getElementById('sync-status');if(!el){return;}el.textContent=message||'';el.className='sync-status '+(type||'');}
function cleanSupabaseUrl(url){return (url||'').trim().replace(/\\/$/,'');}
function getSupabaseConfig(){return {url:cleanSupabaseUrl(localStorage.getItem(supabaseUrlKey)||supabaseDefaultUrl||''),key:localStorage.getItem(supabaseAnonKey)||supabaseDefaultAnonKey||''};}
function supabaseHeaders(){var cfg=getSupabaseConfig();return {'apikey':cfg.key,'Authorization':'Bearer '+cfg.key,'Content-Type':'application/json'};}
async function supabaseFetch(path,options){var cfg=getSupabaseConfig();if(!cfg.url||!cfg.key){throw new Error('Supabase config is required.');}var response=await fetch(cfg.url+path,Object.assign({headers:supabaseHeaders()},options||{}));if(!response.ok){var text='';try{text=await response.text();}catch(e){}throw new Error('Supabase request failed: '+response.status+(text?' '+text.slice(0,120):''));}return response;}
async function loadSupabaseRegions(){var response=await supabaseFetch('/rest/v1/region_overrides?select=record_id,region&limit=10000',{method:'GET'});var rows=await response.json();var map={};rows.forEach(function(row){if(row.record_id&&row.region){map[row.record_id]=row.region;}});return map;}
async function loadSupabaseDeleted(){var response=await supabaseFetch('/rest/v1/deleted_records?select=record_id&limit=10000',{method:'GET'});var rows=await response.json();return rows.map(function(row){return row.record_id;}).filter(Boolean);}
async function upsertSupabaseRegion(id,region){await supabaseFetch('/rest/v1/region_overrides?on_conflict=record_id',{method:'POST',headers:Object.assign(supabaseHeaders(),{'Prefer':'resolution=merge-duplicates,return=minimal'}),body:JSON.stringify({record_id:id,region:region})});}
async function upsertSupabaseDeleted(ids){var rows=ids.map(function(id){return {record_id:id};});await supabaseFetch('/rest/v1/deleted_records?on_conflict=record_id',{method:'POST',headers:Object.assign(supabaseHeaders(),{'Prefer':'resolution=merge-duplicates,return=minimal'}),body:JSON.stringify(rows)});}
function rowHasRegion(row){var id=row.getAttribute('data-record-id');var regions=getRegionOverrides();var fixed=row.querySelector('.fixed-region');return !!(fixed||(id&&regions[id]));}
function rowMatchesDate(row){var date=row.getAttribute('data-contract-date')||'';if(dateMode==='all'){return true;}if(dateMode==='search'){return !!date&&!!selectedDateFrom&&!!selectedDateTo&&date>=selectedDateFrom&&date<=selectedDateTo;}return !!date&&date>=defaultDateFrom&&date<=defaultDateTo;}
function renderSavedRegion(editor,value){var span=document.createElement('span');span.className='region-text saved-region';span.setAttribute('data-region-id',editor.getAttribute('data-region-id')||'');span.textContent=value;editor.replaceWith(span);}
function applyRegions(regions){document.querySelectorAll('.region-editor').forEach(function(editor){var id=editor.getAttribute('data-region-id');var value=id?regions[id]:'';if(value){renderSavedRegion(editor,value);}});}
function applyDeleted(deleted){var deletedSet=new Set(deleted||[]);document.querySelectorAll('#tbody tr').forEach(function(row){row.setAttribute('data-deleted',deletedSet.has(row.getAttribute('data-record-id'))?'1':'0');});filterCurrent();}
async function loadRemoteState(){try{var regionRemote=await loadSupabaseRegions();var deletedRemote=await loadSupabaseDeleted();writeJsonStorage(regionStorageKey,regionRemote);writeJsonStorage(deletedStorageKey,deletedRemote);applyRegions(regionRemote);applyDeleted(deletedRemote);setStatus('Supabase data loaded.','ok');}catch(error){applyRegions(getRegionOverrides());applyDeleted(getDeletedRecords());setStatus(error.message||'Could not load Supabase data.','error');}}
async function saveRegion(button){var editor=button.closest('.region-editor');if(!editor){return;}var select=editor.querySelector('.region-select');var value=select?select.value:'';var id=editor.getAttribute('data-region-id');if(!value||!id){setStatus('Select a region.','error');return;}button.disabled=true;button.textContent='Saving';setStatus('Saving region to Supabase...');try{await upsertSupabaseRegion(id,value);var regions=getRegionOverrides();regions[id]=value;writeJsonStorage(regionStorageKey,regions);renderSavedRegion(editor,value);if(unsavedOnly){filterCurrent();}setStatus('Region saved to Supabase.','ok');}catch(error){setStatus(error.message||'Failed to save deletes.','error');button.disabled=false;button.textContent='Save';}}
function selectedIds(){return Array.from(document.querySelectorAll('#tbody tr')).filter(function(row){return row.style.display!=='none'&&row.querySelector('.row-check')&&row.querySelector('.row-check').checked;}).map(function(row){return row.getAttribute('data-record-id');});}
async function deleteSelected(){var ids=selectedIds();if(!ids.length){setStatus('Check notices to delete.','error');return;}if(!confirm(ids.length+' items will be deleted from the list. Continue?')){return;}var btn=document.getElementById('delete-selected');btn.disabled=true;setStatus('Saving selected deletes to Supabase...');try{await upsertSupabaseDeleted(ids);var merged=Array.from(new Set(getDeletedRecords().concat(ids))).sort();writeJsonStorage(deletedStorageKey,merged);applyDeleted(merged);setStatus('Selected notices deleted.','ok');}catch(error){setStatus(error.message||'Failed to save deletes.','error');}finally{btn.disabled=false;}}
function toggleAll(master){document.querySelectorAll('#tbody tr').forEach(function(row){if(row.style.display!=='none'){var cb=row.querySelector('.row-check');if(cb){cb.checked=master.checked;}}});}
function updateUnsavedUi(){var btn=document.getElementById('unsaved-only-btn');if(btn){btn.classList.toggle('active',unsavedOnly);btn.setAttribute('aria-pressed',unsavedOnly?'true':'false');btn.textContent=unsavedOnly?'\uC9C0\uC5ED \uBBF8\uC800\uC7A5\uB9CC \uD45C\uC2DC \uC911':'\uC9C0\uC5ED \uBBF8\uC800\uC7A5\uB9CC \uBCF4\uAE30';}var state=document.getElementById('filter-state');if(state){state.classList.toggle('active',unsavedOnly);state.textContent=unsavedOnly?'\uC9C0\uC5ED \uBBF8\uC800\uC7A5 \uD544\uD130 \uC801\uC6A9 \uC911':'\uC9C0\uC5ED \uD544\uD130 \uAEBC\uC9D0';}}
function filterCurrent(){updateUnsavedUi();var rows=document.querySelectorAll('#tbody tr');var visible=0;rows.forEach(function(row){var kws=(row.getAttribute('data-keywords')||'').split(',');var keywordMatch=activeKeyword==='all'||kws.indexOf(activeKeyword)!==-1;var deleted=row.getAttribute('data-deleted')==='1';var unsavedMatch=!unsavedOnly||!rowHasRegion(row);var show=keywordMatch&&!deleted&&unsavedMatch&&rowMatchesDate(row);row.style.display=show?'':'none';var cb=row.querySelector('.row-check');if(!show&&cb){cb.checked=false;}if(show){visible++;}});document.getElementById('visible-count').textContent=visible;document.getElementById('no-result').style.display=visible===0?'block':'none';var number=1;rows.forEach(function(row){if(row.style.display!=='none'){row.querySelector('.row-no').textContent=number++;}});var master=document.querySelector('thead input[type="checkbox"]');if(master){master.checked=false;}}
function filterTable(btn,kind,value){activeKeyword=value;document.querySelectorAll('[data-kind="keyword"]').forEach(function(item){item.classList.remove('active');});btn.classList.add('active');filterCurrent();}
function toggleUnsavedOnly(){unsavedOnly=!unsavedOnly;filterCurrent();}
function syncDateToggles(){var previous=document.getElementById('previous-view');var all=document.getElementById('all-view');if(previous){previous.checked=dateMode==='previous';}if(all){all.checked=dateMode==='all';}}
function clearDateInputs(){var from=document.getElementById('date-from-filter');var to=document.getElementById('date-to-filter');if(from){from.value='';}if(to){to.value='';}}
function setPreviousView(checked){dateMode=checked?'previous':'all';selectedDateFrom='';selectedDateTo='';clearDateInputs();syncDateToggles();filterCurrent();}
function setAllView(checked){dateMode=checked?'all':'previous';selectedDateFrom='';selectedDateTo='';clearDateInputs();syncDateToggles();filterCurrent();}
function applyDateSearch(){var from=document.getElementById('date-from-filter');var to=document.getElementById('date-to-filter');selectedDateFrom=from?from.value:'';selectedDateTo=to?to.value:'';if(!selectedDateFrom&&!selectedDateTo){setStatus('Select a date range.','error');return;}if(!selectedDateFrom){selectedDateFrom=selectedDateTo;}if(!selectedDateTo){selectedDateTo=selectedDateFrom;}if(selectedDateFrom>selectedDateTo){var tmp=selectedDateFrom;selectedDateFrom=selectedDateTo;selectedDateTo=tmp;}if(from){from.value=selectedDateFrom;}if(to){to.value=selectedDateTo;}dateMode='search';syncDateToggles();filterCurrent();setStatus('Showing selected date range.','ok');}
function clearDateSearch(){dateMode='previous';selectedDateFrom='';selectedDateTo='';clearDateInputs();syncDateToggles();filterCurrent();}
document.addEventListener('DOMContentLoaded',function(){syncDateToggles();filterCurrent();var start=function(){loadRemoteState();};if('requestIdleCallback' in window){requestIdleCallback(start,{timeout:1200});}else{setTimeout(start,250);}});
""".strip()
    js = js.replace("__DEFAULT_DATE_FROM__", default_date_from).replace("__DEFAULT_DATE_TO__", default_date_to)

    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>S2B 수의계약 누적 내역</title>"
        "<style>" + css + "</style></head><body>"
        "<div class='wrap'>"
        "<div class='header'><h1>S2B 수의계약 누적 내역</h1>"
        "<div class='meta'>누적 생성: " + esc(exported_at) + "</div></div>"
        "<div class='panel'><h2>검색어로 필터링</h2><div class='filters'>" + keyword_buttons + "</div></div>"
        "<div class='summary'><span>총 <strong id='visible-count'>" + str(default_visible_count) + "</strong>건 표시 중</span>"
        "<a href='" + esc(ref_url) + "' target='_blank' rel='noopener' class='s2b-link'>S2B 수의계약 내역 바로가기 &#8599;</a></div>"
        "<div class='toolbar date-tools'><label class='date-toggle'><input type='checkbox' id='previous-view' checked onchange='setPreviousView(this.checked)'>&#51060;&#51204;&#51068; &#48372;&#44592;</label><label class='date-toggle'><input type='checkbox' id='all-view' onchange='setAllView(this.checked)'>&#51204;&#52404; &#48372;&#44592;</label><span class='range-note'>&#44592;&#48376; &#48276;&#50948;: " + esc(default_range_label) + "</span><input type='date' id='date-from-filter' class='date-input' aria-label='&#44228;&#50557;&#52404;&#44208;&#51068; &#49884;&#51089;&#51068;'><span class='range-note'>~</span><input type='date' id='date-to-filter' class='date-input' aria-label='&#44228;&#50557;&#52404;&#44208;&#51068; &#51333;&#47308;&#51068;'><button type='button' class='action-btn secondary' onclick='applyDateSearch()'>&#45216;&#51676; &#44160;&#49353;</button><button type='button' class='action-btn secondary' onclick='clearDateSearch()'>&#52488;&#44592;&#54868;</button></div>"
        "<div class='toolbar'><button type='button' id='delete-selected' class='action-btn danger' onclick='deleteSelected()'>&#49440;&#53469; &#49325;&#51228;</button>"
        "<button type='button' id='unsaved-only-btn' class='action-btn filter-toggle' aria-pressed='false' onclick='toggleUnsavedOnly()'>&#51648;&#50669; &#48120;&#51200;&#51109;&#47564; &#48372;&#44592;</button>"
        "<span id='filter-state' class='filter-state'>&#51648;&#50669; &#54596;&#53552; &#44732;&#51664;</span>"
        "<span class='toolbar-spacer'></span><span id='sync-status' class='sync-status'>Supabase sync pending.</span></div>"
        "<div class='table-wrap'><table><thead><tr>"
        "<th><input type='checkbox' aria-label='전체 선택' onclick='toggleAll(this)'></th><th>No</th><th style='text-align:left'>계약명</th><th>검색키워드</th><th>지역</th><th>계약기관</th><th>계약대상자</th>"
        "<th>금액</th><th>계약체결일</th>"
        "</tr></thead><tbody id='tbody'>" + rows_html + "</tbody></table>"
        "<div class='no-result' id='no-result'>표시할 계약내역이 없습니다.</div></div>"
        "<div class='footer'>로컬 파일 자동 생성 · " + esc(exported_at) + "</div>"
        "</div><script>" + js + "</script></body></html>"
    )

def save_cumulative_html(data):
    html_text = build_cumulative_html(data)
    with open(CUMULATIVE_HTML_FILE, "w", encoding="utf-8") as file:
        file.write(html_text)
    print("[html] saved: " + CUMULATIVE_HTML_FILE)
    with open(INDEX_HTML_FILE, "w", encoding="utf-8") as file:
        file.write(html_text)
    print("[html] saved: " + INDEX_HTML_FILE)
    return CUMULATIVE_HTML_FILE



def run_git(args, timeout=120):
    return subprocess.run(
        ["git"] + args,
        cwd=APP_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def publish_to_github(date_from, date_to, enabled=AUTO_GITHUB_UPLOAD):
    if not enabled:
        print("[github] skipped: auto upload is disabled")
        return False

    repo_check = run_git(["rev-parse", "--is-inside-work-tree"])
    if repo_check.returncode != 0:
        print("[github] skipped: this folder is not a configured Git repository.")
        print("[github] setup once: git init, git remote add origin <GitHub URL>, then git push -u origin main")
        return False

    remote = run_git(["remote", "get-url", "origin"])
    if remote.returncode != 0:
        print("[github] skipped: origin remote is not configured.")
        print(remote.stdout.strip())
        return False

    sync = run_git(["pull", "--rebase", "--autostash", "origin", "main"], timeout=180)
    if sync.returncode != 0:
        print("[github] git pull failed:\n" + sync.stdout.strip())
        return False

    files = [name for name in GITHUB_UPLOAD_FILES if os.path.exists(os.path.join(APP_DIR, name))]
    if not files:
        print("[github] skipped: no cumulative files found")
        return False

    add = run_git(["add"] + files)
    if add.returncode != 0:
        print("[github] git add failed:\n" + add.stdout.strip())
        return False

    status = run_git(["status", "--porcelain", "--"] + files)
    if status.returncode != 0:
        print("[github] git status failed:\n" + status.stdout.strip())
        return False
    if not status.stdout.strip():
        print("[github] no changes to upload")
        return True

    msg = "Update S2B cumulative data " + display_date(date_from) + "~" + display_date(date_to)
    commit = run_git(["commit", "-m", msg])
    if commit.returncode != 0:
        print("[github] git commit failed:\n" + commit.stdout.strip())
        return False

    push = run_git(["push"], timeout=180)
    if push.returncode != 0:
        print("[github] git push failed:\n" + push.stdout.strip())
        print("[github] check GitHub login/token or run the first push manually.")
        return False

    print("[github] uploaded to GitHub")
    return True
def parse_args():
    parser = argparse.ArgumentParser(description="S2B 수의계약 로컬 누적 크롤러")
    parser.add_argument("--from", dest="date_from", help="검색 시작일: YYYYMMDD 또는 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="검색 종료일: YYYYMMDD 또는 YYYY-MM-DD")
    parser.add_argument("--keywords", help="검색할 키워드를 쉼표로 지정합니다. 예: 국어,수학,영어")
    parser.add_argument("--batch-size", type=int, default=0, help="키워드를 몇 개씩 나눠 실행할지 지정합니다.")
    parser.add_argument("--batch-index", type=int, default=1, help="실행할 키워드 묶음 번호입니다. 1부터 시작합니다.")
    parser.add_argument("--page-delay-min", type=float, default=PAGE_DELAY_RANGE[0], help="Minimum delay between page requests in seconds.")
    parser.add_argument("--page-delay-max", type=float, default=PAGE_DELAY_RANGE[1], help="Maximum delay between page requests in seconds.")
    parser.add_argument("--keyword-delay-min", type=float, default=KEYWORD_DELAY_RANGE[0], help="Minimum delay between keyword searches in seconds.")
    parser.add_argument("--keyword-delay-max", type=float, default=KEYWORD_DELAY_RANGE[1], help="Maximum delay between keyword searches in seconds.")
    parser.add_argument("--no-github-upload", action="store_false", dest="github_upload", default=AUTO_GITHUB_UPLOAD, help="Disable automatic GitHub upload after saving cumulative files.")
    parser.add_argument("--github-upload", action="store_true", dest="github_upload", help="Enable automatic GitHub upload after saving cumulative files.")
    return parser.parse_args()


def main():
    print("=" * 55)
    print("  S2B local cumulative crawler")
    print("=" * 55)
    args = parse_args()
    try:
        global PAGE_DELAY_RANGE, KEYWORD_DELAY_RANGE
        PAGE_DELAY_RANGE = validate_delay_range(args.page_delay_min, args.page_delay_max, "--page-delay")
        KEYWORD_DELAY_RANGE = validate_delay_range(args.keyword_delay_min, args.keyword_delay_max, "--keyword-delay")
        date_from, date_to = get_date_range_from_user(args)
        keywords = select_keywords(args)
    except ValueError as exc:
        print("[error] " + str(exc))
        return
    results = fetch_all(date_from, date_to, keywords)
    data = update_cumulative_json(results, date_from, date_to)
    save_cumulative_html(data)
    publish_to_github(date_from, date_to, args.github_upload)
    print("done.")


if __name__ == "__main__":
    main()

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
from datetime import datetime
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
    "기업", "논문", "노트", "노후", "대학", "대학교", "대회", "도서",
    "도서관", "도시", "로봇", "물품", "문화상품권", "보건실", "보드게임",
    "비품", "사회대", "사회복무요원", "샤워", "성인", "센서", "수리",
    "수학여행", "실험실", "안전", "어린이집", "연설대", "연필", "예술",
    "옥수수", "외국어", "외국어학교", "재료", "전자칠판", "체육", "체험",
    "축제", "취업", "캠프", "콘센트릴", "키트", "특수학교", "특수학급",
    "페스타", "페스티벌", "폐기물", "폐수통", "폐시약", "학술", "해외학교",
    "행사", "현장체험", "하루 한장",
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


def select_keywords(args):
    selected = KEYWORDS
    if args.keywords:
        requested = [keyword.strip() for keyword in args.keywords.split(",") if keyword.strip()]
        unknown = [keyword for keyword in requested if keyword not in KEYWORDS]
        if unknown:
            raise ValueError("등록되지 않은 검색 키워드입니다: " + ", ".join(unknown))
        selected = requested

    if args.batch_size:
        if args.batch_size < 1:
            raise ValueError("--batch-size는 1 이상이어야 합니다.")
        if args.batch_index < 1:
            raise ValueError("--batch-index는 1 이상이어야 합니다.")
        start = (args.batch_index - 1) * args.batch_size
        end = start + args.batch_size
        if start >= len(selected):
            raise ValueError("선택한 키워드 묶음에 포함되는 키워드가 없습니다.")
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


def esc(value):
    return html.escape(str(value or ""), quote=True)


def build_cumulative_html(data):
    records = data.get("records", [])
    exported_at = data.get("exported_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    last_period = data.get("meta", {}).get("lastSearchPeriod", "")
    ref_url = LIST_URL + "?forwardName=list03"

    all_keywords = []
    for keyword in KEYWORDS:
        if any(keyword in row.get("keywords", []) for row in records):
            all_keywords.append(keyword)

    rows_html = ""
    for index, row in enumerate(records, 1):
        keywords = row.get("keywords", [])
        keywords_joined = ",".join(keywords)
        tag_html = "".join('<span class="tag">' + esc(keyword) + "</span>" for keyword in keywords)
        contract_name = esc(row.get("contract_name", ""))
        if row.get("link"):
            name_html = (
                '<a href="' + esc(row.get("link")) + '" target="_blank" rel="noopener" '
                'class="contract-link">' + contract_name + " &#8599;</a>"
            )
        else:
            name_html = contract_name

        rows_html += (
            '<tr data-keywords="' + esc(keywords_joined) + '" data-level="' + esc(row.get("school_level", "")) + '">'
            '<td class="tc">' + str(index) + '</td>'
            '<td>' + name_html + '</td>'
            '<td><div class="tags">' + tag_html + '</div></td>'
            '<td class="tc">' + esc(row.get("region", "")) + '</td>'
            '<td>' + esc(row.get("institution", "")) + '</td>'
            '<td>' + esc(row.get("counterpart", "")) + '</td>'
            '<td class="tr">' + esc(row.get("amount", "")) + '</td>'
            '<td class="tc">' + esc(row.get("contract_date", "")) + '</td>'
            '<td class="tc">' + esc(row.get("search_period_from", "")) + '~' + esc(row.get("search_period_to", "")) + '</td>'
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
*{box-sizing:border-box}body{margin:0;font-family:'Malgun Gothic',Arial,sans-serif;font-size:13px;color:#2f343b;background:#f4f6f8}.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}.header{background:#245a92;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:16px}.header h1{font-size:19px;margin:0 0 7px}.meta{font-size:12px;opacity:.88}.panel{background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:14px 16px;margin-bottom:14px}.panel h2{font-size:12px;color:#69727d;margin:0 0 10px}.filters{display:flex;flex-wrap:wrap;gap:7px}.btn{border:1px solid #2f6fa8;color:#245a92;background:#fff;border-radius:18px;padding:6px 12px;font-size:12px;cursor:pointer;font-family:inherit}.btn:hover{background:#eef5fb}.btn.active{background:#245a92;color:#fff}.cnt{background:rgba(36,90,146,.1);border-radius:10px;padding:1px 6px;margin-left:3px}.btn.active .cnt{background:rgba(255,255,255,.25)}.summary{display:flex;justify-content:space-between;gap:12px;align-items:center;background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:12px 16px;margin-bottom:14px}.summary strong{font-size:17px;color:#c0392b}.s2b-link{color:#245a92;text-decoration:none}.table-wrap{background:#fff;border:1px solid #e2e6ea;border-radius:8px;overflow:auto}table{width:100%;border-collapse:collapse;min-width:1220px}thead tr{background:#245a92;color:#fff}th{padding:11px 9px;font-size:12px;font-weight:600;white-space:nowrap}td{padding:10px 9px;border-bottom:1px solid #edf0f2;vertical-align:middle}tbody tr:hover td{background:#f8fbff}.tc{text-align:center}.tr{text-align:right}.contract-link{color:#1769aa;text-decoration:none}.contract-link:hover{text-decoration:underline}.tags{margin-top:5px}.tag{display:inline-block;background:#e8f1fa;color:#245a92;border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px 0 0}td:nth-child(1){color:#89939e;width:42px}td:nth-child(7){font-weight:600;white-space:nowrap}td:nth-child(8),td:nth-child(9){font-size:12px;color:#5c6670;white-space:nowrap}.no-result{text-align:center;padding:54px 20px;color:#8a94a0;display:none}.footer{text-align:center;color:#9aa3ad;font-size:11px;margin-top:18px}@media(max-width:720px){.wrap{padding:12px 8px}.header{padding:16px}.summary{align-items:flex-start;flex-direction:column}.panel{padding:12px}.btn{padding:6px 10px}}
""".strip()

    js = """
var activeKeyword='all';
function filterTable(btn,kind,value){
  activeKeyword=value;
  document.querySelectorAll('[data-kind="keyword"]').forEach(function(item){item.classList.remove('active');});
  btn.classList.add('active');
  var rows=document.querySelectorAll('#tbody tr');
  var visible=0;
  rows.forEach(function(row){
    var kws=(row.getAttribute('data-keywords')||'').split(',');
    var show=activeKeyword==='all'||kws.indexOf(activeKeyword)!==-1;
    row.style.display=show?'':'none';
    if(show){visible++;}
  });
  document.getElementById('visible-count').textContent=visible;
  document.getElementById('no-result').style.display=visible===0?'block':'none';
  var number=1;
  rows.forEach(function(row){if(row.style.display!=='none'){row.cells[0].textContent=number++;}});
}
""".strip()

    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>S2B 누적 수의계약 내역</title>"
        "<style>" + css + "</style></head><body>"
        "<div class='wrap'>"
        "<div class='header'><h1>S2B 수의계약 누적 내역</h1>"
        "<div class='meta'>누적 생성: " + esc(exported_at) + " &nbsp;|&nbsp; 마지막 검색기간: "
        + esc(last_period) + "</div></div>"
        "<div class='panel'><h2>검색어로 필터링</h2><div class='filters'>" + keyword_buttons + "</div></div>"
        "<div class='summary'><span>총 <strong id='visible-count'>" + str(len(records)) + "</strong>건 표시 중</span>"
        "<a href='" + esc(ref_url) + "' target='_blank' rel='noopener' class='s2b-link'>S2B 수의계약 내역 바로가기 &#8599;</a></div>"
        "<div class='table-wrap'><table><thead><tr>"
        "<th>No</th><th style='text-align:left'>계약명</th><th>검색키워드</th><th>지역</th><th>계약기관</th><th>계약대상자</th>"
        "<th>금액</th><th>계약체결일</th><th>수집 검색기간</th>"
        "</tr></thead><tbody id='tbody'>" + rows_html + "</tbody></table>"
        "<div class='no-result' id='no-result'>해당 검색어의 계약내역이 없습니다.</div></div>"
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

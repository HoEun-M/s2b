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
    "에듀테크", "AI",
]
EXCLUDE_WORDS = [
    "거치대", "건설", "경연대회", "공사", "공연", "공책",
    "교구", "교재", "급식", "기기", "기자재", "기숙사",
    "기업", "논문", "노트", "노후", "대학", "대학교", "대회",
    "도서", "도서관", "도시", "로봇", "문화상품권", "보건실",
    "보드게임", "비품", "사회대", "사회복무요원", "샤워", "성인", "센서",
    "수리", "수학여행", "실험실", "안전", "어린이집", "연설대", "연필",
    "예술", "옥수수", "외국어", "외국어학교", "재료", "전자칠판", "화이트보드",
    "체험", "축제", "취업", "캠프", "콘센트릴", "키트", "특수학교",
    "특수학급", "페스타", "페스티벌", "폐기물", "폐수통", "폐시약", "학술",
    "해외학교", "행사", "현장체험", "하루 한장", "플라스크", "간식", "워크북",
    "준비물", "용역", "스탠드", "강사", "차량", "한국어", "다국어",
    "도화지", "박철완", "개정판", "제작", "다락원", "빠작", "뿌리",
    "박연수", "오리온", "마은정", "드림디포", "황정하", "기탄", "이유민",
    "손진현", "국어사전", "수학사랑",
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
SCHOOL_SUFFIXES = ("초등학교", "중학교", "고등학교", "특수학교", "각종학교")
ADDRESS_DISTRICT_PATTERN = re.compile(r"([가-힣]+(?:시|군|구))")
DISTRICT_SUPPORT_OFFICE_OVERRIDES = {
    ("강원", "속초시"): "강원특별자치도속초양양교육지원청",
    ("강원", "양양군"): "강원특별자치도속초양양교육지원청",
    ("경기", "구리시"): "경기도구리남양주교육지원청",
    ("경기", "남양주시"): "경기도구리남양주교육지원청",
    ("경기", "동두천시"): "경기도동두천양주교육지원청",
    ("경기", "양주시"): "경기도동두천양주교육지원청",
    ("경기", "안양시"): "경기도안양과천교육지원청",
    ("경기", "과천시"): "경기도안양과천교육지원청",
    ("경기", "군포시"): "경기도군포의왕교육지원청",
    ("경기", "의왕시"): "경기도군포의왕교육지원청",
    ("경기", "광주시"): "경기도광주하남교육지원청",
    ("경기", "하남시"): "경기도광주하남교육지원청",
    ("경기", "화성시"): "경기도화성오산교육지원청",
    ("경기", "오산시"): "경기도화성오산교육지원청",
    ("충남", "논산시"): "충청남도논산계룡교육지원청",
    ("충남", "계룡시"): "충청남도논산계룡교육지원청",
    ("충북", "괴산군"): "충청북도괴산증평교육지원청",
    ("충북", "증평군"): "충청북도괴산증평교육지원청",
    ("서울", "강남구"): "서울특별시강남서초교육지원청",
    ("서울", "서초구"): "서울특별시강남서초교육지원청",
    ("서울", "강동구"): "서울특별시강동송파교육지원청",
    ("서울", "송파구"): "서울특별시강동송파교육지원청",
    ("서울", "강서구"): "서울특별시강서양천교육지원청",
    ("서울", "양천구"): "서울특별시강서양천교육지원청",
    ("서울", "동작구"): "서울특별시동작관악교육지원청",
    ("서울", "관악구"): "서울특별시동작관악교육지원청",
    ("서울", "성동구"): "서울특별시성동광진교육지원청",
    ("서울", "광진구"): "서울특별시성동광진교육지원청",
    ("서울", "성북구"): "서울특별시성북강북교육지원청",
    ("서울", "강북구"): "서울특별시성북강북교육지원청",
    ("서울", "구로구"): "서울특별시남부교육지원청",
    ("서울", "금천구"): "서울특별시남부교육지원청",
    ("서울", "영등포구"): "서울특별시남부교육지원청",
    ("서울", "노원구"): "서울특별시북부교육지원청",
    ("서울", "도봉구"): "서울특별시북부교육지원청",
    ("서울", "동대문구"): "서울특별시동부교육지원청",
    ("서울", "중랑구"): "서울특별시동부교육지원청",
    ("서울", "마포구"): "서울특별시서부교육지원청",
    ("서울", "서대문구"): "서울특별시서부교육지원청",
    ("서울", "은평구"): "서울특별시서부교육지원청",
    ("서울", "용산구"): "서울특별시중부교육지원청",
    ("서울", "종로구"): "서울특별시중부교육지원청",
    ("서울", "중구"): "서울특별시중부교육지원청",
    ("대구", "수성구"): "대구광역시동부교육지원청",
    ("대구", "동구"): "대구광역시동부교육지원청",
    ("대구", "중구"): "대구광역시동부교육지원청",
    ("대구", "남구"): "대구광역시남부교육지원청",
    ("대구", "달서구"): "대구광역시남부교육지원청",
    ("대구", "북구"): "대구광역시서부교육지원청",
    ("대구", "서구"): "대구광역시서부교육지원청",
    ("대구", "달성군"): "대구광역시달성교육지원청",
    ("대구", "군위군"): "대구광역시군위교육지원청",
    ("인천", "연수구"): "인천광역시동부교육지원청",
    ("인천", "남동구"): "인천광역시동부교육지원청",
    ("인천", "부평구"): "인천광역시북부교육지원청",
    ("인천", "계양구"): "인천광역시북부교육지원청",
    ("인천", "미추홀구"): "인천광역시남부교육지원청",
    ("인천", "옹진군"): "인천광역시남부교육지원청",
    ("인천", "서구"): "인천광역시서부교육지원청",
    ("인천", "강화군"): "인천광역시강화교육지원청",
    ("광주", "동구"): "광주광역시동부교육지원청",
    ("광주", "북구"): "광주광역시동부교육지원청",
    ("광주", "서구"): "광주광역시서부교육지원청",
    ("광주", "남구"): "광주광역시서부교육지원청",
    ("광주", "광산구"): "광주광역시서부교육지원청",
}
REGION_DISTRICT_PREFIXES = {
    "경기": {"수원": "수원시", "성남": "성남시", "의정부": "의정부시", "안양": "안양시", "부천": "부천시", "광명": "광명시", "평택": "평택시", "동두천": "동두천시", "안산": "안산시", "고양": "고양시", "과천": "과천시", "구리": "구리시", "남양주": "남양주시", "오산": "오산시", "시흥": "시흥시", "군포": "군포시", "의왕": "의왕시", "하남": "하남시", "용인": "용인시", "파주": "파주시", "이천": "이천시", "안성": "안성시", "김포": "김포시", "화성": "화성시", "광주": "광주시", "양주": "양주시", "포천": "포천시", "여주": "여주시", "연천": "연천군", "가평": "가평군", "양평": "양평군"},
    "강원": {"춘천": "춘천시", "원주": "원주시", "강릉": "강릉시", "동해": "동해시", "태백": "태백시", "속초": "속초시", "삼척": "삼척시", "홍천": "홍천군", "횡성": "횡성군", "영월": "영월군", "평창": "평창군", "정선": "정선군", "철원": "철원군", "화천": "화천군", "양구": "양구군", "인제": "인제군", "고성": "고성군", "양양": "양양군"},
    "충북": {"청주": "청주시", "충주": "충주시", "제천": "제천시", "보은": "보은군", "옥천": "옥천군", "영동": "영동군", "증평": "증평군", "진천": "진천군", "괴산": "괴산군", "음성": "음성군", "단양": "단양군"},
    "충남": {"천안": "천안시", "공주": "공주시", "보령": "보령시", "아산": "아산시", "서산": "서산시", "논산": "논산시", "계룡": "계룡시", "당진": "당진시", "금산": "금산군", "부여": "부여군", "서천": "서천군", "청양": "청양군", "홍성": "홍성군", "예산": "예산군", "태안": "태안군"},
    "전북": {"전주": "전주시", "군산": "군산시", "익산": "익산시", "정읍": "정읍시", "남원": "남원시", "김제": "김제시", "완주": "완주군", "진안": "진안군", "무주": "무주군", "장수": "장수군", "임실": "임실군", "순창": "순창군", "고창": "고창군", "부안": "부안군"},
    "전남": {"목포": "목포시", "여수": "여수시", "순천": "순천시", "나주": "나주시", "광양": "광양시", "담양": "담양군", "곡성": "곡성군", "구례": "구례군", "고흥": "고흥군", "보성": "보성군", "화순": "화순군", "장흥": "장흥군", "강진": "강진군", "해남": "해남군", "영암": "영암군", "무안": "무안군", "함평": "함평군", "영광": "영광군", "장성": "장성군", "완도": "완도군", "진도": "진도군", "신안": "신안군"},
    "경북": {"포항": "포항시", "경주": "경주시", "김천": "김천시", "안동": "안동시", "구미": "구미시", "영주": "영주시", "영천": "영천시", "상주": "상주시", "문경": "문경시", "경산": "경산시", "의성": "의성군", "청송": "청송군", "영양": "영양군", "영덕": "영덕군", "청도": "청도군", "고령": "고령군", "성주": "성주군", "칠곡": "칠곡군", "예천": "예천군", "봉화": "봉화군", "울진": "울진군", "울릉": "울릉군"},
    "경남": {"창원": "창원시", "진주": "진주시", "통영": "통영시", "사천": "사천시", "김해": "김해시", "밀양": "밀양시", "거제": "거제시", "양산": "양산시", "의령": "의령군", "함안": "함안군", "창녕": "창녕군", "고성": "고성군", "남해": "남해군", "하동": "하동군", "산청": "산청군", "함양": "함양군", "거창": "거창군", "합천": "합천군"},
    "제주": {"제주": "제주시", "서귀포": "서귀포시"},
}
SCHOOL_LOOKUP_ALIASES = {
    "서울교대부설초등학교": "서울교육대학교부설초등학교",
    "서울교대부설초등학교서무과": "서울교육대학교부설초등학교",
}
SCHOOL_DISTRICT_OVERRIDES = {
    "서울성수초등학교": ("서울", "성동구"),
    "서울신가초등학교": ("서울", "송파구"),
    "서울교대부설초등학교": ("서울", "서초구"),
    "서울교육대학교부설초등학교": ("서울", "서초구"),
}
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


def fetch_by_keyword(session, keyword, date_from, date_to, backfill_terms=None):
    results = []
    backfill_terms = backfill_terms or []
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

        if backfill_terms:
            filtered = [
                record for record in records
                if keyword in record["계약명"]
                and any(term in record["계약명"] for term in backfill_terms)
            ]
        else:
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


def parse_backfill_terms(raw_terms):
    return [term.strip() for term in (raw_terms or "").split(",") if term.strip()]


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


def fetch_all(date_from, date_to, keywords, backfill_terms=None):
    backfill_terms = backfill_terms or []
    print("[period] " + display_date(date_from) + " ~ " + display_date(date_to))
    print("[keywords] " + ", ".join(keywords))
    if backfill_terms:
        print("[backfill excluded terms] " + ", ".join(backfill_terms) + "\n")
    else:
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
        items = fetch_by_keyword(session, keyword, date_from, date_to, backfill_terms)
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


def school_category(institution):
    text = institution or ""
    if any(token in text for token in ("교육청", "교육지원청", "교육원", "센터", "대학교")):
        return "기타기관"
    if "유치원" in text:
        return "유치원"
    if "초등학교" in text:
        return "초등학교"
    if "중학교" in text:
        return "중학교"
    if "고등학교" in text or "과학고" in text or "체육고" in text:
        return "고등학교"
    if "학교" in text:
        return "기타학교"
    return "기타기관"


def normalize_school_name(value):
    return re.sub(r"\s+", "", value or "")


def school_lookup_names(value):
    raw = (value or "").strip()
    if not raw:
        return []
    seeds = [
        raw,
        re.sub(r"\([^)]*\)", "", raw).strip(),
        re.sub(r"\[[^\]]*\]", "", raw).strip(),
    ]
    names = []
    for seed in seeds:
        for candidate in (seed, seed.replace(" ", "")):
            for suffix in ("\ubcd1\uc124\uc720\uce58\uc6d0",):
                if candidate.endswith(suffix):
                    candidate = candidate[:-len(suffix)].strip()
            normalized = normalize_school_name(candidate)
            alias = SCHOOL_LOOKUP_ALIASES.get(normalized)
            for item in (candidate, alias):
                if item and item not in names:
                    names.append(item)
    return names

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


def district_from_school_name(region, institution):
    name = normalize_school_name(institution)
    for lookup_name in school_lookup_names(institution):
        override = SCHOOL_DISTRICT_OVERRIDES.get(normalize_school_name(lookup_name))
        if override and (not region or override[0] == region):
            return override
    if not name or not any(suffix in name for suffix in SCHOOL_SUFFIXES):
        return "", ""
    region_items = [(region, REGION_DISTRICT_PREFIXES.get(region, {}))] if region else REGION_DISTRICT_PREFIXES.items()
    for region_name, district_map in region_items:
        for prefix, district in sorted(district_map.items(), key=lambda item: len(item[0]), reverse=True):
            if name.startswith(prefix):
                if not region:
                    suffix_index = min((name.find(suffix) for suffix in SCHOOL_SUFFIXES if suffix in name), default=-1)
                    middle = name[len(prefix):suffix_index] if suffix_index > len(prefix) else ""
                    if len(middle) < 2:
                        continue
                return region_name, district
    return "", ""


def strip_region_prefix_from_school_name(institution, region=""):
    text = normalize_school_name(institution)
    if not text or not any(suffix in text for suffix in SCHOOL_SUFFIXES):
        return ""
    aliases = sorted(REGION_ALIASES, key=lambda item: len(item[0]), reverse=True)
    for alias, alias_region in aliases:
        alias_text = normalize_school_name(alias)
        if region and alias_region != region:
            continue
        if not alias_text or not text.startswith(alias_text):
            continue
        stripped = text[len(alias_text):]
        if stripped and stripped != text and any(suffix in stripped for suffix in SCHOOL_SUFFIXES):
            return stripped
    return ""


def district_prefix_from_text(value):
    text = value or ""
    match = ADDRESS_DISTRICT_PATTERN.search(text)
    if not match:
        return ""
    district = match.group(1)
    if len(district) <= 1:
        return ""
    return district[:-1]


def support_office_from_region_district(region, district):
    if not region or not district:
        return ""
    override = DISTRICT_SUPPORT_OFFICE_OVERRIDES.get((region, district))
    if override:
        return override
    base = district[:-1]
    if region == "경기":
        return "경기도" + base + "교육지원청"
    if region == "전남":
        return "전라남도" + base + "교육지원청"
    if region == "전북":
        return "전북특별자치도" + base + "교육지원청"
    if region == "경북":
        return "경상북도" + base + "교육지원청"
    if region == "경남":
        return base + "교육지원청"
    if region == "충남":
        return "충청남도" + base + "교육지원청"
    if region == "충북":
        return "충청북도" + base + "교육지원청"
    if region == "강원":
        return "강원특별자치도" + base + "교육지원청"
    if region == "제주":
        return "서귀포시교육지원청" if district == "서귀포시" else "제주시교육지원청"
    return ""


def normalize_support_office_name(region, support_office):
    text = support_office or ""
    mixed_prefix = "전남광주통합특별시"
    if text.startswith(mixed_prefix):
        tail = text[len(mixed_prefix):]
        if region == "전남":
            return "전라남도" + tail
        if region == "광주":
            if tail.startswith("광주"):
                tail = tail[len("광주"):]
            return "광주광역시" + tail
    return text


def region_from_support_office(support_office):
    text = support_office or ""
    if not text:
        return ""
    mixed_prefix = "전남광주통합특별시"
    if text.startswith(mixed_prefix):
        tail = text[len(mixed_prefix):]
        district_base = tail.replace("교육지원청", "")
        jeonnam_bases = {district[:-1] for district in REGION_DISTRICT_PREFIXES.get("전남", {}).values()}
        return "전남" if district_base in jeonnam_bases else "광주"
    for alias, region in sorted(REGION_ALIASES, key=lambda item: len(item[0]), reverse=True):
        if alias in text:
            return region
    for region, district_map in REGION_DISTRICT_PREFIXES.items():
        for district in district_map.values():
            base = district[:-1]
            if text.startswith(base + "교육지원청") or text.startswith(base + "시교육지원청") or text.startswith(base + "군교육지원청") or text.startswith(base + "구교육지원청"):
                return region
    for (region, _district), office in DISTRICT_SUPPORT_OFFICE_OVERRIDES.items():
        if text == office:
            return region
    return ""


def support_office_matches_region(region, support_office):
    if not region or not support_office:
        return True
    support_region = region_from_support_office(support_office)
    return not support_region or support_region == region


def normalize_record_region_support(region, support_office):
    normalized_region = region or region_from_support_office(support_office)
    text = support_office or ""
    mixed_prefix = "전남광주통합특별시"
    if text.startswith(mixed_prefix):
        tail = text[len(mixed_prefix):]
        district_base = tail.replace("교육지원청", "")
        jeonnam_bases = {district[:-1] for district in REGION_DISTRICT_PREFIXES.get("전남", {}).values()}
        if district_base in jeonnam_bases:
            normalized_region = "전남"
        elif not normalized_region:
            normalized_region = "광주"
    normalized_support = normalize_support_office_name(normalized_region, text)
    if not support_office_matches_region(normalized_region, normalized_support):
        normalized_support = ""
    return normalized_region, normalized_support


def normalize_region_candidate(candidate):
    normalized = dict(candidate or {})
    region = normalized.get("region", "")
    district = normalized.get("district", "")
    if region == "광주" and district in set(REGION_DISTRICT_PREFIXES.get("전남", {}).values()):
        region = "전남"
    normalized["region"] = region
    normalized["support_office"] = normalize_support_office_name(region, normalized.get("support_office", ""))
    return normalized


def support_office_from_institution(institution):
    text = institution or ""
    marker = "\uad50\uc721\uc9c0\uc6d0\uccad"
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    before = text[:marker_index + len(marker)]
    start = max(before.rfind(" ") + 1, before.rfind("(") + 1, before.rfind(")") + 1)
    return before[start:].strip()


def strip_district_prefix_from_school_name(institution, district_prefix):
    text = normalize_school_name(institution)
    prefix = normalize_school_name(district_prefix)
    if not text or not prefix or not any(suffix in text for suffix in SCHOOL_SUFFIXES):
        return ""
    if not text.startswith(prefix):
        return ""
    stripped = text[len(prefix):]
    if stripped and stripped != text and any(suffix in stripped for suffix in SCHOOL_SUFFIXES):
        return stripped
    return ""


def school_candidate(row):
    address = row.get("ORG_RDNMA") or ""
    region = short_region(row.get("LCTN_SC_NM") or address)
    district = ""
    parts = address.split()
    if len(parts) >= 2:
        district = parts[1]
    return normalize_region_candidate({
        "school_name": row.get("SCHUL_NM", ""),
        "region": region,
        "district": district,
        "education_office": row.get("ATPT_OFCDC_SC_NM", ""),
        "support_office": row.get("JU_ORG_NM", ""),
        "address": address,
        "school_code": row.get("SD_SCHUL_CODE", ""),
    })


def fetch_school_candidates(institution):
    lookup_names = school_lookup_names(institution)
    cache_key = normalize_school_name(lookup_names[0]) if lookup_names else ""
    if not cache_key:
        return []
    if cache_key in _school_region_cache:
        return _school_region_cache[cache_key]
    candidates = []
    for lookup_name in lookup_names:
        name = normalize_school_name(lookup_name)
        params = {
            "Type": "json",
            "pIndex": "1",
            "pSize": "100",
            "SCHUL_NM": lookup_name,
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
            if candidates:
                break
        except Exception as exc:
            print("[region] school lookup failed for " + lookup_name + ": " + str(exc))
    _school_region_cache[cache_key] = candidates
    return candidates


def fetch_region_stripped_school_candidates(institution, region):
    stripped = strip_region_prefix_from_school_name(institution, region)
    if not stripped:
        return []
    return fetch_school_candidates(stripped)


def fetch_district_stripped_school_candidates(institution, district_prefix):
    stripped = strip_district_prefix_from_school_name(institution, district_prefix)
    if not stripped:
        return []
    return fetch_school_candidates(stripped)


def resolve_region(institution):
    direct = region_from_institution_name(institution)
    if direct:
        candidates = fetch_region_stripped_school_candidates(institution, direct.get("region", ""))
        if candidates:
            direct["region_candidates"] = candidates
            direct["region_source"] = "institution_name_neis_stripped"
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
    district_region, district = district_from_school_name("", institution)
    if district_region and district:
        return {
            "region": district_region,
            "region_status": "matched",
            "region_source": "institution_district_prefix",
            "region_candidates": [],
        }
    return {"region": "", "region_status": "unknown", "region_source": "neis_school_info", "region_candidates": []}


def infer_support_office(region, institution, candidates, business_place=""):
    region = region or ""
    institution_support_office = support_office_from_institution(institution)
    if institution_support_office:
        return institution_support_office
    name = normalize_school_name(institution)
    district = ""
    district_match = ADDRESS_DISTRICT_PATTERN.search(business_place or "")
    if district_match:
        district = district_match.group(1)
    district_prefix = district[:-1] if district else ""
    district_rule_support_office = support_office_from_region_district(region, district)
    if district_rule_support_office:
        return district_rule_support_office
    school_region, school_district = district_from_school_name(region, institution)
    school_rule_support_office = support_office_from_region_district(school_region or region, school_district)
    if school_rule_support_office:
        return school_rule_support_office
    district_matched = []
    matched = []
    for candidate in candidates or []:
        if region and candidate.get("region", "") != region:
            continue
        district_support_office = support_office_from_region_district(candidate.get("region", ""), candidate.get("district", ""))
        if district_support_office:
            district_matched.append(district_support_office)
        if name and normalize_school_name(candidate.get("school_name", "")) != name:
            continue
        support_office = candidate.get("support_office", "")
        if support_office:
            matched.append(support_office)
    district_unique = sorted(set(district_matched))
    if len(district_unique) == 1:
        return district_unique[0]
    unique = sorted(set(matched))
    if len(unique) == 1:
        return unique[0]
    if not unique and len(candidates or []) == 1:
        only_candidate = (candidates or [{}])[0]
        if not region or only_candidate.get("region", "") == region:
            return only_candidate.get("support_office", "")
    if not unique:
        stripped_candidates = fetch_region_stripped_school_candidates(institution, region)
        stripped_matched = []
        for candidate in stripped_candidates:
            if region and candidate.get("region", "") != region:
                continue
            support_office = candidate.get("support_office", "")
            if support_office:
                stripped_matched.append(support_office)
        stripped_unique = sorted(set(stripped_matched))
        if len(stripped_unique) == 1:
            return stripped_unique[0]
    if district_prefix:
        district_candidates = fetch_district_stripped_school_candidates(institution, district_prefix)
        district_matched = []
        for candidate in district_candidates:
            if region and candidate.get("region", "") != region:
                continue
            support_office = candidate.get("support_office", "")
            if support_office:
                district_matched.append(support_office)
        district_unique = sorted(set(district_matched))
        if len(district_unique) == 1:
            return district_unique[0]
    return ""


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
    record["support_office"] = infer_support_office(record.get("region", ""), institution, record.get("region_candidates", []), record.get("business_place", ""))
    record["region"], record["support_office"] = normalize_record_region_support(record.get("region", ""), record.get("support_office", ""))
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
        old.setdefault("support_office", infer_support_office(old.get("region", ""), old.get("institution", ""), old.get("region_candidates", []), old.get("business_place", "")))
        old["region"], old["support_office"] = normalize_record_region_support(old.get("region", ""), old.get("support_office", ""))
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

    def amount_number(value):
        digits = re.sub(r"[^0-9]", "", str(value or ""))
        return int(digits or "0")


    all_keywords = []
    for keyword in KEYWORDS:
        if any(keyword in row.get("keywords", []) for row in records):
            all_keywords.append(keyword)

    rows_html = ""
    default_visible_count = len(records)
    for index, row in enumerate(records, 1):
        keywords = row.get("keywords", [])
        keywords_joined = ",".join(keywords)
        contract_date = row.get("contract_date", "")
        on_first_page = index <= 20
        display_number = str(index) if on_first_page else ""
        row_display_attr = "" if on_first_page else ' style="display:none"'
        tag_html = "".join('<span class="tag">' + esc(keyword) + "</span>" for keyword in keywords)
        contract_name_raw = row.get("contract_name", "")
        contract_name = esc(contract_name_raw)
        if row.get("link"):
            name_html = (
                '<a href="' + esc(row.get("link")) + '" target="_blank" rel="noopener" '
                'class="contract-link">' + contract_name + " &#8599;</a>"
            )
        else:
            name_html = contract_name

        record_id = esc(row.get("id") or row.get("tender_no") or str(index))
        search_text = " ".join(
            [
                row.get("contract_name", ""),
                row.get("institution", ""),
                row.get("counterpart", ""),
            ]
        )
        region, support_office = normalize_record_region_support(row.get("region", ""), row.get("support_office", ""))
        candidates = [normalize_region_candidate(candidate) for candidate in (row.get("region_candidates", []) or [])]
        support_by_region = {}
        for candidate in candidates:
            candidate_region = candidate.get("region", "")
            candidate_support = candidate.get("support_office", "")
            if candidate_region and candidate_support:
                support_by_region.setdefault(candidate_region, set()).add(candidate_support)
        support_by_region = {
            candidate_region: sorted(values)[0]
            for candidate_region, values in support_by_region.items()
            if len(values) == 1
        }
        support_json = json.dumps(support_by_region, ensure_ascii=False)
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
            '<tr data-record-id="' + record_id + '" data-keywords="' + esc(keywords_joined) + '" data-contract-name="' + esc(contract_name_raw) + '" data-search-text="' + esc(search_text) + '" data-level="' + esc(row.get("school_level", "")) + '" data-contract-date="' + esc(contract_date) + '" data-counterpart="' + esc(row.get("counterpart", "") or "미지정") + '" data-school-category="' + esc(school_category(row.get("institution", ""))) + '"' + row_display_attr + '>'
            '<td class="tc select-cell"><input type="checkbox" class="row-check" aria-label="삭제할 공고 선택"></td>'
            '<td class="tc row-no">' + display_number + '</td>'
            '<td>' + name_html + '</td>'
            '<td class="tc region-cell">' + region_html + '</td>'
            '<td class="support-cell" data-original-support="' + esc(support_office) + '" data-support-by-region="' + esc(support_json) + '">' + esc(support_office) + '</td>'
            '<td>' + esc(row.get("institution", "")) + '</td>'
            '<td>' + esc(row.get("counterpart", "")) + '</td>'
            '<td class="tr">' + esc(row.get("amount", "")) + '</td>'
            '<td class="tc">' + esc(row.get("contract_date", "")) + '</td>'
            '</tr>\n'
        )

    keyword_buttons = (
        '<button class="btn active" data-kind="keyword" data-filter-value="all" onclick="filterTable(this,\'keyword\',\'all\')">'
        '전체 <span class="cnt">' + str(len(records)) + '</span></button>\n'
    )
    for keyword in all_keywords:
        count = sum(1 for row in records if keyword in row.get("keywords", []))
        keyword_buttons += (
            '<button class="btn" data-kind="keyword" data-filter-value="' + esc(keyword) + '" onclick="filterTable(this,\'keyword\',\'' + esc(keyword) + '\')">'
            + esc(keyword) + ' <span class="cnt">' + str(count) + '</span></button>\n'
        )

    dashboard_records = []
    for row in records:
        support_by_region = {}
        for candidate in [normalize_region_candidate(candidate) for candidate in (row.get("region_candidates", []) or [])]:
            candidate_region = candidate.get("region", "")
            candidate_support = candidate.get("support_office", "")
            if candidate_region and candidate_support:
                support_by_region.setdefault(candidate_region, set()).add(candidate_support)
        support_by_region = {candidate_region: sorted(values)[0] for candidate_region, values in support_by_region.items() if len(values) == 1}
        record_region, record_support = normalize_record_region_support(row.get("region", ""), row.get("support_office", ""))
        dashboard_records.append({"id": row.get("id") or row.get("tender_no", ""), "counterpart": row.get("counterpart", "") or "미지정", "amount": amount_number(row.get("amount", "")), "contractDate": row.get("contract_date", ""), "region": record_region or "미지정", "supportOffice": record_support or "미지정", "supportByRegion": support_by_region, "schoolCategory": school_category(row.get("institution", ""))})
    dashboard_json = json.dumps(dashboard_records, ensure_ascii=False, separators=(",", ":"))

    css = """
*{box-sizing:border-box}body{margin:0;font-family:'Malgun Gothic',Arial,sans-serif;font-size:13px;color:#2f343b;background:#f4f6f8}.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}.header{background:#245a92;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:16px}.header h1{font-size:19px;margin:0 0 7px}.meta{font-size:12px;opacity:.88}.panel,.toolbar{background:#fff;border:1px solid #dce4ec;border-radius:8px;margin-bottom:14px}.panel{padding:14px 16px}.panel h2{font-size:12px;color:#69727d;margin:0 0 10px}.filters,.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.btn{border:1px solid #2f6fa8;color:#245a92;background:#fff;border-radius:18px;padding:6px 12px;font-size:12px;cursor:pointer;font-family:inherit}.btn:hover{background:#eef5fb}.btn.active{background:#245a92;color:#fff}.cnt{background:rgba(36,90,146,.1);border-radius:10px;padding:1px 6px;margin-left:3px}.btn.active .cnt{background:rgba(255,255,255,.25)}.summary{display:flex;justify-content:space-between;gap:12px;align-items:center;background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:12px 16px;margin-bottom:14px}.summary strong{font-size:17px;color:#c0392b}.s2b-link{color:#245a92;text-decoration:none}.toolbar{padding:10px 12px}.toolbar-spacer{flex:1}.action-btn{height:30px;border:1px solid #245a92;border-radius:6px;background:#245a92;color:#fff;font-family:inherit;font-size:12px;padding:0 10px;cursor:pointer}.action-btn.danger{border-color:#b33a3a;background:#b33a3a}.action-btn.secondary{background:#fff;color:#245a92}.action-btn.filter-toggle{position:relative;border-color:#245a92;background:#fff;color:#245a92}.action-btn.filter-toggle.active{background:#245a92;color:#fff;box-shadow:0 0 0 3px rgba(36,90,146,.14)}.action-btn.filter-toggle.active::before{content:'ON';font-weight:700;margin-right:5px}.filter-state{display:inline-flex;align-items:center;height:30px;border:1px solid #d4e2ef;border-radius:15px;background:#f7fbff;color:#5c6670;padding:0 10px;font-size:12px}.filter-state.active{border-color:#245a92;background:#e8f1fa;color:#245a92;font-weight:600}.action-btn:hover{filter:brightness(.95)}.date-tools{gap:10px}.date-toggle{display:inline-flex;align-items:center;gap:5px;height:30px;border:1px solid #bfd0df;border-radius:16px;padding:0 10px;background:#fff;color:#263442;font-size:12px}.date-toggle input{margin:0}.date-input{height:30px;border:1px solid #b9c7d6;border-radius:6px;padding:0 8px;font-family:inherit;font-size:12px}.search-input{height:30px;min-width:220px;border:1px solid #b9c7d6;border-radius:6px;padding:0 9px;font-family:inherit;font-size:12px}.range-note{font-size:12px;color:#5c6670}.sync-status{font-size:12px;color:#5c6670}.sync-status.ok{color:#1d7a38}.sync-status.error{color:#b33a3a}.table-wrap{background:#fff;border:1px solid #e2e6ea;border-radius:8px;overflow:auto;max-height:calc(100vh - 260px);min-height:260px}table{width:100%;border-collapse:collapse;min-width:1120px}thead tr{background:#245a92;color:#fff}th{position:sticky;top:0;z-index:2;background:#245a92;padding:11px 9px;font-size:12px;font-weight:600;white-space:nowrap}td{padding:10px 9px;border-bottom:1px solid #edf0f2;vertical-align:middle}tbody tr{content-visibility:auto;contain-intrinsic-size:52px}tbody tr:hover td{background:#f8fbff}.tc{text-align:center}.tr{text-align:right}.select-cell{width:36px}.row-no{color:#89939e;width:42px}.contract-link{color:#1769aa;text-decoration:none}.contract-link:hover{text-decoration:underline}.tags{margin-top:5px}.tag{display:inline-block;background:#e8f1fa;color:#245a92;border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px 0 0}.region-cell{min-width:210px}.region-editor{display:flex;gap:6px;justify-content:center;align-items:center}.region-select{height:28px;max-width:160px;border:1px solid #b9c7d6;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:12px;padding:0 6px}.region-save{height:28px;border:1px solid #245a92;border-radius:6px;background:#245a92;color:#fff;font-family:inherit;font-size:12px;padding:0 8px;cursor:pointer}.region-save:disabled,.action-btn:disabled{opacity:.65;cursor:wait}.region-text{font-weight:600;color:#263442}td:nth-child(8){font-weight:600;white-space:nowrap}td:nth-child(9){font-size:12px;color:#5c6670;white-space:nowrap}.no-result{text-align:center;padding:54px 20px;color:#8a94a0;display:none}.pagination{display:none;flex-wrap:wrap;gap:6px;align-items:center;justify-content:center;background:#fff;border:1px solid #e2e6ea;border-radius:8px;margin-top:10px;padding:10px}.pagination-top{position:sticky;top:0;z-index:5;margin-top:0;margin-bottom:10px;box-shadow:0 2px 8px rgba(36,90,146,.12)}.pagination-bottom{margin-top:10px}.page-btn{min-width:30px;height:30px;border:1px solid #bfd0df;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:12px;cursor:pointer}.page-btn:hover{background:#eef5fb}.page-btn.active{border-color:#245a92;background:#245a92;color:#fff}.page-btn:disabled{opacity:.45;cursor:not-allowed}.page-info{font-size:12px;color:#5c6670;margin-left:4px}.footer{text-align:center;color:#9aa3ad;font-size:11px;margin-top:18px}@media(max-width:720px){.wrap{padding:12px 8px}.header{padding:16px}.summary,.toolbar{align-items:flex-start;flex-direction:column}.panel{padding:12px}.table-wrap{max-height:calc(100vh - 220px)}.btn{padding:6px 10px}.region-editor{flex-direction:column}.region-select,.region-save{width:100%;max-width:none}}
""".strip()
    css += """
.nav-tabs{display:flex;gap:8px;margin:0 0 14px}.tab-btn{height:34px;border:1px solid #bfd0df;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:13px;padding:0 14px;cursor:pointer}.tab-btn.active{border-color:#245a92;background:#245a92;color:#fff}.view{display:none}.view.active{display:block}.dashboard-grid{display:grid;grid-template-columns:1.05fr .95fr;gap:14px}.metric-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:14px}.metric{background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:14px 16px}.metric-label{font-size:12px;color:#69727d;margin-bottom:6px}.metric-value{font-size:22px;font-weight:700;color:#263442}.dash-panel{background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:14px 16px;min-width:0}.dash-panel h2{font-size:13px;color:#263442;margin:0 0 12px}.dash-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}.dash-head h2{margin:0}.dash-select{height:30px;border:1px solid #b9c7d6;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:12px;padding:0 8px}.bar-list{display:flex;flex-direction:column;gap:8px}.bar-row{display:grid;grid-template-columns:minmax(88px,160px) 1fr minmax(90px,auto);gap:10px;align-items:center;width:100%;border:0;background:transparent;padding:0;font-family:inherit;text-align:left}.bar-row-button{cursor:pointer}.bar-row-button:hover .bar-label{color:#1769aa;text-decoration:underline}.bar-row-button:focus-visible{outline:2px solid #245a92;outline-offset:2px;border-radius:4px}.bar-label{font-size:12px;color:#263442;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-track{height:10px;border-radius:999px;background:#e8eef4;overflow:hidden}.bar-fill{height:100%;border-radius:999px;background:#2f6fa8}.bar-value{font-size:12px;color:#263442;text-align:right;font-weight:600;white-space:nowrap}.dashboard-filter-note{display:none;align-items:center;justify-content:space-between;gap:10px;background:#fff;border:1px solid #dce4ec;border-radius:8px;margin-bottom:14px;padding:10px 12px;color:#263442}.dashboard-filter-note strong{color:#245a92}.empty-chart{padding:28px 8px;color:#8a94a0;text-align:center}.detail-view .panel{margin-top:0}@media(max-width:900px){.dashboard-grid,.metric-row{grid-template-columns:1fr}.bar-row{grid-template-columns:1fr}.bar-value{text-align:left}.dashboard-filter-note{align-items:flex-start;flex-direction:column}}
""".strip()
    css += """
.purchase-panel{background:#fff;border:1px solid #dce4ec;border-radius:8px;margin-bottom:14px;padding:14px 16px}.purchase-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.purchase-head h2{font-size:13px;color:#263442;margin:0}.purchase-tools{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.purchase-scope{height:28px;border:1px solid #bfd0df;border-radius:14px;background:#fff;color:#263442;font-family:inherit;font-size:12px;padding:0 10px;cursor:pointer}.purchase-scope.active{border-color:#245a92;background:#245a92;color:#fff}.purchase-table-wrap{overflow:auto}.purchase-table{width:100%;min-width:760px;border-collapse:collapse}.purchase-table th{position:static;background:#f0f4f8;color:#263442}.purchase-table td,.purchase-table th{padding:8px 9px;border-bottom:1px solid #edf0f2;font-size:12px}.purchase-type-row{cursor:pointer}.purchase-type-row:hover td{background:#f8fbff}.purchase-type-row.active td{background:#e8f1fa;color:#245a92;font-weight:600}.purchase-note{font-size:12px;color:#69727d;margin:8px 0 0}.purchase-subgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.purchase-subpanel{border:1px solid #edf0f2;border-radius:8px;overflow:hidden}.purchase-subpanel h3{font-size:12px;margin:0;padding:9px 10px;background:#f7fbff;color:#263442}.purchase-subpanel .purchase-table{min-width:520px}.purchase-csv{height:28px;border:1px solid #245a92;border-radius:6px;background:#fff;color:#245a92;font-family:inherit;font-size:12px;padding:0 9px;cursor:pointer}@media(max-width:900px){.purchase-head{align-items:flex-start;flex-direction:column}.purchase-subgrid{grid-template-columns:1fr}.purchase-table{min-width:680px}}
""".strip()

    js = """
var activeKeyword='all';
var contractSearchText='';
var unsavedOnly=false;
var dateMode='all';
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
var regionCache=null;
var deletedCache=null;
var currentPage=1;
var pageSize=20;
var dashboardRecords=__DASHBOARD_RECORDS__;
var dashboardRecordMap=null;
var activeDashboardRegion='';
var dashboardDetailFilter=null;
var activePurchaseType='';
var purchaseVendorScope='all';
var lastPurchaseStats=null;
const PURCHASE_TYPES=[
{key:'aidt',label:'AI디지털 교육자료 (AIDT)',keywords:['AIDT','디지털 교육자료','디지털교육자료','AI디지털교과서','AI 디지털 교과서','발행사 AIDT']},
{key:'assessment',label:'평가도구',keywords:['지니아튜터','매쓰홀릭','매쓰홀릭T','스쿨플랫','매쓰플랫','수학대왕','기출탭탭','일프로연산','문제은행','진단평가','학력진단','단원평가','리드 인공지능 문해력 진단']},
{key:'courseware',label:'코스웨어',keywords:['초코팝','초코클래스','달달','옥수수','홈런','아이스크림홈런','밀크티','스마트올','엘리하이','자작자작','클래스팅','클래스팅 AI','토도수학','알공 수학','똑똑수학탐험대','토도한글','러니','레서','매일국어T','리드','토도영어','알공 영어','쿠키영어','리딩앤','리딩게이트','리딩오션','원아워','영어독후활동프로그램','코드모스','엘리스스쿨','엘리스 LXP','플랭','플랭스쿨','스쿨런','두드림','AI 코스웨어','코스웨어']},
{key:'tool',label:'수업도구',keywords:['제미나이','Gemini','클로드','Claude','GPT','지피티','챗지피티','유튜브','YouTube','캔바','Canva','미리캔버스','젭','ZEP','카훗','Kahoot','북크리에이터','패들렛','Padlet','감마','Gamma','수노','Suno','뤼튼','노션','Notion','띵커벨','퀴즈앤','클래스카드','퀴즈렛','다했니','슬라이도','망고보드','투닝','스픽','Speak','일레븐랩스','ElevenLabs','런웨이','Runway','미드저니','Midjourney']},
{key:'hardware',label:'하드웨어·디바이스',keywords:['태블릿','노트북','전자칠판','충전함','실물화상기','크롬북','아이패드','거치대']},
{key:'material',label:'교구·실물자료',keywords:['레고','LEGO','스파이크','포디랜드','4D프레임','고피쉬','카드게임','보드게임','키트','모형','드론','로봇','메이커','3D펜','VR','AR']},
{key:'book',label:'교재·도서',keywords:['만점왕','EBS','워크북','문제집','받아쓰기','일일수학','기능중심수학','한걸음 수학','학습지','교과서 외','권세트','4권세트','느린학습자용 교재 세트','초등 쓰기 워크북']},
{key:'service',label:'연수·컨설팅·용역',keywords:['연수','강사','컨설팅','직무연수','교원연수','운영용역']},
{key:'unknownProduct',label:'제품 식별 불가',keywords:['AI 디지털 활용 선도학교 운영 물품 구입','AI디지털활용선도학교 운영 물품 구입','에듀테크 콘텐츠 구독료','코스웨어 구입','코스웨어구입','코스웨어 구매','에듀테크 구입','에듀테크 구매','프로그램 구독료']},
{key:'uncategorized',label:'미분류',keywords:[]}
];
var REGION_SUPPORT_TOKENS={서울:['서울','서울특별시'],부산:['부산','부산광역시'],대구:['대구','대구광역시'],인천:['인천','인천광역시'],광주:['광주','광주광역시'],대전:['대전','대전광역시'],울산:['울산','울산광역시'],세종:['세종','세종특별자치시'],경기:['경기','경기도'],강원:['강원','강원특별자치도'],충북:['충북','충청북도'],충남:['충남','충청남도'],전북:['전북','전라북도','전북특별자치도'],전남:['전남','전라남도'],경북:['경북','경상북도'],경남:['경남','경상남도'],제주:['제주','제주특별자치도']};
function supportMatchesRegion(region,support){if(!region||!support||support==='\uBBF8\uC9C0\uC815'){return true;}var tokens=REGION_SUPPORT_TOKENS[region]||[region];return tokens.some(function(token){return support.indexOf(token)!==-1;});}
function readJsonStorage(key,fallback){try{return JSON.parse(localStorage.getItem(key)||JSON.stringify(fallback));}catch(e){return fallback;}}
function writeJsonStorage(key,value){localStorage.setItem(key,JSON.stringify(value));}
function getRegionOverrides(){if(regionCache===null){regionCache=readJsonStorage(regionStorageKey,{});}return regionCache;}
function setRegionOverrides(value){regionCache=value||{};writeJsonStorage(regionStorageKey,regionCache);}
function getDeletedRecords(){if(deletedCache===null){deletedCache=readJsonStorage(deletedStorageKey,[]);}return deletedCache;}
function setDeletedRecords(value){deletedCache=value||[];writeJsonStorage(deletedStorageKey,deletedCache);}
function setStatus(message,type){var el=document.getElementById('sync-status');if(!el){return;}el.textContent=message||'';el.className='sync-status '+(type||'');}
function cleanSupabaseUrl(url){return (url||'').trim().replace(/\\/$/,'');}
function getSupabaseConfig(){return {url:cleanSupabaseUrl(localStorage.getItem(supabaseUrlKey)||supabaseDefaultUrl||''),key:localStorage.getItem(supabaseAnonKey)||supabaseDefaultAnonKey||''};}
function supabaseHeaders(){var cfg=getSupabaseConfig();return {'apikey':cfg.key,'Authorization':'Bearer '+cfg.key,'Content-Type':'application/json'};}
async function supabaseFetch(path,options){var cfg=getSupabaseConfig();if(!cfg.url||!cfg.key){throw new Error('Supabase config is required.');}var response=await fetch(cfg.url+path,Object.assign({headers:supabaseHeaders()},options||{}));if(!response.ok){var text='';try{text=await response.text();}catch(e){}throw new Error('Supabase request failed: '+response.status+(text?' '+text.slice(0,120):''));}return response;}
async function loadSupabaseRegions(){var response=await supabaseFetch('/rest/v1/region_overrides?select=record_id,region&limit=10000',{method:'GET'});var rows=await response.json();var map={};rows.forEach(function(row){if(row.record_id&&row.region){map[row.record_id]=row.region;}});return map;}
async function loadSupabaseDeleted(){var response=await supabaseFetch('/rest/v1/deleted_records?select=record_id&limit=10000',{method:'GET'});var rows=await response.json();return rows.map(function(row){return row.record_id;}).filter(Boolean);}
async function upsertSupabaseRegion(id,region){await supabaseFetch('/rest/v1/region_overrides?on_conflict=record_id',{method:'POST',headers:Object.assign(supabaseHeaders(),{'Prefer':'resolution=merge-duplicates,return=minimal'}),body:JSON.stringify({record_id:id,region:region})});}
async function upsertSupabaseDeleted(ids){var rows=ids.map(function(id){return {record_id:id};});await supabaseFetch('/rest/v1/deleted_records?on_conflict=record_id',{method:'POST',headers:Object.assign(supabaseHeaders(),{'Prefer':'resolution=merge-duplicates,return=minimal'}),body:JSON.stringify(rows)});}
function rowHasRegion(row){if(row.getAttribute('data-has-region')==='1'){return true;}var id=row.getAttribute('data-record-id');var regions=getRegionOverrides();var fixed=row.querySelector('.fixed-region');var has=!!(fixed||(id&&regions[id]));if(has){row.setAttribute('data-has-region','1');}return has;}
function rowMatchesDate(row){var date=row.getAttribute('data-contract-date')||'';if(dateMode==='all'){return true;}if(dateMode==='search'){return !!date&&!!selectedDateFrom&&!!selectedDateTo&&date>=selectedDateFrom&&date<=selectedDateTo;}return !!date&&date>=defaultDateFrom&&date<=defaultDateTo;}
function normalizeSearchText(value){return (value||'').toLowerCase().replace(/\\s+/g,'');}
function setContractSearch(value){contractSearchText=normalizeSearchText(value);filterCurrent();}
function clearContractSearch(){contractSearchText='';var el=document.getElementById('contract-search');if(el){el.value='';}filterCurrent();}
function updateSupportOffice(row,region){if(!row){return;}var cell=row.querySelector('.support-cell');if(!cell){return;}var map={};try{map=JSON.parse(cell.getAttribute('data-support-by-region')||'{}');}catch(e){map={};}var original=cell.getAttribute('data-original-support')||'';cell.textContent=(region&&map[region])?map[region]:original;}
function renderSavedRegion(editor,value){var span=document.createElement('span');span.className='region-text saved-region';span.setAttribute('data-region-id',editor.getAttribute('data-region-id')||'');span.textContent=value;editor.replaceWith(span);var row=span.closest('tr');if(row){row.setAttribute('data-has-region','1');updateSupportOffice(row,value);}}
function applyRegions(regions){document.querySelectorAll('.region-editor').forEach(function(editor){var id=editor.getAttribute('data-region-id');var value=id?regions[id]:'';if(value){renderSavedRegion(editor,value);}});}
function updateKeywordCounts(){var rows=Array.from(document.querySelectorAll('#tbody tr')).filter(function(row){return row.getAttribute('data-deleted')!=='1';});document.querySelectorAll('[data-kind="keyword"]').forEach(function(btn){var value=btn.getAttribute('data-filter-value')||'all';var count=value==='all'?rows.length:rows.filter(function(row){var keywords=row.getAttribute('data-keywords')||'';return (','+keywords+',').indexOf(','+value+',')!==-1;}).length;var cnt=btn.querySelector('.cnt');if(cnt){cnt.textContent=count.toLocaleString('ko-KR');}});}
function applyDeleted(deleted){var deletedSet=new Set(deleted||[]);document.querySelectorAll('#tbody tr').forEach(function(row){row.setAttribute('data-deleted',deletedSet.has(row.getAttribute('data-record-id'))?'1':'0');});updateKeywordCounts();filterCurrent();renderDashboard();}
async function loadRemoteState(){try{var localRegions=getRegionOverrides();var localDeleted=getDeletedRecords();var regionRemote=await loadSupabaseRegions();var deletedRemote=await loadSupabaseDeleted();var mergedRegions=Object.assign({},regionRemote,localRegions);var mergedDeleted=Array.from(new Set((deletedRemote||[]).concat(localDeleted||[]))).sort();setRegionOverrides(mergedRegions);setDeletedRecords(mergedDeleted);applyRegions(mergedRegions);applyDeleted(mergedDeleted);setStatus('Supabase data loaded.','ok');}catch(error){applyRegions(getRegionOverrides());applyDeleted(getDeletedRecords());setStatus('Supabase 연결 실패. 로컬 저장 모드로 동작합니다.','error');}}
async function saveRegion(button){var editor=button.closest('.region-editor');if(!editor){return;}var select=editor.querySelector('.region-select');var value=select?select.value:'';var id=editor.getAttribute('data-region-id');if(!value||!id){setStatus('지역을 선택하세요.','error');return;}button.disabled=true;button.textContent='저장 중';var regions=getRegionOverrides();regions[id]=value;setRegionOverrides(regions);var row=editor.closest('tr');renderSavedRegion(editor,value);if(row){row.setAttribute('data-has-region','1');}if(unsavedOnly){filterCurrent();}setStatus('지역을 로컬에 저장했습니다. Supabase 동기화 중...');try{await upsertSupabaseRegion(id,value);setStatus('지역 저장 완료.','ok');}catch(error){setStatus('지역은 로컬에 저장됨. Supabase 동기화 실패.','error');}}
function selectedIds(){return Array.from(document.querySelectorAll('#tbody tr')).filter(function(row){return row.style.display!=='none'&&row.querySelector('.row-check')&&row.querySelector('.row-check').checked;}).map(function(row){return row.getAttribute('data-record-id');});}
async function deleteSelected(){var ids=selectedIds();if(!ids.length){setStatus('삭제할 항목을 선택하세요.','error');return;}if(!confirm(ids.length+'개 항목을 목록에서 삭제할까요?')){return;}var btn=document.getElementById('delete-selected');btn.disabled=true;var merged=Array.from(new Set(getDeletedRecords().concat(ids))).sort();setDeletedRecords(merged);applyDeleted(merged);setStatus('선택 항목을 로컬에서 삭제했습니다. Supabase 동기화 중...');try{await upsertSupabaseDeleted(ids);setStatus('선택 항목 삭제 완료.','ok');}catch(error){setStatus('삭제는 로컬에 저장됨. Supabase 동기화 실패.','error');}finally{btn.disabled=false;}}
async function syncLocalToSupabase(button){var regions=getRegionOverrides();var deleted=getDeletedRecords();var ids=Object.keys(regions).filter(function(id){return regions[id];});if(!ids.length&&!deleted.length){setStatus('동기화할 로컬 저장값이 없습니다.','ok');return;}if(button){button.disabled=true;}setStatus('로컬 저장값을 Supabase로 동기화 중...');try{for(var i=0;i<ids.length;i++){await upsertSupabaseRegion(ids[i],regions[ids[i]]);}if(deleted.length){await upsertSupabaseDeleted(deleted);}setStatus('로컬 저장값 Supabase 동기화 완료.','ok');}catch(error){setStatus('Supabase 동기화 실패: '+(error.message||'Failed to fetch'),'error');}finally{if(button){button.disabled=false;}}}
function showView(name){document.querySelectorAll('.view').forEach(function(el){el.classList.toggle('active',el.getAttribute('data-view')===name);});document.querySelectorAll('.tab-btn').forEach(function(btn){btn.classList.toggle('active',btn.getAttribute('data-target')===name);});if(name==='dashboard'){renderDashboard();}else{filterCurrent(true);}}
function formatWon(value){return (Number(value)||0).toLocaleString('ko-KR')+'\uC6D0';}
function formatWonDetail(value){var amount=Number(value)||0;var text=amount.toLocaleString('ko-KR')+'\uC6D0';if(Math.abs(amount)>=100000000){text+=' ('+(amount/100000000).toFixed(1)+'\uC5B5)';}return text;}
function addAmount(map,key,amount){key=key||'\uBBF8\uC9C0\uC815';map[key]=(map[key]||0)+(Number(amount)||0);}
function sortedEntries(map){return Object.keys(map).map(function(key){return {name:key,value:map[key]};}).sort(function(a,b){return b.value-a.value||a.name.localeCompare(b.name,'ko-KR');});}
function escapeHtml(value){return String(value||'').replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function normalizedIncludes(text,keyword){return normalizeSearchText(text).indexOf(normalizeSearchText(keyword))!==-1;}
function purchaseTypeByKey(key){for(var i=0;i<PURCHASE_TYPES.length;i++){if(PURCHASE_TYPES[i].key===key){return PURCHASE_TYPES[i];}}return PURCHASE_TYPES[PURCHASE_TYPES.length-1];}
function isComplexContract(name){return /외\\s*\\d+\\s*종/.test(name||'');}
function amountFromRow(row){var cell=row.querySelector('td.tr');var digits=(cell?cell.textContent:'').replace(/[^0-9]/g,'');return Number(digits||0);}
function vendorMatchesScope(row){var vendor=row.getAttribute('data-counterpart')||'';var has=normalizeSearchText(vendor).indexOf(normalizeSearchText('미래엔'))!==-1;if(purchaseVendorScope==='mirae'){return has;}if(purchaseVendorScope==='other'){return !has;}return true;}
function classifyPurchaseType(name){var text=name||'';var normalized=normalizeSearchText(text);var coursewareGeneric=false;var concreteMatch=false;for(var i=0;i<PURCHASE_TYPES.length;i++){var type=PURCHASE_TYPES[i];if(type.key==='unknownProduct'||type.key==='uncategorized'){continue;}for(var j=0;j<type.keywords.length;j++){var keyword=type.keywords[j];if(normalized.indexOf(normalizeSearchText(keyword))!==-1){if(type.key==='courseware'&&(keyword==='코스웨어'||keyword==='AI 코스웨어')){coursewareGeneric=true;}else{concreteMatch=true;}if(type.key!=='courseware'||keyword!=='코스웨어'){return type;}}}}var unknown=purchaseTypeByKey('unknownProduct');for(var u=0;u<unknown.keywords.length;u++){if(normalized.indexOf(normalizeSearchText(unknown.keywords[u]))!==-1&&!concreteMatch){return unknown;}}if(coursewareGeneric){return purchaseTypeByKey('courseware');}return purchaseTypeByKey('uncategorized');}
function createPurchaseStats(){var byType={};PURCHASE_TYPES.forEach(function(type){byType[type.key]={key:type.key,label:type.label,count:0,amount:0,complex:0,levels:{},months:{}};});return {byType:byType,totalCount:0,totalAmount:0,complexCount:0};}
function addPurchaseStats(stats,row,type,amount){var item=stats.byType[type.key]||stats.byType.uncategorized;var level=row.getAttribute('data-level')||'기타';var month=(row.getAttribute('data-contract-date')||'').slice(0,7)||'미지정';var complex=isComplexContract(row.getAttribute('data-contract-name')||'');item.count+=1;item.amount+=amount;item.levels[level]=(item.levels[level]||0)+amount;item.months[month]=(item.months[month]||0)+amount;stats.totalCount+=1;stats.totalAmount+=amount;if(complex){item.complex+=1;stats.complexCount+=1;}}
function purchaseStatsItems(stats){return PURCHASE_TYPES.map(function(type){return stats.byType[type.key];}).filter(function(item){return item.count>0||item.key==='uncategorized';}).sort(function(a,b){if(a.key==='total'){return 1;}return b.amount-a.amount||b.count-a.count||a.label.localeCompare(b.label,'ko-KR');});}
function setPurchaseTypeFilter(key){activePurchaseType=activePurchaseType===key?'':key;if(activePurchaseType){showView('details');}else{filterCurrent();}}
function setPurchaseVendorScope(scope){purchaseVendorScope=scope||'all';filterCurrent();}
function renderPurchaseTypeBars(stats){var el=document.getElementById('purchase-type-chart');if(!el){return;}var rows=purchaseStatsItems(stats).filter(function(item){return item.count>0;});if(!rows.length){el.innerHTML='<div class="empty-chart">표시할 매출액이 없습니다.</div>';return;}var max=Math.max.apply(null,rows.map(function(item){return item.amount;}))||1;el.innerHTML=rows.map(function(item){var pct=Math.max(2,Math.round(item.amount/max*100));var label=escapeHtml(item.label);var active=activePurchaseType===item.key?' active':'';return '<button type="button" class="bar-row bar-row-button'+active+'" onclick="setPurchaseTypeFilter(\\''+item.key+'\\')"><div class="bar-label" title="'+label+'">'+label+'</div><div class="bar-track"><div class="bar-fill" style="width:'+pct+'%"></div></div><div class="bar-value">'+formatWon(item.amount)+'</div></button>';}).join('');}
function renderPurchaseStats(stats){lastPurchaseStats=stats;var body=document.getElementById('purchase-type-body');var foot=document.getElementById('purchase-type-foot');var note=document.getElementById('purchase-complex-note');renderPurchaseTypeBars(stats);if(!body||!foot){return;}var rows=purchaseStatsItems(stats);body.innerHTML=rows.map(function(item){var pct=stats.totalAmount?((item.amount/stats.totalAmount)*100).toFixed(1):'0.0';var avg=item.count?Math.round(item.amount/item.count):0;var active=activePurchaseType===item.key?' active':'';return '<tr class="purchase-type-row'+active+'" onclick="setPurchaseTypeFilter(\\''+item.key+'\\')"><td>'+escapeHtml(item.label)+'</td><td class="tr">'+item.count.toLocaleString('ko-KR')+'</td><td class="tr">'+formatWonDetail(item.amount)+'</td><td class="tr">'+pct+'%</td><td class="tr">'+formatWonDetail(avg)+'</td></tr>';}).join('');var avgTotal=stats.totalCount?Math.round(stats.totalAmount/stats.totalCount):0;foot.innerHTML='<tr><th>합계</th><th class="tr">'+stats.totalCount.toLocaleString('ko-KR')+'</th><th class="tr">'+formatWonDetail(stats.totalAmount)+'</th><th class="tr">100.0%</th><th class="tr">'+formatWonDetail(avgTotal)+'</th></tr>';if(note){note.textContent='복합계약 '+stats.complexCount.toLocaleString('ko-KR')+'건 포함 (단일 제품 기준 아님)';}renderPurchaseCrossTables(stats);updatePurchaseScopeButtons();}
function renderPurchaseCrossTables(stats){var levelEl=document.getElementById('purchase-level-body');var monthEl=document.getElementById('purchase-month-body');if(levelEl){var levels=['초','중','고','기타'];levelEl.innerHTML=purchaseStatsItems(stats).filter(function(item){return item.count>0;}).map(function(item){return '<tr><td>'+escapeHtml(item.label)+'</td>'+levels.map(function(level){return '<td class="tr">'+formatWonDetail(item.levels[level]||0)+'</td>';}).join('')+'</tr>';}).join('');}if(monthEl){var months={};PURCHASE_TYPES.forEach(function(type){var item=stats.byType[type.key];Object.keys(item.months).forEach(function(month){months[month]=true;});});var monthList=Object.keys(months).sort();monthEl.innerHTML=monthList.map(function(month){var cells=purchaseStatsItems(stats).filter(function(item){return item.count>0;}).slice(0,6).map(function(item){return '<td class="tr">'+formatWonDetail(item.months[month]||0)+'</td>';}).join('');return '<tr><td>'+escapeHtml(month)+'</td>'+cells+'</tr>';}).join('');var head=document.getElementById('purchase-month-head');if(head){head.innerHTML='<tr><th>월</th>'+purchaseStatsItems(stats).filter(function(item){return item.count>0;}).slice(0,6).map(function(item){return '<th>'+escapeHtml(item.label)+'</th>';}).join('')+'</tr>';}}}
function updatePurchaseScopeButtons(){document.querySelectorAll('[data-purchase-scope]').forEach(function(btn){btn.classList.toggle('active',(btn.getAttribute('data-purchase-scope')||'all')===purchaseVendorScope);});}
function downloadPurchaseCsv(){if(!lastPurchaseStats){return;}var lines=['유형,건수,계약금액,비중(%),건당 평균,복합계약'];purchaseStatsItems(lastPurchaseStats).forEach(function(item){var pct=lastPurchaseStats.totalAmount?((item.amount/lastPurchaseStats.totalAmount)*100).toFixed(1):'0.0';var avg=item.count?Math.round(item.amount/item.count):0;lines.push([item.label,item.count,item.amount,pct,avg,item.complex].map(function(value){return '"'+String(value).replace(/"/g,'""')+'"';}).join(','));});var blob=new Blob(['\ufeff'+lines.join('\\n')],{type:'text/csv;charset=utf-8;'});var url=URL.createObjectURL(blob);var a=document.createElement('a');a.href=url;a.download='s2b_purchase_type_summary.csv';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);}
function renderBars(id,items,limit,filterType){var el=document.getElementById(id);if(!el){return;}var shown=(items||[]).slice(0,limit||12);if(!shown.length){el.innerHTML='<div class="empty-chart">\uD45C\uC2DC\uD560 \uB9E4\uCD9C\uC561\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.</div>';return;}var max=Math.max.apply(null,shown.map(function(item){return item.value;}))||1;el.innerHTML=shown.map(function(item){var pct=Math.max(2,Math.round(item.value/max*100));var label=escapeHtml(item.name);var click=filterType?' onclick="applyDashboardFilter('+escapeHtml(JSON.stringify(filterType))+','+escapeHtml(JSON.stringify(item.name))+','+escapeHtml(JSON.stringify(item.region||''))+')"':'';return '<button type="button" class="bar-row '+(filterType?'bar-row-button':'')+'"'+click+'><div class="bar-label" title="'+label+'">'+label+'</div><div class="bar-track"><div class="bar-fill" style="width:'+pct+'%"></div></div><div class="bar-value">'+formatWon(item.value)+'</div></button>';}).join('');}
function dashboardRows(){var regions=getRegionOverrides();var deleted=new Set(getDeletedRecords()||[]);return dashboardRecords.filter(function(row){return !deleted.has(row.id);}).map(function(row){var hasOverride=Object.prototype.hasOwnProperty.call(regions,row.id);var region=hasOverride?regions[row.id]:(row.region||'\uBBF8\uC9C0\uC815');var original=row.supportOffice||'';var mapped=(region&&row.supportByRegion&&row.supportByRegion[region])?row.supportByRegion[region]:'';var support=(hasOverride&&mapped)?mapped:((original&&original!=='\uBBF8\uC9C0\uC815')?original:(mapped||'\uBBF8\uC9C0\uC815'));if(!supportMatchesRegion(region,support)){support=mapped||'\uBBF8\uC9C0\uC815';}return Object.assign({},row,{region:region,supportOffice:support});});}
function dashboardRecordForRow(row){if(dashboardRecordMap===null){dashboardRecordMap={};dashboardRecords.forEach(function(record){dashboardRecordMap[record.id]=record;});}return dashboardRecordMap[row.getAttribute('data-record-id')]||null;}
function renderRegionOptions(items){var select=document.getElementById('dashboard-region');if(!select){return;}var current=activeDashboardRegion;var regions=items.map(function(item){return item.name;}).filter(function(name){return name&&name!=='\uBBF8\uC9C0\uC815';});select.innerHTML='<option value="">\uC2DC\uB3C4 \uC120\uD0DD</option>'+regions.map(function(name){return '<option value="'+name+'">'+name+'</option>';}).join('');if(current&&regions.indexOf(current)!==-1){select.value=current;}else{activeDashboardRegion=regions[0]||'';select.value=activeDashboardRegion;}}
function setDashboardRegion(value){activeDashboardRegion=value;renderDashboard(false);}
function renderDashboard(updateRegionSelect){if(updateRegionSelect!==false){updateRegionSelect=true;}var rows=dashboardRows();var total=rows.reduce(function(sum,row){return sum+(Number(row.amount)||0);},0);var vendorMap={};var monthMap={};var regionMap={};var supportMap={};var schoolMap={};var schoolOrder=['유치원','초등학교','중학교','고등학교','기타학교','기타기관'];rows.forEach(function(row){addAmount(vendorMap,row.counterpart,row.amount);addAmount(monthMap,(row.contractDate||'').slice(0,7)||'\uBBF8\uC9C0\uC815',row.amount);addAmount(regionMap,row.region,row.amount);addAmount(schoolMap,row.schoolCategory||'기타기관',row.amount);});var vendorItems=sortedEntries(vendorMap);var monthItems=sortedEntries(monthMap).sort(function(a,b){return a.name.localeCompare(b.name);});var regionItems=sortedEntries(regionMap);var schoolItems=schoolOrder.map(function(name){return {name:name,value:schoolMap[name]||0};}).filter(function(item){return item.value>0;});if(updateRegionSelect){renderRegionOptions(regionItems);}rows.forEach(function(row){if(activeDashboardRegion&&row.region===activeDashboardRegion){addAmount(supportMap,row.supportOffice,row.amount);}});var supportItems=sortedEntries(supportMap).map(function(item){item.region=activeDashboardRegion;return item;});var totalEl=document.getElementById('dash-total');if(totalEl){totalEl.textContent=formatWon(total);}var countEl=document.getElementById('dash-count');if(countEl){countEl.textContent=rows.length.toLocaleString('ko-KR')+'\uAC74';}var topEl=document.getElementById('dash-top-vendor');if(topEl){topEl.textContent=vendorItems.length?vendorItems[0].name:'-';}var supportTitle=document.getElementById('support-title');if(supportTitle){supportTitle.textContent=(activeDashboardRegion||'\uC2DC\uB3C4')+' \uAD50\uC721\uC9C0\uC6D0\uCCAD\uBCC4 \uB9E4\uCD9C\uC561';}renderBars('vendor-chart',vendorItems,12,'vendor');renderBars('month-chart',monthItems,18,'month');renderBars('region-chart',regionItems,17,'region');renderBars('support-chart',supportItems,14,'support');renderBars('school-chart',schoolItems,6,'school');}
function rowRegionValue(row){var id=row.getAttribute('data-record-id');var regions=getRegionOverrides();if(id&&regions[id]){return regions[id];}var record=dashboardRecordForRow(row);if(record&&record.region){return record.region;}var fixed=row.querySelector('.fixed-region');if(fixed&&fixed.textContent.trim()){return fixed.textContent.trim();}var select=row.querySelector('.region-select');return select&&select.value?select.value:'\uBBF8\uC9C0\uC815';}
function rowSupportValue(row){var id=row.getAttribute('data-record-id');var regions=getRegionOverrides();var hasOverride=id&&Object.prototype.hasOwnProperty.call(regions,id);var region=rowRegionValue(row);var record=dashboardRecordForRow(row);if(record){var mapped=(region&&record.supportByRegion&&record.supportByRegion[region])?record.supportByRegion[region]:'';if(hasOverride&&mapped){return mapped;}if(record.supportOffice&&record.supportOffice!=='\uBBF8\uC9C0\uC815'&&supportMatchesRegion(region,record.supportOffice)){return record.supportOffice;}if(mapped){return mapped;}}var cell=row.querySelector('.support-cell');var value=cell?(cell.textContent||'').trim():'';if(!supportMatchesRegion(region,value)){return '\uBBF8\uC9C0\uC815';}return value||'\uBBF8\uC9C0\uC815';}
function rowMatchesDashboardFilter(row){if(!dashboardDetailFilter){return true;}var value=dashboardDetailFilter.value||'';if(dashboardDetailFilter.type==='vendor'){return (row.getAttribute('data-counterpart')||'\uBBF8\uC9C0\uC815')===value;}if(dashboardDetailFilter.type==='month'){return ((row.getAttribute('data-contract-date')||'').slice(0,7)||'\uBBF8\uC9C0\uC815')===value;}if(dashboardDetailFilter.type==='school'){return (row.getAttribute('data-school-category')||'\uAE30\uD0C0\uAE30\uAD00')===value;}if(dashboardDetailFilter.type==='region'){return rowRegionValue(row)===value;}if(dashboardDetailFilter.type==='support'){return rowSupportValue(row)===value&&(!dashboardDetailFilter.region||rowRegionValue(row)===dashboardDetailFilter.region);}return true;}
function dashboardFilterLabel(filter){if(!filter){return '';}var names={vendor:'업체',month:'월',school:'학교급',region:'시도',support:'교육지원청'};var prefix=names[filter.type]||'대시보드';return prefix+': '+filter.value+(filter.type==='support'&&filter.region?' ('+filter.region+')':'');}
function updateDashboardFilterNote(){var note=document.getElementById('dashboard-filter-note');var text=document.getElementById('dashboard-filter-text');if(!note||!text){return;}if(dashboardDetailFilter){text.textContent=dashboardFilterLabel(dashboardDetailFilter);note.style.display='flex';}else{note.style.display='none';text.textContent='';}}
function activateAllKeywordButton(){document.querySelectorAll('[data-kind="keyword"]').forEach(function(item){item.classList.toggle('active',(item.getAttribute('data-filter-value')||'')==='all');});}
function applyDashboardFilter(type,value,region){dashboardDetailFilter={type:type,value:value,region:region||''};activeKeyword='all';contractSearchText='';unsavedOnly=false;dateMode='all';selectedDateFrom='';selectedDateTo='';var search=document.getElementById('contract-search');if(search){search.value='';}clearDateInputs();syncDateToggles();activateAllKeywordButton();showView('details');filterCurrent();setStatus('대시보드 필터가 적용되었습니다.','ok');}
function clearDashboardFilter(){dashboardDetailFilter=null;updateDashboardFilterNote();filterCurrent();}
function toggleAll(master){document.querySelectorAll('#tbody tr').forEach(function(row){if(row.style.display!=='none'){var cb=row.querySelector('.row-check');if(cb){cb.checked=master.checked;}}});}
function updateUnsavedUi(){var btn=document.getElementById('unsaved-only-btn');if(btn){btn.classList.toggle('active',unsavedOnly);btn.setAttribute('aria-pressed',unsavedOnly?'true':'false');btn.textContent=unsavedOnly?'\uC9C0\uC5ED \uBBF8\uC800\uC7A5\uB9CC \uD45C\uC2DC \uC911':'\uC9C0\uC5ED \uBBF8\uC800\uC7A5\uB9CC \uBCF4\uAE30';}var state=document.getElementById('filter-state');if(state){state.classList.toggle('active',unsavedOnly);state.textContent=unsavedOnly?'\uC9C0\uC5ED \uBBF8\uC800\uC7A5 \uD544\uD130 \uC801\uC6A9 \uC911':'\uC9C0\uC5ED \uD544\uD130 \uAEBC\uC9D0';}}
function renderPagination(total){var els=document.querySelectorAll('.pagination');if(!els.length){return;}var pages=Math.max(1,Math.ceil(total/pageSize));if(currentPage>pages){currentPage=pages;}if(currentPage<1){currentPage=1;}if(total<=pageSize){els.forEach(function(el){el.innerHTML='';el.style.display='none';});return;}var html='<button type="button" class="page-btn" onclick="setPage(1)" '+(currentPage===1?'disabled':'')+'>&laquo;</button>';html+='<button type="button" class="page-btn" onclick="setPage('+(currentPage-1)+')" '+(currentPage===1?'disabled':'')+'>&lsaquo;</button>';var start=Math.max(1,currentPage-3);var end=Math.min(pages,start+6);start=Math.max(1,end-6);for(var i=start;i<=end;i++){html+='<button type="button" class="page-btn '+(i===currentPage?'active':'')+'" onclick="setPage('+i+')">'+i+'</button>';}html+='<button type="button" class="page-btn" onclick="setPage('+(currentPage+1)+')" '+(currentPage===pages?'disabled':'')+'>&rsaquo;</button>';html+='<button type="button" class="page-btn" onclick="setPage('+pages+')" '+(currentPage===pages?'disabled':'')+'>&raquo;</button>';html+='<span class="page-info">'+currentPage+' / '+pages+'</span>';els.forEach(function(el){el.style.display='flex';el.innerHTML=html;});}
function setPage(page){currentPage=page;filterCurrent(true);var wrap=document.querySelector('.table-wrap');if(wrap&&wrap.scrollIntoView){wrap.scrollIntoView({block:'start'});}}
function resetPage(){currentPage=1;}
function filterCurrent(keepPage){if(!keepPage){resetPage();}updateUnsavedUi();updateDashboardFilterNote();var rows=document.querySelectorAll('#tbody tr');var matches=[];var purchaseStats=createPurchaseStats();rows.forEach(function(row){var keywords=row.getAttribute('data-keywords')||'';var keywordMatch=activeKeyword==='all'||(','+keywords+',').indexOf(','+activeKeyword+',')!==-1;var contractText=normalizeSearchText(row.getAttribute('data-search-text')||row.getAttribute('data-contract-name')||'');var contractMatch=!contractSearchText||contractText.indexOf(contractSearchText)!==-1;var deleted=row.getAttribute('data-deleted')==='1';var unsavedMatch=!unsavedOnly||!rowHasRegion(row);var dashboardMatch=rowMatchesDashboardFilter(row);var type=classifyPurchaseType(row.getAttribute('data-contract-name')||'');row.setAttribute('data-purchase-type',type.key);var purchaseMatch=!activePurchaseType||type.key===activePurchaseType;var vendorScopeMatch=vendorMatchesScope(row);var matched=keywordMatch&&contractMatch&&!deleted&&unsavedMatch&&rowMatchesDate(row)&&dashboardMatch&&purchaseMatch&&vendorScopeMatch;row.setAttribute('data-filter-match',matched?'1':'0');if(matched){matches.push(row);addPurchaseStats(purchaseStats,row,type,amountFromRow(row));}});renderPurchaseStats(purchaseStats);var total=matches.length;var pages=Math.max(1,Math.ceil(total/pageSize));if(currentPage>pages){currentPage=pages;}var start=(currentPage-1)*pageSize;var end=start+pageSize;rows.forEach(function(row){row.style.display='none';var cb=row.querySelector('.row-check');if(cb){cb.checked=false;}var no=row.querySelector('.row-no');if(no){no.textContent='';}});matches.slice(start,end).forEach(function(row,index){row.style.display='';var no=row.querySelector('.row-no');if(no){no.textContent=start+index+1;}});document.getElementById('visible-count').textContent=total;document.getElementById('no-result').style.display=total===0?'block':'none';renderPagination(total);var master=document.querySelector('thead input[type="checkbox"]');if(master){master.checked=false;}}
function filterTable(btn,kind,value){activeKeyword=value;document.querySelectorAll('[data-kind="keyword"]').forEach(function(item){item.classList.remove('active');});btn.classList.add('active');filterCurrent();}
function toggleUnsavedOnly(){unsavedOnly=!unsavedOnly;filterCurrent();}
function syncDateToggles(){var all=document.getElementById('all-view');if(all){all.checked=dateMode==='all';}}
function clearDateInputs(){var from=document.getElementById('date-from-filter');var to=document.getElementById('date-to-filter');if(from){from.value='';}if(to){to.value='';}}
function setAllView(checked){dateMode='all';selectedDateFrom='';selectedDateTo='';clearDateInputs();syncDateToggles();filterCurrent();}
function applyDateSearch(){var from=document.getElementById('date-from-filter');var to=document.getElementById('date-to-filter');selectedDateFrom=from?from.value:'';selectedDateTo=to?to.value:'';if(!selectedDateFrom&&!selectedDateTo){setStatus('Select a date range.','error');return;}if(!selectedDateFrom){selectedDateFrom=selectedDateTo;}if(!selectedDateTo){selectedDateTo=selectedDateFrom;}if(selectedDateFrom>selectedDateTo){var tmp=selectedDateFrom;selectedDateFrom=selectedDateTo;selectedDateTo=tmp;}if(from){from.value=selectedDateFrom;}if(to){to.value=selectedDateTo;}dateMode='search';syncDateToggles();filterCurrent();setStatus('Showing selected date range.','ok');}
function clearDateSearch(){dateMode='all';selectedDateFrom='';selectedDateTo='';clearDateInputs();syncDateToggles();filterCurrent();}
document.addEventListener('DOMContentLoaded',function(){syncDateToggles();filterCurrent();renderDashboard();var start=function(){loadRemoteState();};if('requestIdleCallback' in window){requestIdleCallback(start,{timeout:1200});}else{setTimeout(start,250);}});
""".strip()
    js = js.replace("__DEFAULT_DATE_FROM__", default_date_from).replace("__DEFAULT_DATE_TO__", default_date_to)
    js = js.replace("__DASHBOARD_RECORDS__", dashboard_json.replace("</", "<\\/"))

    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>S2B 수의계약 누적 내역</title>"
        "<style>" + css + "</style></head><body>"
        "<div class='wrap'>"
        "<div class='header'><h1>S2B &#49688;&#51032;&#44228;&#50557; &#45572;&#51201; &#45236;&#50669;</h1>"
        "<div class='meta'>&#45572;&#51201; &#49373;&#49457;: " + esc(exported_at) + "</div></div>"
        "<div class='nav-tabs'><button type='button' class='tab-btn active' data-target='dashboard' onclick=\"showView('dashboard')\">&#45824;&#49884;&#48372;&#46300;</button><button type='button' class='tab-btn' data-target='details' onclick=\"showView('details')\">&#49464;&#48512; &#45236;&#50669;</button></div>"
        "<section class='view active' data-view='dashboard'><div class='metric-row'><div class='metric'><div class='metric-label'>&#52509;&#47588;&#52636;&#50529;</div><div class='metric-value' id='dash-total'>0&#50896;</div></div><div class='metric'><div class='metric-label'>&#44228;&#50557; &#44148;&#49688;</div><div class='metric-value' id='dash-count'>0&#44148;</div></div><div class='metric'><div class='metric-label'>&#52572;&#44256; &#47588;&#52636; &#50629;&#52404;</div><div class='metric-value' id='dash-top-vendor'>-</div></div></div><div class='dashboard-grid'><div class='dash-panel'><h2>&#50629;&#52404;&#48324; &#47588;&#52636;&#50529;</h2><div class='bar-list' id='vendor-chart'></div></div><div class='dash-panel'><h2>&#50900;&#48324; &#47588;&#52636;&#50529;</h2><div class='bar-list' id='month-chart'></div></div><div class='dash-panel'><h2>&#49884;&#46020;&#48324; &#47588;&#52636;&#50529;</h2><div class='bar-list' id='region-chart'></div></div><div class='dash-panel'><div class='dash-head'><h2 id='support-title'>&#49884;&#46020; &#44368;&#50977;&#51648;&#50896;&#52397;&#48324; &#47588;&#52636;&#50529;</h2><select id='dashboard-region' class='dash-select' onchange='setDashboardRegion(this.value)'></select></div><div class='bar-list' id='support-chart'></div></div><div class='dash-panel'><h2>학교급별 매출액</h2><div class='bar-list' id='school-chart'></div></div><div class='dash-panel'><h2>구매 유형별 계약금액</h2><div class='bar-list' id='purchase-type-chart'></div></div></div><div class='purchase-panel'><div class='purchase-head'><h2>구매 유형별 계약금액</h2><div class='purchase-tools'><button type='button' class='purchase-scope active' data-purchase-scope='all' onclick=\"setPurchaseVendorScope('all')\">전체</button><button type='button' class='purchase-scope' data-purchase-scope='mirae' onclick=\"setPurchaseVendorScope('mirae')\">미래엔</button><button type='button' class='purchase-scope' data-purchase-scope='other' onclick=\"setPurchaseVendorScope('other')\">그 외</button><button type='button' class='purchase-csv' onclick='downloadPurchaseCsv()'>CSV 다운로드</button></div></div><div class='purchase-table-wrap'><table class='purchase-table'><thead><tr><th>유형</th><th class='tr'>건수</th><th class='tr'>계약금액 합</th><th class='tr'>비중</th><th class='tr'>건당 평균</th></tr></thead><tbody id='purchase-type-body'></tbody><tfoot id='purchase-type-foot'></tfoot></table></div><div id='purchase-complex-note' class='purchase-note'>복합계약 0건 포함 (단일 제품 기준 아님)</div><div class='purchase-subgrid'><div class='purchase-subpanel'><h3>유형 × 학교급</h3><div class='purchase-table-wrap'><table class='purchase-table'><thead><tr><th>유형</th><th class='tr'>초</th><th class='tr'>중</th><th class='tr'>고</th><th class='tr'>기타</th></tr></thead><tbody id='purchase-level-body'></tbody></table></div></div><div class='purchase-subpanel'><h3>유형 × 월별 추이</h3><div class='purchase-table-wrap'><table class='purchase-table'><thead id='purchase-month-head'></thead><tbody id='purchase-month-body'></tbody></table></div></div></div></div></section>"
        "<section class='view detail-view' data-view='details'><div id='dashboard-filter-note' class='dashboard-filter-note'><span>대시보드 필터 적용 중: <strong id='dashboard-filter-text'></strong></span><button type='button' class='action-btn secondary' onclick='clearDashboardFilter()'>필터 해제</button></div><div class='panel'><h2>&#44160;&#49353;&#50612;&#47196; &#54596;&#53552;&#47553;</h2><div class='filters'>" + keyword_buttons + "</div></div>"
        "<div class='summary'><span>&#52509; <strong id='visible-count'>" + str(default_visible_count) + "</strong>&#44148; &#54364;&#49884; &#51473;</span>"
        "<a href='" + esc(ref_url) + "' target='_blank' rel='noopener' class='s2b-link'>S2B &#49688;&#51032;&#44228;&#50557; &#45236;&#50669; &#48148;&#47196;&#44032;&#44592; &#8599;</a></div>"
        "<div class='toolbar date-tools'><label class='date-toggle'><input type='checkbox' id='all-view' checked onchange='setAllView(this.checked)'>&#51204;&#52404; &#48372;&#44592;</label><input type='search' id='contract-search' class='search-input' placeholder='&#44228;&#50557;&#47749;&#183;&#44228;&#50557;&#44592;&#44288;&#183;&#44228;&#50557;&#45824;&#49345;&#51088; &#44160;&#49353;' aria-label='&#44228;&#50557;&#47749;&#183;&#44228;&#50557;&#44592;&#44288;&#183;&#44228;&#50557;&#45824;&#49345;&#51088; &#44160;&#49353;' oninput='setContractSearch(this.value)'><button type='button' class='action-btn secondary' onclick='clearContractSearch()'>&#44160;&#49353; &#52488;&#44592;&#54868;</button><input type='date' id='date-from-filter' class='date-input' aria-label='&#44228;&#50557;&#52404;&#44208;&#51068; &#49884;&#51089;&#51068;'><span class='range-note'>~</span><input type='date' id='date-to-filter' class='date-input' aria-label='&#44228;&#50557;&#52404;&#44208;&#51068; &#51333;&#47308;&#51068;'><button type='button' class='action-btn secondary' onclick='applyDateSearch()'>&#45216;&#51676; &#44160;&#49353;</button><button type='button' class='action-btn secondary' onclick='clearDateSearch()'>&#52488;&#44592;&#54868;</button></div>"
        "<div class='toolbar'><button type='button' id='delete-selected' class='action-btn danger' onclick='deleteSelected()'>&#49440;&#53469; &#49325;&#51228;</button>"
        "<button type='button' id='unsaved-only-btn' class='action-btn filter-toggle' aria-pressed='false' onclick='toggleUnsavedOnly()'>&#51648;&#50669; &#48120;&#51200;&#51109;&#47564; &#48372;&#44592;</button>"
        "<button type='button' id='sync-local' class='action-btn secondary' onclick='syncLocalToSupabase(this)'>&#47196;&#52972; &#51200;&#51109; &#46041;&#44592;&#54868;</button>"
        "<span id='filter-state' class='filter-state'>&#51648;&#50669; &#54596;&#53552; &#44732;&#51664;</span>"
        "<span class='toolbar-spacer'></span><span id='sync-status' class='sync-status'>Supabase sync pending.</span></div>"
        "<div class='pagination pagination-top'></div><div class='table-wrap'><table><thead><tr>"
        "<th><input type='checkbox' aria-label='&#51204;&#52404; &#49440;&#53469;' onclick='toggleAll(this)'></th><th>No</th><th style='text-align:left'>&#44228;&#50557;&#47749;</th><th>&#51648;&#50669;</th><th>&#44368;&#50977;&#51648;&#50896;&#52397;</th><th>&#44228;&#50557;&#44592;&#44288;</th><th>&#44228;&#50557;&#45824;&#49345;&#51088;</th>"
        "<th>&#44552;&#50529;</th><th>&#44228;&#50557;&#52404;&#44208;&#51068;</th>"
        "</tr></thead><tbody id='tbody'>" + rows_html + "</tbody></table>"
        "<div class='no-result' id='no-result'>표시할 계약내역이 없습니다.</div></div>"
        "<div class='pagination pagination-bottom'></div></section>"
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
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def git_output(result):
    return (result.stdout or "").strip()


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
        print(git_output(remote))
        return False

    sync = run_git(["pull", "--rebase", "--autostash", "origin", "main"], timeout=180)
    if sync.returncode != 0:
        print("[github] git pull failed:\n" + git_output(sync))
        return False

    files = [name for name in GITHUB_UPLOAD_FILES if os.path.exists(os.path.join(APP_DIR, name))]
    if not files:
        print("[github] skipped: no cumulative files found")
        return False

    add = run_git(["add"] + files)
    if add.returncode != 0:
        print("[github] git add failed:\n" + git_output(add))
        return False

    status = run_git(["status", "--porcelain", "--"] + files)
    if status.returncode != 0:
        print("[github] git status failed:\n" + git_output(status))
        return False
    if not git_output(status):
        print("[github] no changes to upload")
        return True

    msg = "Update S2B cumulative data " + display_date(date_from) + "~" + display_date(date_to)
    commit = run_git(["commit", "-m", msg])
    if commit.returncode != 0:
        print("[github] git commit failed:\n" + git_output(commit))
        return False

    push = run_git(["push"], timeout=180)
    if push.returncode != 0:
        print("[github] git push failed:\n" + git_output(push))
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
    parser.add_argument("--backfill-excluded", help="쉼표로 지정한 단어 때문에 과거에 제외됐을 가능성이 있는 계약명만 다시 수집합니다. 예: 고등학교,체육")
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
        backfill_terms = parse_backfill_terms(args.backfill_excluded)
    except ValueError as exc:
        print("[error] " + str(exc))
        return
    results = fetch_all(date_from, date_to, keywords, backfill_terms)
    if backfill_terms and not results:
        print("[backfill] no matching records found; cumulative files were not changed.")
        print("done.")
        return
    data = update_cumulative_json(results, date_from, date_to)
    save_cumulative_html(data)
    publish_to_github(date_from, date_to, args.github_upload)
    print("done.")


if __name__ == "__main__":
    main()


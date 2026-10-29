# coding: utf-8
"""나라장터(G2B) 오픈API 수집기: 교육기관의 발주계획 / 입찰공고 / 낙찰(+참여업체 개찰결과) / 계약 정보를 공공데이터포털 API로 받아 누적한다.

- 인증키는 공휴일 API와 같은 data.go.kr 키(S2B_DATA_GO_KR_KEY 환경변수로 덮어쓰기 가능).
  입찰공고정보서비스 / 계약정보서비스 / 낙찰정보서비스 / 발주계획현황서비스에 활용신청이 되어 있어야 한다.
  발주계획현황서비스만 활용신청이 안 돼 있으면 발주계획은 건너뛰고 나머지는 그대로 받는다.
- 원장(crawl_ledger.json)은 학교장터 크롤러와 공유하되 kind="g2b:<source>", search_term=<업무구분>으로 구분한다.
- 체크포인트 디렉터리는 쓰지 않는다(학교장터 finalize가 그 디렉터리를 통째로 병합하므로 섞이면 안 된다).
  대신 하루 조각이 끝날 때마다 바로 누적 파일에 반영한다.
"""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

import s2b_local_crawler as local


API_KEY = os.environ.get("S2B_DATA_GO_KR_KEY", "").strip() or local.HOLIDAY_API_KEY
BASE_URL = "https://apis.data.go.kr/1230000/"
NUM_ROWS = 999
CALL_INTERVAL_SECONDS = 1.0
RATE_LIMIT_WAIT_SECONDS = 60.0
HTTP_RETRY_DELAYS = (5.0, 15.0, 30.0)
DAILY_CALL_BUDGET = 800  # 서비스당 개발계정 한도 1,000회/일보다 여유 있게

# 실제 응답으로 확정한 경로/파라미터 (2026-09-24).
#  - 계약정보: 검색조건 오퍼레이션(PPSSrch)이 없어 전체를 받아 로컬에서 교육기관을 거른다. inqryDiv=1 은 등록일시 기준.
#  - 입찰공고: PPSSrch 가 dminsttNm(수요기관명 부분일치) 서버 필터를 지원. inqryDiv=1 은 공고게시일시 기준.
#  - 낙찰: PPSSrch + dminsttNm. inqryDiv=2 가 개찰일시 기준(1은 등록일시라 건수가 거의 없음).
CATEGORIES = {"물품": "Thng", "용역": "Servc", "공사": "Cnstwk"}
SOURCES = {
    "contract": {"label": "계약", "path": "ao/CntrctInfoService/getCntrctInfoList{cat}", "inqry_div": "1", "server_filter": False},
    "bid": {"label": "입찰공고", "path": "ad/BidPublicInfoService/getBidPblancListInfo{cat}PPSSrch", "inqry_div": "1", "server_filter": True},
    "award": {"label": "낙찰", "path": "as/ScsbidInfoService/getScsbidListSttus{cat}PPSSrch", "inqry_div": "2", "server_filter": True},
    # 발주계획: inqryDiv=1(게시일시) + 발주 연월 범위(orderBgnYm~orderEndYm)가 필수. PPSSrch는 같은 날도 건수가 적게 나와(2026-10-29 확인) 쓰지 않는다.
    #   서버 필터 없이 하루치를 받아 로컬에서 교육기관을 거른다.
    "plan": {"label": "발주계획", "path": "ao/OrderPlanSttusService/getOrderPlanSttusList{cat}", "inqry_div": "1", "server_filter": False},
}
SOURCE_ORDER = ("contract", "bid", "award", "plan")
# 낙찰 건마다 개찰결과(참여업체 전체·순위·투찰금액)를 붙인다. 같은 낙찰정보서비스라 award 호출 예산을 같이 쓴다.
OPENING_PATH = "as/ScsbidInfoService/getOpengResultListInfoOpengCompt"
MAX_BIDDERS = 100
EDU_SERVER_FILTER = "교육청"
# 초중고·교육청 계열만 남긴다. 소관구분 "교육기관"에는 대학 산학협력단, 타 부처 교육원도 섞여 있어서
# 기관명에 교육청/학교/유치원이 있어야 하고, 대학·산학협력단은 뺀다 (교육청 산하 교육연구정보원 등은 이름 앞에 교육청이 붙어 통과).
EDU_MARKERS = ("교육청", "교육지원청", "학교", "유치원")
EDU_EXCLUDE_MARKERS = ("대학", "산학협력단", "전문대", "학원")
EDU_JURISDICTION = "교육기관"

APP_DIR = local.APP_DIR
CUMULATIVE_FILE = os.path.join(APP_DIR, "g2b_cumulative.json")
HTML_FILE = os.path.join(APP_DIR, "g2b.html")
CALL_LOG_FILE = os.path.join(APP_DIR, "outputs", "g2b_call_log.json")


class G2BError(Exception):
    pass


class BudgetExceeded(G2BError):
    pass


# ---------------------------------------------------------------------------
# 호출 예산 (서비스별 일일 카운트, 파일에 남겨 재실행에도 이어진다)
# ---------------------------------------------------------------------------

def load_call_log():
    if not os.path.exists(CALL_LOG_FILE):
        return {}
    try:
        with open(CALL_LOG_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def bump_call_log(source):
    today = datetime.now().strftime("%Y-%m-%d")
    log = load_call_log()
    day = log.setdefault(today, {})
    day[source] = int(day.get(source, 0)) + 1
    # 오래된 날짜는 정리
    for key in list(log.keys()):
        if key < (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d"):
            del log[key]
    os.makedirs(os.path.dirname(CALL_LOG_FILE), exist_ok=True)
    with open(CALL_LOG_FILE, "w", encoding="utf-8") as file:
        json.dump(log, file, ensure_ascii=False, indent=2)
    return day[source]


def calls_today(source):
    return int(load_call_log().get(datetime.now().strftime("%Y-%m-%d"), {}).get(source, 0))


# ---------------------------------------------------------------------------
# API 호출
# ---------------------------------------------------------------------------

def parse_api_payload(payload):
    # 반환: (items, total_count). 게이트웨이/서비스 오류는 G2BError.
    if "OpenAPI_ServiceResponse" in payload:
        header = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        code = str(header.get("returnReasonCode", ""))
        raise G2BError("gateway " + code + " " + str(header.get("returnAuthMsg") or header.get("errMsg") or ""))
    if "nkoneps.com.response.ResponseError" in payload:
        header = payload["nkoneps.com.response.ResponseError"].get("header", {})
        raise G2BError("service " + str(header.get("resultCode")) + " " + str(header.get("resultMsg")))
    response = payload.get("response", payload)
    header = response.get("header", {}) or {}
    if str(header.get("resultCode", "00")) not in ("00", "0"):
        raise G2BError("service " + str(header.get("resultCode")) + " " + str(header.get("resultMsg")))
    body = response.get("body", {}) or {}
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    total = int(body.get("totalCount") or 0)
    return items, total


def api_get(source, path, params, http_get=None):
    # 반환: (items, total_count). 호출 한도/속도 제한/네트워크 오류를 여기서 처리한다.
    http_get = http_get or requests.get
    if calls_today(source) >= DAILY_CALL_BUDGET:
        raise BudgetExceeded(source + " 일일 호출 예산(" + str(DAILY_CALL_BUDGET) + ") 소진")
    query = dict(params)
    query.update({"serviceKey": API_KEY, "type": "json"})
    last_error = None
    for attempt in range(len(HTTP_RETRY_DELAYS) + 1):
        try:
            bump_call_log(source)
            response = http_get(BASE_URL + path, params=query, timeout=120)
            if response.status_code >= 400:
                # 활용신청 안 된 서비스 등 게이트웨이 오류는 403 본문(JSON)에 사유가 온다. 재시도하지 않고 사유대로 처리한다.
                try:
                    parse_api_payload(response.json())
                except ValueError:
                    pass
            response.raise_for_status()
            payload = response.json()
            return parse_api_payload(payload)
        except G2BError as exc:
            message = str(exc)
            if message.startswith("gateway 22") or message.startswith("gateway 23"):
                print("    [g2b] 호출 제한(" + message + "). " + str(int(RATE_LIMIT_WAIT_SECONDS)) + "초 대기 후 재시도")
                time.sleep(RATE_LIMIT_WAIT_SECONDS)
                last_error = exc
                continue
            if message.startswith("gateway 30"):
                raise G2BError("이 서비스에 활용신청이 되어 있지 않습니다 (" + path.split("/")[1] + "). data.go.kr에서 활용신청 후 다시 실행하세요.") from exc
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < len(HTTP_RETRY_DELAYS):
                print("    [g2b] 요청 오류: " + str(exc)[:120] + " -> " + str(int(HTTP_RETRY_DELAYS[attempt])) + "초 후 재시도")
                time.sleep(HTTP_RETRY_DELAYS[attempt])
        finally:
            time.sleep(CALL_INTERVAL_SECONDS)
    raise G2BError("요청이 계속 실패했습니다: " + str(last_error))


def fetch_source_day(source, category, day, http_get=None):
    # 하루치(00:00~23:59)를 페이지 끝까지 받는다. 반환: (raw_items, total_count, pages)
    spec = SOURCES[source]
    path = spec["path"].format(cat=CATEGORIES[category])
    base = {
        "inqryDiv": spec["inqry_div"],
        "inqryBgnDt": day + "0000",
        "inqryEndDt": day + "2359",
        "numOfRows": str(NUM_ROWS),
    }
    if spec["server_filter"]:
        base["dminsttNm"] = EDU_SERVER_FILTER
    if source == "plan":
        # 그날 게시된 계획의 발주 시기는 보통 그해~다다음해라 넉넉히 잡는다
        year = int(day[:4])
        base["orderBgnYm"] = str(year - 1) + "01"
        base["orderEndYm"] = str(year + 2) + "12"
    items = []
    page = 1
    total = 0
    while True:
        params = dict(base)
        params["pageNo"] = str(page)
        page_items, total = api_get(source, path, params, http_get)
        items.extend(page_items)
        if not page_items or len(items) >= total or page * NUM_ROWS >= total:
            break
        page += 1
    return items, total, page


# ---------------------------------------------------------------------------
# 레코드 정규화
# ---------------------------------------------------------------------------

def parse_caret_list(value):
    # "[1^코드^이름^구분^^^],[2^...]" 형식 -> [[...], [...]]
    text = str(value or "").strip()
    if not text:
        return []
    entries = []
    for chunk in re.findall(r"\[([^\]]*)\]", text):
        entries.append(chunk.split("^"))
    return entries


def first_or(entries, index, fallback=""):
    for entry in entries:
        if len(entry) > index and entry[index].strip():
            return entry[index].strip()
    return fallback


def amount_text(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if not digits:
        return ""
    return "{:,}".format(int(digits))


# "부설"은 넣지 않는다: 사범대 부설 학교는 학교 이름(…부설고등학교)으로 이미 걸리고,
# "부설"만으로는 "한국해양과학기술원 부설 선박해양플랜트연구소" 같은 연구소까지 통과한다.
SCHOOL_MARKERS = ("초등학교", "중학교", "고등학교", "유치원")


def is_edu_institution(*names):
    text = " ".join(str(name or "") for name in names)
    # 사범대학부설고등학교처럼 대학 이름이 붙은 초중고는 남기고, 대학·산학협력단 자체는 뺀다.
    if any(marker in text for marker in SCHOOL_MARKERS):
        return True
    if any(marker in text for marker in EDU_EXCLUDE_MARKERS):
        return False
    return any(marker in text for marker in EDU_MARKERS)


def contract_key_from_link(link):
    # 상세 URL의 ctrtNo(계약번호)+ctrtChgOrd(변경차수). 같은 계약이 통합계약번호만 다르게 재등록되는 경우가 있어
    # 통합계약번호 대신 계약번호로 식별하고, 변경계약(차수 01, 02…)은 원계약과 같은 레코드로 합쳐 최신 등록분을 남긴다.
    match = re.search(r"ctrtNo=([A-Za-z0-9]+)", str(link or ""))
    return match.group(1) if match else ""


def normalize_contract(item):
    demand = parse_caret_list(item.get("dminsttList"))
    corps = parse_caret_list(item.get("corpList"))
    institution = first_or(demand, 2, item.get("cntrctInsttNm", ""))
    link = (item.get("cntrctDtlInfoUrl") or "").strip()
    contract_no = contract_key_from_link(link)
    change_order = re.search(r"ctrtChgOrd=(\d+)", link)
    return {
        "id": "contract:" + (contract_no or str(item.get("untyCntrctNo") or item.get("cntrctRefNo") or "")),
        "contract_no": contract_no,
        "change_order": change_order.group(1) if change_order else "",
        "unty_contract_no": str(item.get("untyCntrctNo") or ""),
        "source": "contract",
        "name": (item.get("cntrctNm") or "").strip(),
        "institution": institution,
        "contract_institution": (item.get("cntrctInsttNm") or "").strip(),
        "jurisdiction": (item.get("cntrctInsttJrsdctnDivNm") or "").strip(),
        "counterpart": first_or(corps, 3, ""),
        "counterpart_bizno": first_or(corps, 9, ""),
        "amount": amount_text(item.get("thtmCntrctAmt") or item.get("totCntrctAmt")),
        "date": (item.get("cntrctDate") or item.get("cntrctCnclsDate") or (item.get("rgstDt") or "")[:10]).strip(),
        "registered_at": (item.get("rgstDt") or "").strip(),
        "method": (item.get("cntrctCnclsMthdNm") or "").strip(),
        "basis": (item.get("baseDtls") or "").strip(),
        "law": (item.get("baseLawNm") or "").strip(),
        "notice_no": (item.get("ntceNo") or "").strip(),
        "link": (item.get("cntrctDtlInfoUrl") or "").strip(),
        "classification": (item.get("pubPrcrmntClsfcNm") or "").strip(),
    }


def normalize_bid(item):
    return {
        "id": "bid:" + str(item.get("bidNtceNo") or "") + "-" + str(item.get("bidNtceOrd") or "000"),
        "source": "bid",
        "name": (item.get("bidNtceNm") or "").strip(),
        "institution": (item.get("dminsttNm") or "").strip(),
        "contract_institution": (item.get("ntceInsttNm") or "").strip(),
        "jurisdiction": "",
        "counterpart": "",
        "counterpart_bizno": "",
        "amount": amount_text(item.get("asignBdgtAmt") or item.get("presmptPrce")),
        "date": (item.get("bidNtceDt") or "")[:10],
        "registered_at": (item.get("rgstDt") or item.get("bidNtceDt") or "").strip(),
        "method": (item.get("cntrctCnclsMthdNm") or "").strip(),
        "bid_method": (item.get("bidMethdNm") or "").strip(),
        "close_at": (item.get("bidClseDt") or "").strip(),
        "open_at": (item.get("opengDt") or "").strip(),
        "notice_kind": (item.get("ntceKindNm") or "").strip(),
        "link": (item.get("bidNtceDtlUrl") or item.get("bidNtceUrl") or "").strip(),
        "classification": (item.get("dtilPrdctClsfcNoNm") or "").strip(),
        "changed_at": (item.get("chgDt") or "").strip(),
    }


def normalize_award(item):
    return {
        "id": "award:" + str(item.get("bidNtceNo") or "") + "-" + str(item.get("bidNtceOrd") or "000") + "-" + str(item.get("bidClsfcNo") or "1"),
        "source": "award",
        "name": (item.get("bidNtceNm") or "").strip(),
        "institution": (item.get("dminsttNm") or "").strip(),
        "contract_institution": "",
        "jurisdiction": "",
        "counterpart": (item.get("bidwinnrNm") or "").strip(),
        "counterpart_bizno": (item.get("bidwinnrBizno") or "").strip(),
        "amount": amount_text(item.get("sucsfbidAmt")),
        "date": (item.get("rlOpengDt") or item.get("fnlSucsfDate") or "")[:10],
        "registered_at": (item.get("rgstDt") or "").strip(),
        "method": "",
        "bid_rate": (item.get("sucsfbidRate") or "").strip(),
        "participants": (item.get("prtcptCnum") or "").strip(),
        "notice_no": (item.get("bidNtceNo") or "").strip(),
        "notice_ord": str(item.get("bidNtceOrd") or "000").strip(),
        "bid_clsfc_no": str(item.get("bidClsfcNo") or "").strip(),
        "link": "",
        "classification": "",
    }


def pick(item, *keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


_plan_keys_shown = False


def normalize_plan(item):
    # 실제 응답 항목(2026-10-29): bizNm, orderInsttNm, totlmngInsttNm, sumOrderAmt, orderYear, orderMnth, nticeDt,
    # orderPlanUntyNo, cntrctMthdNm, deptNm, bidNtceNoList, orderPlanDtlUrl 등. 바뀔 때를 대비해 후보도 남겨 두고,
    # 사업명이 비면 실제 항목 이름을 한 번 찍어 둔다. 담당자 이름·전화(ofclNm, telNo)는 저장하지 않는다.
    global _plan_keys_shown
    name = pick(item, "bizNm", "orderPlanNm", "prdctNm", "cntrctNm", "bsnsNm")
    if not name and not _plan_keys_shown:
        _plan_keys_shown = True
        print("    [g2b plan] 사업명 항목을 못 찾았습니다. 응답 항목: " + ", ".join(sorted(item.keys())))
    plan_no = pick(item, "orderPlanUntyNo", "orderPlanNo", "orderPlanSno", "bsnsNo")
    institution = pick(item, "orderInsttNm", "dminsttNm", "insttNm", "ntceInsttNm")
    year, month = pick(item, "orderYear", "orderYy"), pick(item, "orderMnth", "orderMm")
    if not plan_no:
        plan_no = re.sub(r"\s+", "", institution + "|" + name + "|" + year + month)
    return {
        "id": "plan:" + plan_no,
        "source": "plan",
        "name": name,
        "institution": institution,
        "contract_institution": pick(item, "totlmngInsttNm", "jrsdctnInsttNm"),
        "jurisdiction": pick(item, "jrsdctnDivNm"),
        "counterpart": "",
        "amount": amount_text(pick(item, "sumOrderAmt", "orderAmt", "asignBdgtAmt", "bdgtAmt")),
        "date": pick(item, "nticeDt", "rgstDt", "ntceDt", "orderPlanRgstDt")[:10],
        "registered_at": pick(item, "rgstDt", "nticeDt", "ntceDt"),
        "order_month": (year + "-" + month.zfill(2)) if year and month else (year or month),
        "method": pick(item, "cntrctMthdNm", "cntrctCnclsMthdNm"),
        "department": pick(item, "deptNm", "orderInsttDeptNm"),
        "notice_nos": pick(item, "bidNtceNoList", "bidNtceNo"),
        "link": pick(item, "orderPlanDtlUrl"),
        "classification": pick(item, "prdctClsfcNoNm", "dtilPrdctClsfcNoNm"),
        "changed_at": pick(item, "chgDt"),
    }


NORMALIZERS = {"contract": normalize_contract, "bid": normalize_bid, "award": normalize_award, "plan": normalize_plan}


def normalize_bidder(item):
    return {
        "rank": pick(item, "opengRank"),
        "name": pick(item, "prcbdrNm"),
        "bizno": pick(item, "prcbdrBizno"),
        "amount": amount_text(item.get("bidprcAmt")),
        "rate": pick(item, "bidprcrt"),
        "note": pick(item, "rmrk"),
        "tech": pick(item, "techEvlVal"),
        "total": pick(item, "totalEvlAmtVal"),
    }


def fetch_bidders(record, http_get=None):
    # 낙찰 1건의 개찰결과(참여업체 전체). 반환: 업체 목록(순위순, 최대 MAX_BIDDERS)
    # 예전 레코드는 차수·분류번호 항목이 없어 id(award:공고번호-차수-분류번호)에서 꺼낸다
    parts = (record.get("id", "").split(":", 1) + [""])[1].split("-")
    notice_ord = record.get("notice_ord") or (parts[1] if len(parts) > 1 else "") or "000"
    clsfc_no = record.get("bid_clsfc_no") or (parts[2] if len(parts) > 2 else "")
    params = {"bidNtceNo": record.get("notice_no", ""), "bidNtceOrd": notice_ord, "numOfRows": "999", "pageNo": "1"}
    if clsfc_no:
        params["bidClsfcNo"] = clsfc_no
    items, _total = api_get("award", OPENING_PATH, params, http_get)
    bidders = [normalize_bidder(item) for item in items]
    bidders.sort(key=lambda b: int(b["rank"]) if b["rank"].isdigit() else 99999)
    return bidders[:MAX_BIDDERS]


def attach_bidders(records, http_get=None):
    # 낙찰 레코드에 참여업체를 붙인다. 개별 실패는 건너뛰고(다음 실행이나 --fill-bidders로 다시), 예산 소진은 위로 올린다.
    done = 0
    for record in records:
        if record.get("source") != "award" or not record.get("notice_no"):
            continue
        try:
            record["bidders"] = fetch_bidders(record, http_get)
            done += 1
        except BudgetExceeded:
            raise
        except G2BError as exc:
            print("    [g2b] 참여업체 조회 실패 " + record.get("notice_no", "") + ": " + str(exc)[:120])
    return done


def school_name_part(institution):
    # 나라장터 수요기관명은 "부산광역시교육청 동명공업고등학교"처럼 상위기관이 앞에 붙는다.
    # 학교급 판정은 마지막 학교/유치원 토큰으로 한다 (그대로 넣으면 '교육청'이 먼저 걸려 기타기관이 된다).
    tokens = str(institution or "").split()
    for token in reversed(tokens):
        if any(marker in token for marker in ("학교", "유치원", "학원")):
            return token
    return str(institution or "")


# 학교장터 제외어를 그대로 쓰되 "용역"은 뺀다. 나라장터에서는 용역 공고·계약명 대부분에 "용역"이 붙어서
# "AI 코스웨어 구축 용역"처럼 키워드가 맞는 건까지 전부 제외되기 때문이다.
G2B_ALLOWED_EXCLUDE_WORDS = ("용역",)


def is_excluded_g2b_name(name):
    text = name or ""
    for word in G2B_ALLOWED_EXCLUDE_WORDS:
        text = text.replace(word, " ")
    return local.is_excluded_contract_name(text)


def enrich(record, category):
    institution = record.get("institution") or record.get("contract_institution") or ""
    school = school_name_part(institution)
    record["category"] = category
    record["keywords"] = local.tag_keywords(record.get("name", ""))
    record["excluded"] = is_excluded_g2b_name(record.get("name", ""))
    region = local.region_from_institution_name(institution) or {}
    resolved_region = region.get("region", "") or local.short_region(institution)
    record["region"] = "전남" if resolved_region == "광주" else resolved_region
    record["school_name"] = school
    record["school_level"] = local.school_level(school)
    record["school_category"] = local.school_category(school)
    record["is_negotiated"] = "수의" in (record.get("method") or "")
    return record


# 등록일 기준으로 받기 때문에 작년에 체결된 계약의 변경·재등록 건이 섞여 들어온다.
# 계약일이 조회일보다 이만큼 이전이면 이미 지난 매출로 보고 뺀다.
CONTRACT_MAX_BACKDATE_DAYS = 90


def select_records(raw_items, source, category, all_edu=False, day=None):
    # 교육기관 필터(계약은 로컬, 공고/낙찰은 서버 필터 + 로컬 재확인) -> 정규화 -> (계약) 오래된 계약일 제외 -> 키워드 필터
    normalizer = NORMALIZERS[source]
    edu_records = []
    cutoff = ""
    if source == "contract" and day:
        cutoff = (datetime.strptime(day, "%Y%m%d") - timedelta(days=CONTRACT_MAX_BACKDATE_DAYS)).strftime("%Y-%m-%d")
    for item in raw_items:
        if source == "contract":
            demand_names = " ".join(entry[2] for entry in parse_caret_list(item.get("dminsttList")) if len(entry) > 2)
            if not is_edu_institution(item.get("cntrctInsttNm"), demand_names):
                continue
        elif source == "plan":
            plan = normalizer(item)
            if not is_edu_institution(plan["institution"], plan["contract_institution"]):
                continue
        else:
            if not is_edu_institution(item.get("dminsttNm"), item.get("ntceInsttNm")):
                continue
        record = enrich(normalizer(item), category)
        if not record["id"].split(":", 1)[1]:
            continue
        if cutoff and record.get("date") and record["date"] < cutoff:
            continue
        edu_records.append(record)
    if all_edu:
        return edu_records, len(edu_records)
    kept = [record for record in edu_records if record["keywords"] and not record["excluded"]]
    return kept, len(edu_records)


# ---------------------------------------------------------------------------
# 누적 파일
# ---------------------------------------------------------------------------

def load_cumulative():
    if not os.path.exists(CUMULATIVE_FILE):
        return {"exported_at": "", "meta": {}, "records": []}
    with open(CUMULATIVE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("records", [])
    return data


def canonical_id(record):
    # 이전 버전이 통합계약번호로 만든 id를 계약번호 기준으로 바꾼다 (같은 계약의 재등록 중복 제거).
    if record.get("source") == "contract":
        contract_no = record.get("contract_no") or contract_key_from_link(record.get("link"))
        if contract_no:
            record["contract_no"] = contract_no
            return "contract:" + contract_no
    return record.get("id", "")


def merge_cumulative(records, date_from, date_to):
    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = load_cumulative()
    by_id = {}
    for row in sorted(data["records"], key=lambda r: r.get("registered_at", "")):
        row["id"] = canonical_id(row)
        if row["id"]:
            by_id[row["id"]] = row  # 같은 계약이면 등록일이 늦은 쪽이 남는다
    added = updated = 0
    for incoming in sorted(records, key=lambda r: r.get("registered_at", "")):
        incoming["id"] = canonical_id(incoming)
        existing = by_id.get(incoming["id"])
        incoming["last_imported_at"] = imported_at
        if not existing:
            incoming["first_imported_at"] = imported_at
            incoming["import_count"] = 1
            by_id[incoming["id"]] = incoming
            added += 1
            continue
        keywords = sorted(set(existing.get("keywords", [])) | set(incoming.get("keywords", [])))
        first = existing.get("first_imported_at") or imported_at
        count = int(existing.get("import_count") or 0) + 1
        existing.update(incoming)
        existing["keywords"] = keywords
        existing["first_imported_at"] = first
        existing["import_count"] = count
        updated += 1
    rows = sorted(by_id.values(), key=lambda row: (row.get("date", ""), row.get("registered_at", "")), reverse=True)
    payload = {
        "exported_at": imported_at,
        "meta": {"lastImportedAt": imported_at, "lastSearchPeriod": date_from + "~" + date_to, "total": len(rows),
                 "bySource": {source: sum(1 for row in rows if row.get("source") == source) for source in SOURCE_ORDER}},
        "records": rows,
    }
    with open(CUMULATIVE_FILE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=1)
    print("[g2b json] saved: " + CUMULATIVE_FILE + " | added " + str(added) + ", updated " + str(updated) + ", total " + str(len(rows)))
    return payload


# ---------------------------------------------------------------------------
# 대시보드 (g2b.html) - 레코드를 JSON으로 심고 브라우저에서 필터링
# ---------------------------------------------------------------------------

G2B_CSS = """
@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Thin.woff2') format('woff2');
    font-weight: 100;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-ExtraLight.woff2') format('woff2');
    font-weight: 200;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Light.woff2') format('woff2');
    font-weight: 300;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Regular.woff2') format('woff2');
    font-weight: 400;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Medium.woff2') format('woff2');
    font-weight: 500;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-SemiBold.woff2') format('woff2');
    font-weight: 600;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Bold.woff2') format('woff2');
    font-weight: 700;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-ExtraBold.woff2') format('woff2');
    font-weight: 800;
    font-display: swap;
}

@font-face {
    font-family: 'Pretendard';
    src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/pretendard@1.0/Pretendard-Black.woff2') format('woff2');
    font-weight: 900;
    font-display: swap;
}
*{box-sizing:border-box}body{margin:0;font-family:'Pretendard','Malgun Gothic',Arial,sans-serif;font-size:13px;color:#3d453e;background:#f6f8f6}
.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}.header{background:#54805f;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.header h1{font-size:19px;margin:0 0 7px}.meta{font-size:12px;opacity:.88}.header a{color:#fff;font-size:12px}
.nav-tabs{display:flex;gap:8px;margin:0 0 14px;flex-wrap:wrap}.tab-btn{height:34px;border:1px solid #c5d6cb;border-radius:6px;background:#fff;color:#3d453e;font-family:inherit;font-size:13px;padding:0 14px;cursor:pointer}.tab-btn.active{border-color:#54805f;background:#54805f;color:#fff}.cnt{opacity:.75;margin-left:4px}
.toolbar{background:#fff;border:1px solid #dfe8e1;border-radius:8px;margin-bottom:14px;padding:10px 12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.toolbar input,.toolbar select{height:30px;border:1px solid #c2d4c8;border-radius:6px;padding:0 8px;font-family:inherit;font-size:12px}.toolbar input[type=text]{min-width:220px}
.btn{height:30px;border:1px solid #54805f;border-radius:6px;background:#fff;color:#54805f;font-family:inherit;font-size:12px;padding:0 10px;cursor:pointer}.btn.active{background:#54805f;color:#fff}
.metric-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}.metric{background:#fff;border:1px solid #e3ebe5;border-radius:8px;padding:12px 14px}.metric-label{font-size:12px;color:#70807a;margin-bottom:4px}.metric-value{font-size:20px;font-weight:700;color:#3d453e}
.table-wrap{background:#fff;border:1px solid #e3ebe5;border-radius:8px;overflow:auto;max-height:calc(100vh - 300px);min-height:240px}table{width:100%;border-collapse:collapse;min-width:1100px}thead tr{background:#54805f;color:#fff}th{position:sticky;top:0;background:#54805f;padding:10px 8px;font-size:12px;font-weight:600;white-space:nowrap;text-align:left}td{padding:9px 8px;border-bottom:1px solid #eaf1ec;vertical-align:top}tbody tr:hover td{background:#f6f9f7}.tr{text-align:right;white-space:nowrap}.nowrap{white-space:nowrap}
.tag{display:inline-block;background:#eef4f0;color:#54805f;border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px 0 0}.pill{display:inline-block;border-radius:10px;padding:1px 7px;font-size:11px;background:#f2f5f2;color:#5a685f}.pill.nego{background:#f7ede8;color:#c98f80}
a.lnk{color:#4f8068;text-decoration:none}a.lnk:hover{text-decoration:underline}.no-result{text-align:center;padding:48px 20px;color:#8fa096}.footer{text-align:center;color:#99a89e;font-size:11px;margin-top:18px}.embed .header{display:none}.embed .wrap{padding:8px 10px}.embed .table-wrap{max-height:calc(100vh - 300px)}
@media(max-width:900px){.metric-row{grid-template-columns:1fr 1fr}.wrap{padding:12px 8px}}
"""

G2B_JS = r"""
var SOURCES={contract:'계약',bid:'입찰공고',award:'낙찰',plan:'발주계획'};
var state={source:'contract',q:'',method:'all',region:'',category:'',from:'',to:''};
function fmtWon(n){n=Number(n)||0;return n.toLocaleString('ko-KR')+'원';}
function amountNum(v){return Number(String(v||'').replace(/[^0-9]/g,''))||0;}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function rows(){var q=state.q.trim().toLowerCase();return RECORDS.filter(function(r){
  if(r.source!==state.source)return false;
  if(state.method==='nego'&&!r.is_negotiated)return false;
  if(state.method==='comp'&&r.is_negotiated)return false;
  if(state.region&&r.region!==state.region)return false;
  if(state.category&&r.category!==state.category)return false;
  if(state.from&&(r.date||'')<state.from)return false;
  if(state.to&&(r.date||'')>state.to)return false;
  if(q){var hay=[r.name,r.institution,r.counterpart,r.contract_institution,(r.keywords||[]).join(' ')].join(' ').toLowerCase();if(hay.indexOf(q)<0)return false;}
  return true;});}
function render(){var list=rows();var total=list.reduce(function(s,r){return s+amountNum(r.amount);},0);
  document.getElementById('m-count').textContent=list.length.toLocaleString('ko-KR')+'건';
  document.getElementById('m-amount').textContent=fmtWon(total);
  var nego=list.filter(function(r){return r.is_negotiated;}).length;
  document.getElementById('m-nego').textContent=state.source==='award'?'-':nego.toLocaleString('ko-KR')+'건';
  var inst={};list.forEach(function(r){inst[r.institution]=(inst[r.institution]||0)+amountNum(r.amount);});
  var top=Object.keys(inst).sort(function(a,b){return inst[b]-inst[a];})[0];document.getElementById('m-top').textContent=top||'-';
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.toggle('active',b.dataset.source===state.source);});
  document.querySelectorAll('[data-method]').forEach(function(b){b.classList.toggle('active',b.dataset.method===state.method);});
  var head=state.source==='plan'?'<th>NO</th><th>사업명</th><th>발주기관</th><th>지역</th><th>구분</th><th>계약방법</th><th class="tr">발주금액</th><th>발주 시기</th><th>게시일</th><th>공고번호</th>'
    :state.source==='bid'?'<th>NO</th><th>공고명</th><th>수요기관</th><th>지역</th><th>구분</th><th>계약방법</th><th class="tr">예산/추정가</th><th>공고일</th><th>입찰마감</th><th>개찰</th>'
    :state.source==='award'?'<th>NO</th><th>공고명</th><th>수요기관</th><th>지역</th><th>구분</th><th>낙찰업체</th><th class="tr">낙찰금액</th><th>투찰률</th><th>참가</th><th>개찰일</th>'
    :'<th>NO</th><th>계약명</th><th>수요기관</th><th>지역</th><th>구분</th><th>계약방법</th><th>계약업체</th><th class="tr">계약금액</th><th>계약일</th><th>근거</th>';
  document.getElementById('thead').innerHTML='<tr>'+head+'</tr>';
  var body=list.slice(0,2000).map(function(r,i){var name=r.link?'<a class="lnk" href="'+esc(r.link)+'" target="_blank" rel="noopener">'+esc(r.name)+'</a>':esc(r.name);
    var tags=(r.keywords||[]).map(function(k){return '<span class="tag">'+esc(k)+'</span>';}).join('');
    var meth='<span class="pill'+(r.is_negotiated?' nego':'')+'">'+esc(r.method||'-')+'</span>';
    if(state.source==='bid')return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+meth+'</td><td class="tr">'+esc(r.amount)+'</td><td class="nowrap">'+esc(r.date)+'</td><td class="nowrap">'+esc((r.close_at||'').slice(0,16))+'</td><td class="nowrap">'+esc((r.open_at||'').slice(0,16))+'</td></tr>';
    if(state.source==='plan')return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+meth+'</td><td class="tr">'+esc(r.amount)+'</td><td class="nowrap">'+esc(r.order_month)+'</td><td class="nowrap">'+esc(r.date)+'</td><td>'+esc(r.notice_nos)+'</td></tr>';
    if(state.source==='award'){var bd=(r.bidders||[]).map(function(b){return esc(b.rank)+'. '+esc(b.name)+' '+esc(b.amount);}).join('&#10;');
      return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+esc(r.counterpart)+'</td><td class="tr">'+esc(r.amount)+'</td><td class="tr">'+esc(r.bid_rate)+'</td><td class="tr"'+(bd?' title="'+bd+'"':'')+'>'+esc(r.participants)+'</td><td class="nowrap">'+esc(r.date)+'</td></tr>';}
    return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+meth+'</td><td>'+esc(r.counterpart)+'</td><td class="tr">'+esc(r.amount)+'</td><td class="nowrap">'+esc(r.date)+'</td><td>'+esc(r.basis)+'</td></tr>';
  }).join('');
  document.getElementById('tbody').innerHTML=body;document.getElementById('no-result').style.display=list.length?'none':'block';
  document.getElementById('trunc').textContent=list.length>2000?'상위 2,000건만 표시 (CSV는 전체)':'';}
function setSource(s){state.source=s;render();}
function setMethod(m){state.method=m;render();}
function applyFilters(){state.q=document.getElementById('q').value;state.region=document.getElementById('region').value;state.category=document.getElementById('category').value;state.from=document.getElementById('from').value;state.to=document.getElementById('to').value;render();}
function resetFilters(){['q','region','category','from','to'].forEach(function(id){document.getElementById(id).value='';});state.method='all';applyFilters();}
function downloadCsv(){var list=rows();var cols=['source','category','name','institution','contract_institution','region','school_level','method','counterpart','amount','date','close_at','open_at','bid_rate','participants','basis','link','keywords'];
  var lines=[cols.join(',')].concat(list.map(function(r){return cols.map(function(c){var v=c==='keywords'?(r.keywords||[]).join('|'):(r[c]==null?'':r[c]);return '"'+String(v).replace(/"/g,'""')+'"';}).join(',');}));
  var blob=new Blob(['﻿'+lines.join('\n')],{type:'text/csv;charset=utf-8'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='g2b_'+state.source+'_'+new Date().toISOString().slice(0,10)+'.csv';a.click();}
document.addEventListener('DOMContentLoaded',function(){var params=new URLSearchParams(location.search);if(params.get('embed')==='1'){document.body.classList.add('embed');}var initial=params.get('source');if(initial&&SOURCES[initial]){state.source=initial;}var regions={};RECORDS.forEach(function(r){if(r.region)regions[r.region]=1;});var sel=document.getElementById('region');Object.keys(regions).sort().forEach(function(k){var o=document.createElement('option');o.value=k;o.textContent=k;sel.appendChild(o);});
  document.querySelectorAll('.tab-btn').forEach(function(b){var n=RECORDS.filter(function(r){return r.source===b.dataset.source;}).length;b.innerHTML=esc(SOURCES[b.dataset.source])+'<span class="cnt">'+n.toLocaleString('ko-KR')+'</span>';});render();});
"""


def build_html(data):
    records = data.get("records", [])
    exported_at = data.get("exported_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    period = (data.get("meta") or {}).get("lastSearchPeriod", "")
    records_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    tabs = "".join("<button type='button' class='tab-btn' data-source='" + source + "' onclick=\"setSource('" + source + "')\">" + SOURCES[source]["label"] + "</button>" for source in SOURCE_ORDER)
    categories = "".join("<option value='" + html.escape(name) + "'>" + html.escape(name) + "</option>" for name in CATEGORIES)
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>나라장터 교육기관 입찰·계약</title><style>" + G2B_CSS + "</style></head><body><div class='wrap'>"
        "<div class='header'><div><h1>나라장터 교육기관 입찰공고 · 낙찰 · 계약</h1><div class='meta'>누적 생성: " + html.escape(exported_at) + (" · 마지막 수집 기간 " + html.escape(period) if period else "") + " · 키워드 필터 적용분 (S2B와 같은 키워드/제외어)</div></div>"
        "<div><a href='index.html'>← 학교장터 수의계약 대시보드</a></div></div>"
        "<div class='nav-tabs'>" + tabs + "</div>"
        "<div class='metric-row'><div class='metric'><div class='metric-label'>건수</div><div class='metric-value' id='m-count'>0</div></div>"
        "<div class='metric'><div class='metric-label'>금액 합계</div><div class='metric-value' id='m-amount'>0원</div></div>"
        "<div class='metric'><div class='metric-label'>수의계약</div><div class='metric-value' id='m-nego'>0</div></div>"
        "<div class='metric'><div class='metric-label'>최다 수요기관(금액)</div><div class='metric-value' id='m-top' style='font-size:14px'>-</div></div></div>"
        "<div class='toolbar'><input type='text' id='q' placeholder='계약명·기관·업체 검색' onkeyup='applyFilters()'>"
        "<button type='button' class='btn active' data-method='all' onclick=\"setMethod('all')\">전체</button><button type='button' class='btn' data-method='nego' onclick=\"setMethod('nego')\">수의계약</button><button type='button' class='btn' data-method='comp' onclick=\"setMethod('comp')\">경쟁</button>"
        "<select id='region' onchange='applyFilters()'><option value=''>지역 전체</option></select>"
        "<select id='category' onchange='applyFilters()'><option value=''>구분 전체</option>" + categories + "</select>"
        "<input type='date' id='from' onchange='applyFilters()'> ~ <input type='date' id='to' onchange='applyFilters()'>"
        "<button type='button' class='btn' onclick='resetFilters()'>초기화</button><button type='button' class='btn' onclick='downloadCsv()'>CSV 다운로드</button><span id='trunc' class='meta' style='color:#70807a'></span></div>"
        "<div class='table-wrap'><table><thead id='thead'></thead><tbody id='tbody'></tbody></table><div id='no-result' class='no-result'>조건에 맞는 건이 없습니다.</div></div>"
        "<div class='footer'>출처: 공공데이터포털 조달청 나라장터 발주계획현황·입찰공고정보·낙찰정보·계약정보서비스</div></div>"
        "<script>var RECORDS=" + records_json + ";</script><script>" + G2B_JS + "</script></body></html>"
    )


def save_html(data):
    with open(HTML_FILE, "w", encoding="utf-8") as file:
        file.write(build_html(data))
    print("[g2b html] saved: " + HTML_FILE)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def ledger_kind(source):
    return "g2b:" + source


# 계약은 물품·용역만 받는다. 공사 계약은 교육기관 건이 많지만(하루 수백 건) 에듀테크 키워드에 걸린 적이 없고,
# 서비스별 일일 호출 예산의 1/3을 잡아먹는다. 필요하면 --contract-categories 로 바꾼다.
DEFAULT_CONTRACT_CATEGORIES = ("물품", "용역")


def plan_jobs(sources, categories, date_from, date_to, recrawl=False, contract_categories=None):
    ledger = local.load_ledger()
    jobs, skipped = [], []
    for day in local.period_days(date_from, date_to):
        for source in sources:
            source_categories = list(contract_categories) if (source == "contract" and contract_categories) else categories
            for category in source_categories:
                job = {"source": source, "category": category, "day": day}
                if not recrawl and local.is_covered(ledger, category, day, day, ledger_kind(source)):
                    skipped.append(job)
                else:
                    jobs.append(job)
    return jobs, skipped


def run(date_from, date_to, sources, categories, all_edu=False, recrawl=False, github_upload=True, dry_run=False, http_get=None, contract_categories=None):
    if contract_categories is None:
        contract_categories = [c for c in DEFAULT_CONTRACT_CATEGORIES if c in categories] or list(categories)
    jobs, skipped = plan_jobs(sources, categories, date_from, date_to, recrawl, contract_categories)
    print("[g2b] " + local.display_date(date_from) + " ~ " + local.display_date(date_to) + " | sources " + ",".join(sources) + " | categories " + ",".join(categories) + (" | contract: " + ",".join(contract_categories) if "contract" in sources else ""))
    print("[g2b] jobs " + str(len(jobs)) + " (skip " + str(len(skipped)) + " already complete)" + (" | all-edu" if all_edu else " | keyword filter") + (" | DRY RUN" if dry_run else ""))
    for source in sources:
        print("[g2b] " + source + " calls today so far: " + str(calls_today(source)) + "/" + str(DAILY_CALL_BUDGET))

    collected = []
    summary = {"complete": 0, "error": 0}
    stopped = None
    disabled = {}   # 활용신청이 안 된 서비스: 그 소스만 건너뛰고 나머지는 계속 받는다
    # 하루치가 끝날 때마다 누적 파일에 반영하고 그 뒤에 원장을 쓴다. 중간에 죽어도 진행 중이던 하루만 다시 받으면 된다.
    pending_records = []
    pending_ledger = []
    current_day = None

    def flush():
        nonlocal pending_records, pending_ledger
        if dry_run or (not pending_records and not pending_ledger):
            pending_records, pending_ledger = [], []
            return
        if pending_records:
            merge_cumulative(pending_records, current_day, current_day)
        for entry in pending_ledger:
            local.record_ledger(*entry)
        pending_records, pending_ledger = [], []

    for index, job in enumerate(jobs, 1):
        source, category, day = job["source"], job["category"], job["day"]
        if day != current_day:
            flush()
            current_day = day
        if source in disabled:
            continue
        label = "[" + SOURCES[source]["label"] + "/" + category + "] " + local.display_date(day)
        try:
            raw, total, pages = fetch_source_day(source, category, day, http_get)
            records, edu_count = select_records(raw, source, category, all_edu, day)
            bidder_calls = attach_bidders(records, http_get) if source == "award" and not dry_run else 0
            print(label + " raw " + str(total) + " -> edu " + str(edu_count) + " -> kept " + str(len(records)) + " (" + str(pages + bidder_calls) + " calls" + (", 참여업체 " + str(bidder_calls) + "건" if bidder_calls else "") + ") (" + str(index) + "/" + str(len(jobs)) + ")")
            collected.extend(records)
            pending_records.extend(records)
            pending_ledger.append((category, day, day, local.STATUS_COMPLETE, pages, len(records), ledger_kind(source), "api", "raw " + str(total) + " edu " + str(edu_count)))
            summary["complete"] += 1
        except BudgetExceeded as exc:
            print(label + " 중단: " + str(exc))
            stopped = str(exc)
            break
        except G2BError as exc:
            print(label + " 오류: " + str(exc))
            pending_ledger.append((category, day, day, local.STATUS_ERROR, 0, 0, ledger_kind(source), "api", str(exc)[:200]))
            summary["error"] += 1
            if "활용신청" in str(exc):
                if source == "plan":
                    disabled[source] = str(exc)
                    print("[g2b] 발주계획은 건너뜁니다. data.go.kr에서 '조달청_나라장터 발주계획현황서비스' 활용신청 후 다시 받으세요.")
                    continue
                stopped = str(exc)
                break
    flush()

    print("=" * 55)
    print("[g2b] 수집 " + str(len(collected)) + "건 | 완료 " + str(summary["complete"]) + ", 오류 " + str(summary["error"]) + (" | 중단: " + stopped if stopped else ""))
    if stopped:
        local.notify("나라장터 수집 중단", stopped)
    if dry_run:
        return collected
    data = merge_cumulative([], date_from, date_to)
    save_html(data)
    # 통합 대시보드(index.html)는 학교장터 누적 + 나라장터 누적을 함께 싣는다. 여기서도 다시 만들어 둔다.
    try:
        local.save_cumulative_html(local.load_cumulative_json())
    except Exception as exc:
        print("[html] 통합 대시보드 재생성 실패 (나라장터 파일은 저장됨): " + str(exc))
    try:
        local.publish_to_github(date_from, date_to, github_upload)
    except Exception as exc:
        print("[github] upload failed, but local files were saved: " + str(exc))
    return collected


def fill_bidders(github_upload=True, http_get=None):
    # 이미 받아 둔 낙찰 중 참여업체가 없는 건에 개찰결과를 채운다 (기능 추가 전 수집분 보충용).
    data = load_cumulative()
    targets = [row for row in data["records"] if row.get("source") == "award" and "bidders" not in row]
    print("[g2b] 참여업체를 채울 낙찰 " + str(len(targets)) + "건 | award calls today so far: " + str(calls_today("award")) + "/" + str(DAILY_CALL_BUDGET))
    try:
        done = attach_bidders(targets, http_get)
    except BudgetExceeded as exc:
        done = sum(1 for row in targets if "bidders" in row)
        print("[g2b] 중단: " + str(exc))
    print("[g2b] 참여업체 " + str(done) + "건 채움")
    if not done:
        return 0
    period = ((data.get("meta") or {}).get("lastSearchPeriod") or "~").split("~")
    date_from, date_to = period[0], period[-1]
    data = merge_cumulative([row for row in targets if "bidders" in row], date_from, date_to)
    save_html(data)
    try:
        local.publish_to_github(date_from, date_to, github_upload)
    except Exception as exc:
        print("[github] upload failed, but local files were saved: " + str(exc))
    return done


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="나라장터 교육기관 입찰공고/낙찰/계약 수집 (공공데이터포털 API)")
    parser.add_argument("--from", dest="date_from", help="시작일 YYYYMMDD (기본: 전 영업일)")
    parser.add_argument("--to", dest="date_to", help="종료일 YYYYMMDD (기본: 시작일과 같음)")
    parser.add_argument("--sources", default=",".join(SOURCE_ORDER), help="contract,bid,award,plan 중 선택 (기본 전부)")
    parser.add_argument("--fill-bidders", action="store_true", help="이미 받은 낙찰 중 참여업체(개찰결과)가 없는 건만 채우고 종료합니다.")
    parser.add_argument("--categories", default=",".join(CATEGORIES), help="물품,용역,공사 중 선택 (기본 전부; 입찰공고·낙찰에 적용)")
    parser.add_argument("--contract-categories", default=",".join(DEFAULT_CONTRACT_CATEGORIES), help="계약 소스에만 적용할 구분. 기본 물품,용역 (공사 제외)")
    parser.add_argument("--all-edu", action="store_true", help="키워드 필터 없이 교육기관 건 전체를 저장합니다.")
    parser.add_argument("--recrawl", action="store_true", help="원장에 완료 기록이 있어도 다시 받습니다.")
    parser.add_argument("--coverage", action="store_true", help="소스별 수집 완료 기간을 출력하고 종료합니다.")
    parser.add_argument("--dry-run", action="store_true", help="받기만 하고 누적 파일·원장·업로드는 건드리지 않습니다.")
    parser.add_argument("--no-github-upload", action="store_false", dest="github_upload", default=local.AUTO_GITHUB_UPLOAD)
    return parser.parse_args(argv)


def resolve_period(args):
    if args.date_from:
        date_from = local.normalize_date(args.date_from)
        date_to = local.normalize_date(args.date_to) if args.date_to else date_from
    else:
        date_from, date_to, holidays = local.previous_workday_range()
        date_from, date_to = local.normalize_date(date_from), local.normalize_date(date_to)
        print("[g2b] 전 영업일 자동 산출: " + local.display_date(date_from) + " ~ " + local.display_date(date_to) + (" (공휴일 " + ", ".join(holidays) + ")" if holidays else ""))
    if date_from > date_to:
        raise ValueError("시작일이 종료일보다 늦습니다.")
    return date_from, date_to


def main(argv=None):
    print("=" * 55)
    print("  G2B (나라장터) education procurement collector")
    print("=" * 55)
    args = parse_args(argv)
    sources = [item.strip() for item in args.sources.split(",") if item.strip()]
    categories = [item.strip() for item in args.categories.split(",") if item.strip()]
    contract_categories = [item.strip() for item in args.contract_categories.split(",") if item.strip()]
    unknown = [item for item in sources if item not in SOURCES] + [item for item in categories + contract_categories if item not in CATEGORIES]
    if unknown:
        print("[error] unknown source/category: " + ", ".join(unknown))
        return 2
    if args.coverage:
        for source in sources:
            local.print_coverage(categories, ledger_kind(source))
        return 0
    if args.fill_bidders:
        fill_bidders(args.github_upload)
        print("done.")
        return 0
    try:
        date_from, date_to = resolve_period(args)
    except ValueError as exc:
        print("[error] " + str(exc))
        return 2
    run(date_from, date_to, sources, categories, args.all_edu, args.recrawl, args.github_upload, args.dry_run, None, contract_categories)
    print("done.")
    return 0


if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False):
        input("Press Enter to exit...")
    sys.exit(code)

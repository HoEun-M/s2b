# coding: utf-8
"""나라장터(G2B) 오픈API 수집기: 교육기관의 입찰공고 / 낙찰 / 계약 정보를 공공데이터포털 API로 받아 누적한다.

- 인증키는 공휴일 API와 같은 data.go.kr 키(S2B_DATA_GO_KR_KEY 환경변수로 덮어쓰기 가능).
  입찰공고정보서비스 / 계약정보서비스 / 낙찰정보서비스 3개에 활용신청이 되어 있어야 한다.
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
}
SOURCE_ORDER = ("contract", "bid", "award")
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


SCHOOL_MARKERS = ("초등학교", "중학교", "고등학교", "유치원", "부설")


def is_edu_institution(*names):
    text = " ".join(str(name or "") for name in names)
    # 사범대학부설고등학교처럼 대학 이름이 붙은 초중고는 남기고, 대학·산학협력단 자체는 뺀다.
    if any(marker in text for marker in SCHOOL_MARKERS):
        return True
    if any(marker in text for marker in EDU_EXCLUDE_MARKERS):
        return False
    return any(marker in text for marker in EDU_MARKERS)


def normalize_contract(item):
    demand = parse_caret_list(item.get("dminsttList"))
    corps = parse_caret_list(item.get("corpList"))
    institution = first_or(demand, 2, item.get("cntrctInsttNm", ""))
    return {
        "id": "contract:" + str(item.get("untyCntrctNo") or item.get("cntrctRefNo") or ""),
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
        "link": "",
        "classification": "",
    }


NORMALIZERS = {"contract": normalize_contract, "bid": normalize_bid, "award": normalize_award}


def school_name_part(institution):
    # 나라장터 수요기관명은 "부산광역시교육청 동명공업고등학교"처럼 상위기관이 앞에 붙는다.
    # 학교급 판정은 마지막 학교/유치원 토큰으로 한다 (그대로 넣으면 '교육청'이 먼저 걸려 기타기관이 된다).
    tokens = str(institution or "").split()
    for token in reversed(tokens):
        if any(marker in token for marker in ("학교", "유치원", "학원")):
            return token
    return str(institution or "")


def enrich(record, category):
    institution = record.get("institution") or record.get("contract_institution") or ""
    school = school_name_part(institution)
    record["category"] = category
    record["keywords"] = local.tag_keywords(record.get("name", ""))
    record["excluded"] = local.is_excluded_contract_name(record.get("name", ""))
    region = local.region_from_institution_name(institution) or {}
    record["region"] = region.get("region", "") or local.short_region(institution)
    record["school_name"] = school
    record["school_level"] = local.school_level(school)
    record["school_category"] = local.school_category(school)
    record["is_negotiated"] = "수의" in (record.get("method") or "")
    return record


def select_records(raw_items, source, category, all_edu=False):
    # 교육기관 필터(계약은 로컬, 공고/낙찰은 서버 필터 + 로컬 재확인) -> 정규화 -> 키워드 필터
    normalizer = NORMALIZERS[source]
    edu_records = []
    for item in raw_items:
        if source == "contract":
            demand_names = " ".join(entry[2] for entry in parse_caret_list(item.get("dminsttList")) if len(entry) > 2)
            if not is_edu_institution(item.get("cntrctInsttNm"), demand_names):
                continue
        else:
            if not is_edu_institution(item.get("dminsttNm"), item.get("ntceInsttNm")):
                continue
        record = enrich(normalizer(item), category)
        if not record["id"].split(":", 1)[1]:
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


def merge_cumulative(records, date_from, date_to):
    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = load_cumulative()
    by_id = {row["id"]: row for row in data["records"] if row.get("id")}
    added = updated = 0
    for incoming in records:
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
@import url('https://fonts.googleapis.com/earlyaccess/nanumgothic.css');
*{box-sizing:border-box}body{margin:0;font-family:'Nanum Gothic','Malgun Gothic',Arial,sans-serif;font-size:13px;color:#2f343b;background:#f4f6f8}
.wrap{max-width:1280px;margin:0 auto;padding:24px 16px}.header{background:#245a92;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}
.header h1{font-size:19px;margin:0 0 7px}.meta{font-size:12px;opacity:.88}.header a{color:#fff;font-size:12px}
.nav-tabs{display:flex;gap:8px;margin:0 0 14px;flex-wrap:wrap}.tab-btn{height:34px;border:1px solid #bfd0df;border-radius:6px;background:#fff;color:#263442;font-family:inherit;font-size:13px;padding:0 14px;cursor:pointer}.tab-btn.active{border-color:#245a92;background:#245a92;color:#fff}.cnt{opacity:.75;margin-left:4px}
.toolbar{background:#fff;border:1px solid #dce4ec;border-radius:8px;margin-bottom:14px;padding:10px 12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.toolbar input,.toolbar select{height:30px;border:1px solid #b9c7d6;border-radius:6px;padding:0 8px;font-family:inherit;font-size:12px}.toolbar input[type=text]{min-width:220px}
.btn{height:30px;border:1px solid #245a92;border-radius:6px;background:#fff;color:#245a92;font-family:inherit;font-size:12px;padding:0 10px;cursor:pointer}.btn.active{background:#245a92;color:#fff}
.metric-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}.metric{background:#fff;border:1px solid #e2e6ea;border-radius:8px;padding:12px 14px}.metric-label{font-size:12px;color:#69727d;margin-bottom:4px}.metric-value{font-size:20px;font-weight:700;color:#263442}
.table-wrap{background:#fff;border:1px solid #e2e6ea;border-radius:8px;overflow:auto;max-height:calc(100vh - 300px);min-height:240px}table{width:100%;border-collapse:collapse;min-width:1100px}thead tr{background:#245a92;color:#fff}th{position:sticky;top:0;background:#245a92;padding:10px 8px;font-size:12px;font-weight:600;white-space:nowrap;text-align:left}td{padding:9px 8px;border-bottom:1px solid #edf0f2;vertical-align:top}tbody tr:hover td{background:#f8fbff}.tr{text-align:right;white-space:nowrap}.nowrap{white-space:nowrap}
.tag{display:inline-block;background:#e8f1fa;color:#245a92;border-radius:10px;padding:1px 7px;font-size:11px;margin:2px 3px 0 0}.pill{display:inline-block;border-radius:10px;padding:1px 7px;font-size:11px;background:#eef2f5;color:#4b5561}.pill.nego{background:#fdecec;color:#b33a3a}
a.lnk{color:#1769aa;text-decoration:none}a.lnk:hover{text-decoration:underline}.no-result{text-align:center;padding:48px 20px;color:#8a94a0}.footer{text-align:center;color:#9aa3ad;font-size:11px;margin-top:18px}
@media(max-width:900px){.metric-row{grid-template-columns:1fr 1fr}.wrap{padding:12px 8px}}
"""

G2B_JS = r"""
var SOURCES={contract:'계약',bid:'입찰공고',award:'낙찰'};
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
  var head=state.source==='bid'?'<th>NO</th><th>공고명</th><th>수요기관</th><th>지역</th><th>구분</th><th>계약방법</th><th class="tr">예산/추정가</th><th>공고일</th><th>입찰마감</th><th>개찰</th>'
    :state.source==='award'?'<th>NO</th><th>공고명</th><th>수요기관</th><th>지역</th><th>구분</th><th>낙찰업체</th><th class="tr">낙찰금액</th><th>투찰률</th><th>참가</th><th>개찰일</th>'
    :'<th>NO</th><th>계약명</th><th>수요기관</th><th>지역</th><th>구분</th><th>계약방법</th><th>계약업체</th><th class="tr">계약금액</th><th>계약일</th><th>근거</th>';
  document.getElementById('thead').innerHTML='<tr>'+head+'</tr>';
  var body=list.slice(0,2000).map(function(r,i){var name=r.link?'<a class="lnk" href="'+esc(r.link)+'" target="_blank" rel="noopener">'+esc(r.name)+'</a>':esc(r.name);
    var tags=(r.keywords||[]).map(function(k){return '<span class="tag">'+esc(k)+'</span>';}).join('');
    var meth='<span class="pill'+(r.is_negotiated?' nego':'')+'">'+esc(r.method||'-')+'</span>';
    if(state.source==='bid')return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+meth+'</td><td class="tr">'+esc(r.amount)+'</td><td class="nowrap">'+esc(r.date)+'</td><td class="nowrap">'+esc((r.close_at||'').slice(0,16))+'</td><td class="nowrap">'+esc((r.open_at||'').slice(0,16))+'</td></tr>';
    if(state.source==='award')return '<tr><td>'+(i+1)+'</td><td>'+name+'<div>'+tags+'</div></td><td>'+esc(r.institution)+'</td><td class="nowrap">'+esc(r.region)+'</td><td>'+esc(r.category)+'</td><td>'+esc(r.counterpart)+'</td><td class="tr">'+esc(r.amount)+'</td><td class="tr">'+esc(r.bid_rate)+'</td><td class="tr">'+esc(r.participants)+'</td><td class="nowrap">'+esc(r.date)+'</td></tr>';
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
document.addEventListener('DOMContentLoaded',function(){var regions={};RECORDS.forEach(function(r){if(r.region)regions[r.region]=1;});var sel=document.getElementById('region');Object.keys(regions).sort().forEach(function(k){var o=document.createElement('option');o.value=k;o.textContent=k;sel.appendChild(o);});
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
        "<button type='button' class='btn' onclick='resetFilters()'>초기화</button><button type='button' class='btn' onclick='downloadCsv()'>CSV 다운로드</button><span id='trunc' class='meta' style='color:#69727d'></span></div>"
        "<div class='table-wrap'><table><thead id='thead'></thead><tbody id='tbody'></tbody></table><div id='no-result' class='no-result'>조건에 맞는 건이 없습니다.</div></div>"
        "<div class='footer'>출처: 공공데이터포털 조달청 나라장터 입찰공고정보·낙찰정보·계약정보서비스</div></div>"
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


def plan_jobs(sources, categories, date_from, date_to, recrawl=False):
    ledger = local.load_ledger()
    jobs, skipped = [], []
    for day in local.period_days(date_from, date_to):
        for source in sources:
            for category in categories:
                job = {"source": source, "category": category, "day": day}
                if not recrawl and local.is_covered(ledger, category, day, day, ledger_kind(source)):
                    skipped.append(job)
                else:
                    jobs.append(job)
    return jobs, skipped


def run(date_from, date_to, sources, categories, all_edu=False, recrawl=False, github_upload=True, dry_run=False, http_get=None):
    jobs, skipped = plan_jobs(sources, categories, date_from, date_to, recrawl)
    print("[g2b] " + local.display_date(date_from) + " ~ " + local.display_date(date_to) + " | sources " + ",".join(sources) + " | categories " + ",".join(categories))
    print("[g2b] jobs " + str(len(jobs)) + " (skip " + str(len(skipped)) + " already complete)" + (" | all-edu" if all_edu else " | keyword filter") + (" | DRY RUN" if dry_run else ""))
    for source in sources:
        print("[g2b] " + source + " calls today so far: " + str(calls_today(source)) + "/" + str(DAILY_CALL_BUDGET))

    collected = []
    summary = {"complete": 0, "error": 0}
    stopped = None
    for index, job in enumerate(jobs, 1):
        source, category, day = job["source"], job["category"], job["day"]
        label = "[" + SOURCES[source]["label"] + "/" + category + "] " + local.display_date(day)
        try:
            raw, total, pages = fetch_source_day(source, category, day, http_get)
            records, edu_count = select_records(raw, source, category, all_edu)
            print(label + " raw " + str(total) + " -> edu " + str(edu_count) + " -> kept " + str(len(records)) + " (" + str(pages) + " calls) (" + str(index) + "/" + str(len(jobs)) + ")")
            collected.extend(records)
            if not dry_run:
                local.record_ledger(category, day, day, local.STATUS_COMPLETE, pages, len(records), ledger_kind(source), "api", "raw " + str(total) + " edu " + str(edu_count))
            summary["complete"] += 1
        except BudgetExceeded as exc:
            print(label + " 중단: " + str(exc))
            stopped = str(exc)
            break
        except G2BError as exc:
            print(label + " 오류: " + str(exc))
            if not dry_run:
                local.record_ledger(category, day, day, local.STATUS_ERROR, 0, 0, ledger_kind(source), "api", str(exc)[:200])
            summary["error"] += 1
            if "활용신청" in str(exc):
                stopped = str(exc)
                break

    print("=" * 55)
    print("[g2b] 수집 " + str(len(collected)) + "건 | 완료 " + str(summary["complete"]) + ", 오류 " + str(summary["error"]) + (" | 중단: " + stopped if stopped else ""))
    if stopped:
        local.notify("나라장터 수집 중단", stopped)
    if dry_run:
        return collected
    data = merge_cumulative(collected, date_from, date_to)
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="나라장터 교육기관 입찰공고/낙찰/계약 수집 (공공데이터포털 API)")
    parser.add_argument("--from", dest="date_from", help="시작일 YYYYMMDD (기본: 전 영업일)")
    parser.add_argument("--to", dest="date_to", help="종료일 YYYYMMDD (기본: 시작일과 같음)")
    parser.add_argument("--sources", default=",".join(SOURCE_ORDER), help="contract,bid,award 중 선택 (기본 전부)")
    parser.add_argument("--categories", default=",".join(CATEGORIES), help="물품,용역,공사 중 선택 (기본 전부)")
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
    unknown = [item for item in sources if item not in SOURCES] + [item for item in categories if item not in CATEGORIES]
    if unknown:
        print("[error] unknown source/category: " + ", ".join(unknown))
        return 2
    if args.coverage:
        for source in sources:
            local.print_coverage(categories, ledger_kind(source))
        return 0
    try:
        date_from, date_to = resolve_period(args)
    except ValueError as exc:
        print("[error] " + str(exc))
        return 2
    run(date_from, date_to, sources, categories, args.all_edu, args.recrawl, args.github_upload, args.dry_run)
    print("done.")
    return 0


if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False):
        input("Press Enter to exit...")
    sys.exit(code)

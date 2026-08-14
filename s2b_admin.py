import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import s2b_local_crawler as local


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CUMULATIVE_JSON_FILE = os.path.join(APP_DIR, "s2b_cumulative.json")
REGION_OVERRIDES_FILE = os.path.join(APP_DIR, "region_overrides.json")
DELETED_RECORDS_FILE = os.path.join(APP_DIR, "deleted_records.json")
BACKUP_DIR = os.path.join(APP_DIR, "outputs", "admin_backups")

SUPABASE_URL = os.environ.get("S2B_SUPABASE_URL", "https://fozuzbszeujgskjasvzq.supabase.co").rstrip("/")
SUPABASE_KEY = os.environ.get("S2B_SUPABASE_KEY", "sb_publishable_bFJbCmjIbzCEracNlI-lhA_9hYn1rdc")

REGIONS = ["", "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
SCHOOL_LEVELS = ["", "유", "초", "중", "고", "기타"]
EDITABLE_FIELDS = {
    "school_level",
    "region",
    "region_status",
    "region_source",
    "support_office",
    "keywords",
}

DATA_LOCK = threading.Lock()


def read_json_file(path, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json_file(path, value):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)


def load_data():
    data = read_json_file(CUMULATIVE_JSON_FILE, {"exported_at": "", "meta": {}, "records": []})
    data.setdefault("records", [])
    return data


def save_data(data):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.exists(CUMULATIVE_JSON_FILE):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CUMULATIVE_JSON_FILE, os.path.join(BACKUP_DIR, "s2b_cumulative_" + timestamp + ".json"))
    data["exported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data.setdefault("meta", {})["total"] = len(data.get("records", []))
    write_json_file(CUMULATIVE_JSON_FILE, data)


def load_region_overrides():
    value = read_json_file(REGION_OVERRIDES_FILE, {})
    return value if isinstance(value, dict) else {}


def save_region_overrides(value):
    write_json_file(REGION_OVERRIDES_FILE, dict(sorted((value or {}).items())))


def load_deleted_records():
    value = read_json_file(DELETED_RECORDS_FILE, [])
    return set(value if isinstance(value, list) else [])


def save_deleted_records(value):
    write_json_file(DELETED_RECORDS_FILE, sorted(set(value or [])))


def amount_number(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else 0


def record_id(record):
    return record.get("id") or record.get("tender_no") or ""


def support_mismatch(record):
    region = record.get("region") or ""
    support = record.get("support_office") or ""
    if not region or region == "미지정" or not support or support == "미지정":
        return False
    support_region = local.region_from_support_office(support)
    return bool(support_region and support_region != region)


def support_unknown_region(record):
    region = record.get("region") or ""
    support = record.get("support_office") or ""
    if not region or region == "미지정" or not support or support == "미지정":
        return False
    return not local.region_from_support_office(support)


def compact_record(record, deleted):
    rid = record_id(record)
    return {
        "id": rid,
        "contract_name": record.get("contract_name", ""),
        "institution": record.get("institution", ""),
        "counterpart": record.get("counterpart", ""),
        "amount": record.get("amount", ""),
        "amount_number": amount_number(record.get("amount", "")),
        "contract_date": record.get("contract_date", ""),
        "keywords": record.get("keywords", []),
        "school_level": record.get("school_level", ""),
        "region": record.get("region", ""),
        "region_status": record.get("region_status", ""),
        "region_source": record.get("region_source", ""),
        "support_office": record.get("support_office", ""),
        "link": record.get("link", ""),
        "deleted": rid in deleted,
        "support_mismatch": support_mismatch(record),
        "support_unknown_region": support_unknown_region(record),
    }


def support_offices_for_region(region, records=None, current=""):
    offices = set()
    if region:
        for district in local.REGION_DISTRICT_PREFIXES.get(region, {}).values():
            office = local.support_office_from_region_district(region, district)
            if office:
                offices.add(office)
    for row in records or []:
        support = row.get("support_office") or ""
        if support and local.support_office_matches_region(region, support):
            offices.add(support)
        for candidate in row.get("region_candidates", []) or []:
            candidate_region = candidate.get("region") or ""
            candidate_support = candidate.get("support_office") or ""
            if (not region or candidate_region == region) and candidate_support:
                offices.add(candidate_support)
    if current:
        offices.add(current)
    return [""] + sorted(offices)


def calc_stats(records, deleted):
    regions = {}
    mismatches = 0
    unknown_support = 0
    empty_support = 0
    empty_region = 0
    deleted_count = 0
    for row in records:
        rid = record_id(row)
        if rid in deleted:
            deleted_count += 1
        region = row.get("region") or ""
        if not region or region == "미지정":
            empty_region += 1
        else:
            regions[region] = regions.get(region, 0) + 1
        support = row.get("support_office") or ""
        if region and region != "미지정" and (not support or support == "미지정"):
            empty_support += 1
        if support_mismatch(row):
            mismatches += 1
        if support_unknown_region(row):
            unknown_support += 1
    return {
        "total": len(records),
        "deleted": deleted_count,
        "empty_region": empty_region,
        "empty_support_with_region": empty_support,
        "support_mismatch": mismatches,
        "support_unknown_region": unknown_support,
        "regions": regions,
        "exported_at": read_json_file(CUMULATIVE_JSON_FILE, {}).get("exported_at", ""),
    }


def filter_records(records, deleted, params):
    query = (params.get("q", [""])[0] or "").strip().lower()
    region = params.get("region", [""])[0] or ""
    issue = params.get("issue", [""])[0] or ""
    rows = []
    for row in records:
        rid = record_id(row)
        if query:
            haystack = " ".join([
                rid,
                row.get("contract_name", ""),
                row.get("institution", ""),
                row.get("counterpart", ""),
                row.get("support_office", ""),
                ",".join(row.get("keywords", []) or []),
            ]).lower()
            if query not in haystack:
                continue
        if region and (row.get("region") or "") != region:
            continue
        if issue == "deleted" and rid not in deleted:
            continue
        if issue == "no_region" and (row.get("region") or "") not in ("", "미지정"):
            continue
        if issue == "missing_support" and ((row.get("region") or "") in ("", "미지정") or (row.get("support_office") or "") not in ("", "미지정")):
            continue
        if issue == "support_mismatch" and not support_mismatch(row):
            continue
        if issue == "support_unknown" and not support_unknown_region(row):
            continue
        rows.append(row)
    rows.sort(key=lambda item: (item.get("contract_date", ""), item.get("id", "")), reverse=True)
    return rows


def find_record(records, rid):
    for row in records:
        if record_id(row) == rid:
            return row
    return None


def supabase_headers(extra=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
    }
    headers.update(extra or {})
    return headers


def supabase_request(path, method="GET", payload=None, extra_headers=None):
    url = SUPABASE_URL + path
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=supabase_headers(extra_headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Supabase " + method + " failed: " + str(exc.code) + " " + detail[:300]) from exc


def delete_remote_rows(table, ids):
    if not ids:
        return
    for start in range(0, len(ids), 80):
        chunk = ids[start:start + 80]
        quoted = ",".join(urllib.parse.quote(str(item), safe="") for item in chunk)
        supabase_request("/rest/v1/" + table + "?record_id=in.(" + quoted + ")", method="DELETE")


def sync_supabase():
    regions = load_region_overrides()
    deleted = sorted(load_deleted_records())
    remote_regions = supabase_request("/rest/v1/region_overrides?select=record_id&limit=20000") or []
    remote_deleted = supabase_request("/rest/v1/deleted_records?select=record_id&limit=20000") or []
    remote_region_ids = {row.get("record_id") for row in remote_regions if row.get("record_id")}
    remote_deleted_ids = {row.get("record_id") for row in remote_deleted if row.get("record_id")}

    region_rows = [{"record_id": rid, "region": region} for rid, region in regions.items() if rid and region]
    if region_rows:
        supabase_request(
            "/rest/v1/region_overrides?on_conflict=record_id",
            method="POST",
            payload=region_rows,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
    delete_remote_rows("region_overrides", sorted(remote_region_ids - set(regions.keys())))

    deleted_rows = [{"record_id": rid} for rid in deleted]
    if deleted_rows:
        supabase_request(
            "/rest/v1/deleted_records?on_conflict=record_id",
            method="POST",
            payload=deleted_rows,
            extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
    delete_remote_rows("deleted_records", sorted(remote_deleted_ids - set(deleted)))
    return {
        "region_overrides": len(region_rows),
        "deleted_records": len(deleted_rows),
        "removed_remote_regions": len(remote_region_ids - set(regions.keys())),
        "removed_remote_deleted": len(remote_deleted_ids - set(deleted)),
    }


def publish_admin_changes():
    result = local.run_git(["add", "s2b_cumulative.json", "s2b_cumulative.html", "index.html", "region_overrides.json", "deleted_records.json", "s2b_admin.py"])
    if result.returncode != 0:
        raise RuntimeError(local.git_output(result))
    status = local.run_git(["status", "--porcelain", "--", "s2b_cumulative.json", "s2b_cumulative.html", "index.html", "region_overrides.json", "deleted_records.json", "s2b_admin.py"])
    if status.returncode != 0:
        raise RuntimeError(local.git_output(status))
    if not local.git_output(status):
        return {"changed": False, "message": "업로드할 변경사항이 없습니다."}
    commit = local.run_git(["commit", "-m", "Update S2B admin edits"])
    if commit.returncode != 0:
        raise RuntimeError(local.git_output(commit))
    push = local.run_git(["push"], timeout=180)
    if push.returncode != 0:
        raise RuntimeError(local.git_output(push))
    return {"changed": True, "message": "GitHub 업로드 완료", "commit": local.git_output(commit)}


HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S2B 로컬 관리 페이지</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#263442;font:13px 'Malgun Gothic',Arial,sans-serif}.wrap{max-width:1440px;margin:0 auto;padding:18px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.top h1{margin:0;font-size:20px}.sub{color:#69727d;font-size:12px}.grid{display:grid;grid-template-columns:1.45fr .8fr;gap:14px}.panel{background:#fff;border:1px solid #dce4ec;border-radius:8px;padding:14px}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-bottom:14px}.stat{background:#fff;border:1px solid #dce4ec;border-radius:8px;padding:10px}.stat span{display:block;color:#69727d;font-size:11px}.stat strong{display:block;margin-top:4px;font-size:18px}.toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}.input,.select{height:32px;border:1px solid #b9c7d6;border-radius:6px;background:#fff;padding:0 9px;font:inherit}.input{min-width:260px}.btn{height:32px;border:1px solid #245a92;border-radius:6px;background:#245a92;color:#fff;padding:0 11px;font:inherit;cursor:pointer}.btn.secondary{background:#fff;color:#245a92}.btn.danger{background:#b33a3a;border-color:#b33a3a}.btn:disabled{opacity:.55;cursor:wait}.table-wrap{height:calc(100vh - 270px);min-height:460px;overflow:auto;border:1px solid #e2e6ea;border-radius:8px}table{width:100%;border-collapse:collapse;min-width:920px}th{position:sticky;top:0;background:#245a92;color:#fff;padding:9px;text-align:left;z-index:2}td{border-bottom:1px solid #edf0f2;padding:8px;vertical-align:top}tr:hover td{background:#f8fbff}tr.active td{background:#e8f1fa}.num{text-align:right;white-space:nowrap;font-weight:600}.muted{color:#69727d}.pill{display:inline-block;border-radius:10px;background:#e8f1fa;color:#245a92;padding:1px 7px;margin:1px;font-size:11px}.issue{color:#b33a3a;font-weight:700}.form{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{display:flex;flex-direction:column;gap:5px}.field.full{grid-column:1/-1}.field label{font-size:11px;color:#69727d}.field input,.field textarea,.field select{width:100%;border:1px solid #b9c7d6;border-radius:6px;padding:8px;font:inherit}.field input[readonly],.field textarea[readonly]{background:#f4f6f8;color:#5c6670}.field textarea{min-height:74px;resize:vertical}.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.status{margin-top:10px;color:#1d7a38;white-space:pre-wrap}.status.error{color:#b33a3a}.check{display:flex;align-items:center;gap:6px;margin-top:22px}.small{font-size:12px;color:#69727d;line-height:1.5}.pager{display:flex;align-items:center;justify-content:space-between;margin-top:10px}.kbd{font-family:Consolas,monospace;background:#f0f4f8;border-radius:4px;padding:1px 4px}@media(max-width:980px){.grid,.stats{grid-template-columns:1fr}.table-wrap{height:420px}.form{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div><h1>S2B 로컬 관리 페이지</h1><div class="sub">로컬 JSON 수정 → HTML 재생성 → Supabase 지역/삭제 동기화</div></div>
    <div class="actions">
      <button class="btn secondary" onclick="regenerate()">HTML 재생성</button>
      <button class="btn secondary" onclick="syncSupabase()">Supabase 동기화</button>
      <button class="btn" onclick="publishGithub()">GitHub 업로드</button>
    </div>
  </div>
  <div class="stats" id="stats"></div>
  <div class="grid">
    <div class="panel">
      <div class="toolbar">
        <input id="q" class="input" placeholder="계약명, 기관, 대상자, 지원청, ID 검색" onkeydown="if(event.key==='Enter') loadRecords(0)">
        <select id="region" class="select" onchange="loadRecords(0)"></select>
        <select id="issue" class="select" onchange="loadRecords(0)">
          <option value="">전체</option>
          <option value="missing_support">지원청 없음</option>
          <option value="support_mismatch">시도-지원청 불일치</option>
          <option value="support_unknown">지원청 시도 판정 불가</option>
          <option value="no_region">지역 없음</option>
          <option value="deleted">삭제 처리</option>
        </select>
        <button class="btn secondary" onclick="loadRecords(0)">검색</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>계약명</th><th>지역/지원청</th><th>기관</th><th>대상자</th><th>금액</th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
      <div class="pager"><button class="btn secondary" onclick="prevPage()">이전</button><span id="pageInfo"></span><button class="btn secondary" onclick="nextPage()">다음</button></div>
    </div>
    <div class="panel">
      <h2 style="margin:0 0 10px;font-size:15px">선택 행 수정</h2>
      <div class="small">S2B 원본 계약 정보는 읽기 전용입니다. 지역, 세부지역/교육지원청, 학교급, 키워드처럼 후처리로 추가한 값만 수정할 수 있습니다.</div>
      <form id="editForm" class="form" onsubmit="event.preventDefault(); saveRecord();">
        <div class="field full"><label>ID</label><input id="f_id" disabled></div>
        <div class="field full"><label>계약명</label><textarea id="f_contract_name" readonly></textarea></div>
        <div class="field"><label>계약기관</label><input id="f_institution" readonly></div>
        <div class="field"><label>계약대상자</label><input id="f_counterpart" readonly></div>
        <div class="field"><label>금액</label><input id="f_amount" readonly></div>
        <div class="field"><label>계약체결일</label><input id="f_contract_date" readonly></div>
        <div class="field"><label>지역</label><select id="f_region" onchange="populateSupportOffices(this.value,'')"></select></div>
        <div class="field"><label>세부지역/교육지원청</label><select id="f_support_office"></select></div>
        <div class="field"><label>학교급</label><select id="f_school_level"></select></div>
        <div class="field"><label>지역 상태</label><input id="f_region_status"></div>
        <div class="field"><label>지역 출처</label><input id="f_region_source"></div>
        <div class="field full"><label>키워드, 쉼표 구분</label><input id="f_keywords"></div>
        <label class="check"><input type="checkbox" id="f_deleted"> 삭제 처리</label>
        <div class="actions full">
          <button class="btn" type="submit">저장</button>
          <button class="btn secondary" type="button" onclick="clearSupport()">지원청 비우기</button>
          <button class="btn danger" type="button" onclick="toggleDeletedOnly()">삭제 토글 저장</button>
        </div>
      </form>
      <div id="status" class="status"></div>
    </div>
  </div>
</div>
<script>
const REGIONS=["","서울","부산","대구","인천","광주","대전","울산","세종","경기","강원","충북","충남","전북","전남","경북","경남","제주"];
const LEVELS=["","유","초","중","고","기타"];
let offset=0, limit=80, total=0, selected=null;
function el(id){return document.getElementById(id)}
function status(msg,err=false){el('status').textContent=msg;el('status').className='status'+(err?' error':'')}
async function api(path,opt){const res=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},opt||{}));const data=await res.json().catch(()=>({}));if(!res.ok)throw new Error(data.error||res.statusText);return data}
function fillSelect(node,values,allLabel){node.innerHTML='';values.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v||allLabel||'선택';node.appendChild(o)})}
function won(v){return Number(v||0).toLocaleString('ko-KR')+'원'}
function keywords(v){return (v||[]).map(k=>`<span class="pill">${escapeHtml(k)}</span>`).join('')}
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function populateSupportOffices(region,current){const data=await api('/api/support-offices?region='+encodeURIComponent(region||'')+'&current='+encodeURIComponent(current||''));const node=el('f_support_office');node.innerHTML='';data.offices.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v||'미지정';node.appendChild(o)});node.value=current||''}
async function loadStats(){const data=await api('/api/stats');el('stats').innerHTML=[
['전체',data.total.toLocaleString('ko-KR')+'건'],['삭제',data.deleted.toLocaleString('ko-KR')+'건'],['지역 없음',data.empty_region.toLocaleString('ko-KR')+'건'],['지원청 없음',data.empty_support_with_region.toLocaleString('ko-KR')+'건'],['시도-지원청 불일치',data.support_mismatch.toLocaleString('ko-KR')+'건']
].map(x=>`<div class="stat"><span>${x[0]}</span><strong>${x[1]}</strong></div>`).join('')}
async function loadRecords(nextOffset){offset=Math.max(0,nextOffset||0);const p=new URLSearchParams({q:el('q').value,region:el('region').value,issue:el('issue').value,offset,limit});const data=await api('/api/records?'+p);total=data.total;el('pageInfo').textContent=`${total.toLocaleString('ko-KR')}건 중 ${total?offset+1:0}-${Math.min(offset+limit,total)}`;el('rows').innerHTML=data.records.map(r=>`<tr onclick="selectRecord('${r.id}')" class="${selected&&selected.id===r.id?'active':''}"><td><b>${escapeHtml(r.contract_name)}</b><div class="muted">${r.id} · ${escapeHtml(r.contract_date)} ${r.deleted?' · 삭제':''}</div><div>${keywords(r.keywords)}</div></td><td>${escapeHtml(r.region||'미지정')}<br><span class="${r.support_mismatch?'issue':'muted'}">${escapeHtml(r.support_office||'미지정')}</span></td><td>${escapeHtml(r.institution)}</td><td>${escapeHtml(r.counterpart)}</td><td class="num">${won(r.amount_number)}</td></tr>`).join('')}
async function selectRecord(id){selected=(await api('/api/records/'+encodeURIComponent(id))).record;for(const k of ['id','contract_name','institution','counterpart','amount','contract_date','region','school_level','region_status','region_source']){el('f_'+k).value=Array.isArray(selected[k])?selected[k].join(', '):(selected[k]||'')}await populateSupportOffices(selected.region||'',selected.support_office||'');el('f_keywords').value=(selected.keywords||[]).join(', ');el('f_deleted').checked=!!selected.deleted;loadRecords(offset)}
function collect(){return {region:el('f_region').value,support_office:el('f_support_office').value,school_level:el('f_school_level').value,region_status:el('f_region_status').value,region_source:el('f_region_source').value,keywords:el('f_keywords').value.split(',').map(x=>x.trim()).filter(Boolean),deleted:el('f_deleted').checked}}
async function saveRecord(){if(!selected){status('먼저 행을 선택하세요.',true);return}try{const data=await api('/api/records/'+encodeURIComponent(selected.id),{method:'PUT',body:JSON.stringify(collect())});selected=data.record;status('저장했습니다. HTML 반영은 상단의 HTML 재생성을 눌러주세요.');await loadStats();await loadRecords(offset)}catch(e){status(e.message,true)}}
async function toggleDeletedOnly(){if(!selected)return;el('f_deleted').checked=!el('f_deleted').checked;await saveRecord()}
function clearSupport(){el('f_support_office').value=''}
async function regenerate(){try{status('HTML 재생성 중...');const data=await api('/api/regenerate',{method:'POST'});status(data.message)}catch(e){status(e.message,true)}}
async function syncSupabase(){try{status('Supabase 동기화 중...');const data=await api('/api/supabase-sync',{method:'POST'});status(`Supabase 동기화 완료\n지역 override ${data.region_overrides}건, 삭제 ${data.deleted_records}건`)}catch(e){status(e.message,true)}}
async function publishGithub(){try{status('GitHub 업로드 중...');const data=await api('/api/github-publish',{method:'POST'});status(data.message)}catch(e){status(e.message,true)}}
function prevPage(){loadRecords(Math.max(0,offset-limit))}
function nextPage(){if(offset+limit<total)loadRecords(offset+limit)}
fillSelect(el('region'),REGIONS,'전체 지역');fillSelect(el('f_region'),REGIONS,'선택');fillSelect(el('f_school_level'),LEVELS,'선택');loadStats();loadRecords(0);
</script>
</body>
</html>"""


class AdminHandler(BaseHTTPRequestHandler):
    server_version = "S2BAdmin/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[admin] " + fmt % args + "\n")

    def send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            raw = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        try:
            params = urllib.parse.parse_qs(parsed.query)
            with DATA_LOCK:
                data = load_data()
                deleted = load_deleted_records()
                records = data.get("records", [])
                if parsed.path == "/api/stats":
                    self.send_json(calc_stats(records, deleted))
                    return
                if parsed.path == "/api/support-offices":
                    region = params.get("region", [""])[0] or ""
                    current = params.get("current", [""])[0] or ""
                    self.send_json({"offices": support_offices_for_region(region, records, current)})
                    return
                if parsed.path == "/api/records":
                    rows = filter_records(records, deleted, params)
                    offset = int(params.get("offset", ["0"])[0] or 0)
                    limit = min(300, int(params.get("limit", ["80"])[0] or 80))
                    self.send_json({"total": len(rows), "records": [compact_record(row, deleted) for row in rows[offset:offset + limit]]})
                    return
                if parsed.path.startswith("/api/records/"):
                    rid = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
                    row = find_record(records, rid)
                    if not row:
                        self.send_json({"error": "record not found"}, 404)
                        return
                    self.send_json({"record": compact_record(row, deleted)})
                    return
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "not found"}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/records/"):
            self.send_json({"error": "not found"}, 404)
            return
        rid = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
        try:
            payload = self.read_body()
            with DATA_LOCK:
                data = load_data()
                deleted = load_deleted_records()
                row = find_record(data.get("records", []), rid)
                if not row:
                    self.send_json({"error": "record not found"}, 404)
                    return
                before_region = row.get("region", "")
                for key in EDITABLE_FIELDS:
                    if key in payload:
                        row[key] = payload[key]
                row["keywords"] = [str(item).strip() for item in (row.get("keywords") or []) if str(item).strip()]
                row["region"], row["support_office"] = local.normalize_record_region_support(row.get("region", ""), row.get("support_office", ""))
                if support_mismatch(row):
                    self.send_json({"error": "지역과 교육지원청 시도가 맞지 않습니다. 지원청을 비우거나 같은 시도 값으로 저장하세요."}, 400)
                    return
                overrides = load_region_overrides()
                if row.get("region") and row.get("region") != before_region:
                    overrides[rid] = row.get("region")
                    save_region_overrides(overrides)
                if payload.get("deleted"):
                    deleted.add(rid)
                else:
                    deleted.discard(rid)
                save_deleted_records(deleted)
                save_data(data)
                self.send_json({"record": compact_record(row, deleted)})
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/regenerate":
                with DATA_LOCK:
                    data = load_data()
                    local.save_cumulative_html(data)
                self.send_json({"message": "HTML 재생성 완료: index.html, s2b_cumulative.html"})
                return
            if parsed.path == "/api/supabase-sync":
                self.send_json(sync_supabase())
                return
            if parsed.path == "/api/github-publish":
                self.send_json(publish_admin_changes())
                return
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "not found"}, 404)


def parse_args():
    parser = argparse.ArgumentParser(description="S2B local admin page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AdminHandler)
    url = "http://" + args.host + ":" + str(args.port)
    print("[admin] open " + url)
    print("[admin] press Ctrl+C to stop")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

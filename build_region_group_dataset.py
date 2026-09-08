import json
import re
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs" / "s2b_region_group_export_20260819"
OUT_JSON = OUT_DIR / "region_group_dataset.json"

GROUP_LINES = """
강원	춘천,철원,화천,홍천,양구,인제
강원	원주,횡성,평창,영월
강원	강릉,속초,양양,동해,삼척,고성
경기	성남
경기	안산
경기	하남,광주
경기	부천
경기	광명
경기	시흥
경기	수원
경기	안양,군포,의왕,과천
경기	화성
경기	용인
경기	오산
경기	이천,여주
경기	양평,가평
경기	평택,안성
경기	김포
경기	양주,동두천,연천
경기	고양
경기	구리,남양주
경기	의정부,포천
경기	파주
경남	창원, 마산
경남	김해
경남	양산
경남	통영,거제
경남	밀양
경남	사천,남해
경남	진주
경북	포항
경북	경주
경북	안동,예천,의성,영양
경북	구미
경북	김천
경북	칠곡,왜관
경북	경산,영천,청도
광주	광주,장성,화순,담양,곡성,보성
대구	대구
대전	대전
부산	영도구,서구,사하구,중구,부산진구,강서구,사상구,북구,동구
부산	남구,동래구,금정구,연제구,수영구,기장군,해운대구
서울	관악,동작
서울	영등포,구로,금천
서울	강서,양천구
서울	성동,동대문,중랑,광진
서울	강남,서초
서울	마포,용산
서울	노원,도봉,성북,강북
서울	강동,송파
서울	서대문,은평
세종	세종
울산	울산
인천	인천,부평
전남	여수
전남	강진,영암,장흥
전남	영광
전남	목포,무안,신안
전남	순천,광양
전남	나주,함평
전남	고흥
전남	해남,완도,진도
전북	남원,임실,순창,장수
전북	김제,부안,전주,완주
전북	정읍,고창
전북	익산
전북	군산
제주	제주, 서귀포
충남	아산
충남	서산,태안,보령
충남	당진, 홍성, 예산
충남	천안
충남	논산,계룡, 부여
충남	공주,청양
충북	진천,청주
충북	제천,단양
충북	충주
충북	괴산,증평,음성
충북	옥천,보은,영동
""".strip()

EXCLUDE_KEYWORDS = ["미래엔", "초코", "초코팝", "초코클래스", "달달"]

PURCHASE_TYPES = [
    ("AI디지털 교육자료 (AIDT)", ["AIDT", "디지털 교육자료", "디지털교육자료", "AI디지털교과서", "AI 디지털 교과서", "발행사 AIDT"]),
    ("평가도구", ["지니아튜터", "매쓰홀릭", "매쓰홀릭T", "스쿨플랫", "매쓰플랫", "수학대왕", "기출탭탭", "일프로연산", "문제은행", "진단평가", "학력진단", "단원평가", "리드 인공지능 문해력 진단", "수학 학습 프로그램", "평가지원", "평가"]),
    ("코스웨어", ["코들", "Codle", "초코팝", "초코클래스", "달달", "옥수수", "홈런", "아이스크림홈런", "밀크티", "스마트올", "엘리하이", "자작자작", "클래스팅", "클래스팅 AI", "토도수학", "알공 수학", "똑똑수학탐험대", "토도한글", "러니", "레서", "매일국어T", "리드", "토도영어", "알공 영어", "쿠키영어", "리딩앤", "리딩게이트", "리딩오션", "원아워", "영어독후활동프로그램", "코드모스", "엘리스스쿨", "엘리스 LXP", "플랭", "플랭스쿨", "스쿨런", "두드림", "AI 코스웨어", "코스웨어"]),
    ("교사업무지원 AI", ["마이클", "MyClass", "My Class", "이음AI", "이음 AI", "AI 구독", "AI구독", "인라인AI", "인라인 AI", "InlineAI", "Inline AI", "유튜브", "YouTube", "GPT", "지피티", "챗지피티", "클로드", "Claude", "제미나이", "Gemini", "캡컷", "CapCut", "진로진학상담", "수파베이스", "Supabase", "커서", "Cursor", "Google AI Pro", "구글 AI", "Grok"]),
    ("수업도구", ["젠스파크", "Genspark", "학습지원", "Zoom", "줌", "Vrew", "브루", "캔바", "Canva", "미리캔버스", "젭", "ZEP", "카훗", "Kahoot", "북크리에이터", "패들렛", "Padlet", "감마", "Gamma", "수노", "Suno", "뤼튼", "노션", "Notion", "띵커벨", "퀴즈앤", "클래스카드", "퀴즈렛", "다했니", "슬라이도", "망고보드", "투닝", "스픽", "Speak", "일레븐랩스", "ElevenLabs", "런웨이", "Runway", "미드저니", "Midjourney", "교우관계", "화상영어", "메타버스", "클링AI", "클링", "공학도구", "심스페이스", "마음일기", "AI대화", "감정사전", "마음관리"]),
    ("하드웨어·디바이스", ["태블릿", "노트북", "전자칠판", "충전함", "실물화상기", "크롬북", "아이패드", "거치대", "갤럭시탭", "키보드", "마우스", "카메라", "웹캠", "모니터", "스마트기기", "디바이스"]),
    ("코딩", ["코딩", "Coding", "퓨너스"]),
    ("교구·실물자료", ["레고", "LEGO", "스파이크", "포디랜드", "4D프레임", "고피쉬", "카드게임", "보드게임", "키트", "모형", "드론", "로봇", "메이커", "3D펜", "VR", "AR"]),
    ("교재·도서", ["만점왕", "EBS", "워크북", "문제집", "받아쓰기", "일일수학", "기능중심수학", "한걸음 수학", "학습지", "교과서 외", "권세트", "4권세트"]),
    ("연수·컨설팅·용역", ["연수", "강사", "컨설팅", "직무연수", "교원연수", "운영용역"]),
]


def norm(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def strip_suffix(value):
    value = re.sub(r"\s+", "", str(value or ""))
    value = re.sub(r"(특별자치시|특별자치도|광역시|특별시|교육지원청|지원청|교육청)$", "", value)
    if len(value) > 2:
        value = re.sub(r"(시|군|구)$", "", value)
    return value


def sheet_name(region, tokens, used):
    base = region + "_" + "".join(strip_suffix(token) for token in tokens)
    base = re.sub(r"[\[\]\:\*\?\/\\]", "", base)[:31] or region
    name = base
    i = 2
    while name in used:
        suffix = str(i)
        name = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def amount_number(value):
    return int(re.sub(r"[^0-9]", "", str(value or "")) or 0)


def classify(contract_name):
    text = norm(contract_name)
    for label, keywords in PURCHASE_TYPES:
        for keyword in keywords:
            if norm(keyword) in text:
                return label, keyword
    return "미분류", ""


def candidate_matches_record(candidate, record, direct_terms):
    record_region = strip_suffix(record.get("region", ""))
    candidate_region = strip_suffix(candidate.get("region", ""))
    if record_region and candidate_region == record_region:
        return True
    candidate_terms = []
    for key in ("district", "address", "support_office", "education_office"):
        value = candidate.get(key)
        if value:
            candidate_terms.append(strip_suffix(value))
            for part in re.split(r"[,/·\s]+", str(value)):
                if part:
                    candidate_terms.append(strip_suffix(part))
    if any(term and term in direct_terms for term in candidate_terms):
        return True
    return not record_region


def location_terms(record, include_candidates=False):
    terms = []
    for key in ("region", "subregion", "support_office", "institution"):
        value = record.get(key)
        if value:
            terms.append(strip_suffix(value))
            for part in re.split(r"[,/·\s]+", str(value)):
                if part:
                    terms.append(strip_suffix(part))
    if not include_candidates:
        return [term for term in dict.fromkeys(terms) if term]
    direct_terms = set(term for term in terms if term)
    institution_key = norm(record.get("institution"))
    for candidate in record.get("region_candidates") or []:
        if norm(candidate.get("school_name")) != institution_key:
            continue
        if not candidate_matches_record(candidate, record, direct_terms):
            continue
        for key in ("district", "address", "support_office", "education_office"):
            value = candidate.get(key)
            if value:
                terms.append(strip_suffix(value))
                for part in re.split(r"[,/·\s]+", str(value)):
                    if part:
                        terms.append(strip_suffix(part))
    return [term for term in dict.fromkeys(terms) if term]


def parse_groups():
    used = set()
    groups = []
    for line in GROUP_LINES.splitlines():
        region, raw_tokens = line.split("\t", 1)
        tokens = [token.strip() for token in raw_tokens.split(",") if token.strip()]
        normalized_tokens = [strip_suffix(token) for token in tokens]
        groups.append({
            "region": region.strip(),
            "label": raw_tokens.replace(", ", ","),
            "tokens": tokens,
            "normalized_tokens": normalized_tokens,
            "sheet": sheet_name(region.strip(), tokens, used),
        })
    return groups


def assign_group(record, groups):
    record_region = strip_suffix(record.get("region", ""))
    if record_region == "미지정":
        record_region = ""
    direct_terms = set(location_terms(record, include_candidates=False))
    candidate_terms = set(location_terms(record, include_candidates=True))
    institution_text = norm(record.get("institution", ""))
    for terms in ({token for token in direct_terms if norm(token) and norm(token) in institution_text}, direct_terms):
        for group in groups:
            group_region = strip_suffix(group["region"])
            same_region = record_region == group_region
            if same_region and group_region in group["normalized_tokens"]:
                return group
            if same_region and any(token in terms for token in group["normalized_tokens"]):
                return group
    direct_matches = [group for group in groups if any(token in direct_terms for token in group["normalized_tokens"])]
    if direct_matches:
        if not record_region and len(direct_matches) != 1:
            return None
        return direct_matches[0]
    for group in groups:
        group_region = strip_suffix(group["region"])
        same_region = record_region == group_region
        if same_region and any(token in candidate_terms for token in group["normalized_tokens"]):
            return group
    if not record_region:
        candidate_matches = [group for group in groups if any(token in candidate_terms for token in group["normalized_tokens"])]
        if len(candidate_matches) == 1:
            return candidate_matches[0]
    return None


def main():
    groups = parse_groups()
    deleted_path = ROOT / "outputs" / "supabase_deleted_records_current.json"
    deleted = set()
    if deleted_path.exists():
        deleted = {row.get("record_id") for row in json.loads(deleted_path.read_text(encoding="utf-8-sig")) if row.get("record_id")}

    data = json.loads((ROOT / "s2b_cumulative.json").read_text(encoding="utf-8"))
    grouped = OrderedDict((group["sheet"], {"meta": group, "rows": []}) for group in groups)
    grouped["미분류"] = {"meta": {"region": "미분류", "label": "미분류", "tokens": [], "sheet": "미분류"}, "rows": []}
    excluded_count = 0
    deleted_count = 0

    for record in data.get("records", []):
        if record.get("id") in deleted:
            deleted_count += 1
            continue
        haystack = " ".join([
            record.get("contract_name", ""),
            record.get("institution", ""),
            record.get("counterpart", ""),
            " ".join(record.get("keywords") or []),
        ])
        if any(norm(keyword) in norm(haystack) for keyword in EXCLUDE_KEYWORDS):
            excluded_count += 1
            continue
        group = assign_group(record, groups)
        purchase_type, matched_keyword = classify(record.get("contract_name", ""))
        row = {
            "지역": record.get("region") or "미지정",
            "세부지역": record.get("subregion") or "미지정",
            "계약기관": record.get("institution") or "",
            "학교급": record.get("school_level") or "",
            "학교구분": record.get("school_type") or "일반",
            "계약명": record.get("contract_name") or "",
            "구매유형": purchase_type,
            "매칭 제품/키워드": matched_keyword,
            "계약대상자": record.get("counterpart") or "",
            "금액": amount_number(record.get("amount")),
            "계약체결일": record.get("contract_date") or "",
            "검색키워드": ", ".join(record.get("keywords") or []),
            "공고번호": record.get("id") or record.get("tender_no") or "",
            "링크": record.get("link") or "",
            "_sort": [record.get("institution") or "", record.get("contract_date") or "", record.get("contract_name") or ""],
        }
        target = group["sheet"] if group else "미분류"
        grouped[target]["rows"].append(row)

    for bucket in grouped.values():
        bucket["rows"].sort(key=lambda item: item["_sort"])
        for row in bucket["rows"]:
            del row["_sort"]

    summary = {
        "source_records": len(data.get("records", [])),
        "deleted_excluded": deleted_count,
        "keyword_excluded": excluded_count,
        "included": sum(len(bucket["rows"]) for bucket in grouped.values()),
        "sheet_count": len(grouped),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summary": summary, "groups": list(grouped.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

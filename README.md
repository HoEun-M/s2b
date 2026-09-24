# S2B 수의계약 크롤러

학교장터(S2B)의 수의계약 공고를 키워드로 수집해 누적 JSON을 만들고, 대시보드 HTML로 렌더링해 GitHub Pages에 배포하는 도구 모음입니다.

## 전체 흐름

```
S2B 목록 페이지 (EUC-KR POST)
      ↓ 키워드 검색 + 제외어 필터
   레코드 파싱 (계약명/기관/금액/체결일)
      ↓ NEIS schoolInfo 조회
   지역 · 교육지원청 · 세부지역 추론
      ↓ id 기준 upsert
   s2b_cumulative.json  (누적 데이터)
      ↓ build_cumulative_html
   index.html / s2b_cumulative.html  (대시보드 + 상세 내역)
      ↓ git add / commit / push
   GitHub (https://github.com/HoEun-M/s2b)
      ↑ 브라우저에서 로드 시
   Supabase (지역·삭제·학교구분 수정 이력 반영)
```

## 실행 파일

| 파일 | 용도 |
| --- | --- |
| `s2b_local_crawler.py` | 파이프라인 본체. requests 기반 수집 + 지역 추론 + HTML 생성 + GitHub 업로드. 다른 스크립트가 모두 이 모듈을 import 합니다. |
| `s2b_browser_crawler.py` | Playwright로 실제 Chrome/Edge 창을 띄워 수집. CAPTCHA가 자주 뜰 때 사용합니다. |
| `s2b_auto_previous_crawler.py` | 공휴일 API로 직전 영업일을 계산해 자동 수집. 키워드를 5개 그룹으로 분할합니다. |
| `s2b_auto_previous_group1~5.py` | 위 그룹 1~5 실행 래퍼. PyInstaller로 exe를 만들 때의 진입점입니다. |
| `s2b_admin.py` | 로컬 관리 서버(기본 http://127.0.0.1:8765). 지역·학교급·키워드 수동 수정, 레코드 삭제, Supabase 동기화. 저장 시 `outputs/admin_backups/`에 자동 백업합니다. |

### 사용 예

```bash
# 기간 지정 수집 (전체 키워드)
python s2b_local_crawler.py --from 20260901 --to 20260905

# 특정 키워드만, GitHub 업로드 없이
python s2b_local_crawler.py --from 20260901 --to 20260905 --keywords 국어,수학 --no-github-upload

# CAPTCHA가 잦을 때: 브라우저 모드
python s2b_browser_crawler.py --from 20260901 --to 20260905 --browser chrome

# 직전 영업일 자동 수집 (그룹 1)
python s2b_auto_previous_group1.py

# 관리 화면
python s2b_admin.py
```

주요 옵션은 `--keywords`, `--batch-size`/`--batch-index`(키워드 분할 실행), `--page-delay-min/max`, `--keyword-delay-min/max`, `--backfill-excluded`(제외어 때문에 빠졌던 계약 재수집), `--no-github-upload` 입니다.

### 기간 분할 · 이어서 수집 · 수집 원장

긴 기간은 자동으로 7일 조각(`--chunk-days`, 0이면 분할 없음)으로 나눠 **조각 × 키워드** 단위로 수집합니다. 페이지를 받을 때마다 `outputs/checkpoints/`에 체크포인트를 남기고, 조각이 끝날 때마다 `crawl_ledger.json`(수집 원장)에 완료/부분/CAPTCHA/오류 상태를 기록합니다.

- 중간에 멈추면(CAPTCHA, 네트워크 오류, 강제 종료) **같은 명령을 다시 실행**하세요. 완료된 조각은 건너뛰고, 미완료 조각은 마지막 페이지부터 이어서 수집합니다.
- 완료됐지만 누적 파일에 반영되기 전에 프로그램이 죽은 조각은 다음 실행에서 자동으로 합쳐집니다.
- 요청 오류는 30초 → 2분 → 5분 간격으로 3회 재시도한 뒤 `error`로 기록하고 다음 조각으로 넘어갑니다.

```bash
# 키워드별로 어느 기간까지 수집이 끝났는지 확인
python s2b_local_crawler.py --coverage

# 원장에 완료 기록이 있어도 무시하고 다시 수집
python s2b_local_crawler.py --from 20260901 --to 20260930 --recrawl
```

브라우저 크롤러와 자동전일 exe도 같은 원장·체크포인트를 사용합니다.

### 수집 방식: 엑셀 내보내기 (기본)

S2B 목록 화면에서 버튼은 주석 처리돼 있지만 서버의 엑셀 내보내기(`forwardName=list03Excel&excelSection=Y`)는 살아 있습니다. 기본 `--mode excel`은 검색어·조각당 **요청 2건**(전체 + `estimate_kind=3`)으로 페이지 수와 무관하게 전부 받습니다. 예: `AI` 19일치 681건 = 페이지 방식 35요청 → 2요청. 응답은 HTML 테이블을 `.xls`로 위장한 형식이라 `parse_excel_export`로 읽습니다.

- 두 번째 요청이 필요한 이유: 상세 링크의 `forwardName`(`view03_2`/`view03_1`)은 `estimate_kind`가 3인지에 따라 갈리는데 엑셀에는 그 값이 없습니다. `estimate_kind=3`으로 한 번 더 받아 두 집합을 나누면 링크가 정확히 복원됩니다(실제 응답으로 검증).
- 엑셀 응답이 오지 않으면(엔드포인트가 막히면) 그 구간만 자동으로 페이지 방식으로 떨어집니다. `--mode pages` 또는 환경변수 `S2B_FETCH_MODE=pages`로 예전 방식을 강제할 수 있습니다.
- 브라우저 모드는 브라우저 세션 쿠키로 같은 요청을 보냅니다. CAPTCHA가 오면 목록 페이지를 띄워 사람이 풀게 한 뒤 다시 요청합니다.
- 원장의 `mode`에 `requests-excel` / `browser-excel` / `*-pages`로 어떤 방식으로 받았는지 남습니다.

### 검색어 축소

S2B 검색은 계약명 부분 문자열 검색이라 `수학` 한 번이면 `토도수학`·`수학대장` 계약까지 모두 돌아옵니다(누적 데이터로 검증). 그래서 요청 키워드를 최소 검색어 집합으로 묶어 검색하고(`plan_search_terms`), 매칭 키워드 태그는 계약명에 들어 있는 모든 `KEYWORDS`를 로컬에서 판정해 붙입니다. 전체 30개 키워드는 26개 검색어로, 자동전일 5번 그룹은 10개→7개로 줄어듭니다. 키워드가 아닌 좁은 검색어(`천재`, `토도`)는 `EXTRA_SEARCH_TERMS`에 등록돼 있습니다. 원장에는 검색어가 덮은 키워드마다 기록되므로 `--coverage`는 그대로 키워드 단위입니다.

## 데이터 파일

| 파일 | 설명 |
| --- | --- |
| `s2b_cumulative.json` | 누적 레코드 원본. id 기준 upsert, `first_imported_at`/`import_count` 유지. |
| `crawl_ledger.json` | 수집 원장. 키워드 × 기간 조각별 상태(`complete`/`partial`/`captcha`/`error`), 페이지 수, 건수, 완료 시각. `--coverage`가 읽는 파일입니다. |
| `outputs/checkpoints/*.json` | 조각별 페이지 단위 체크포인트. 누적 파일에 반영되면 자동 삭제되고, 미완료분만 남습니다. |
| `index.html`, `s2b_cumulative.html` | 생성된 대시보드(업체별·월별·시도별·학교급별·구매유형별) + 상세 내역. 내용은 동일합니다. |
| `school_type_mapping.json` | 선도/연구/중점 학교 매핑. |
| `region_overrides.json`, `deleted_records.json` | 수동 보정 이력의 로컬 사본. 정본은 Supabase입니다. |
| `supabase_setup.sql` | Supabase 테이블(`region_overrides`, `deleted_records`, `school_type_overrides`) 및 RLS 정책. |

## 나라장터(G2B) 수집기 — `g2b_api_crawler.py`

공공데이터포털의 조달청 오픈API로 **교육기관의 입찰공고 · 낙찰 · 계약**을 받습니다. 학교장터가 2천만 원 이하 소액 수의계약 위주라면, 나라장터에는 그보다 큰 건과 경쟁입찰이 있습니다. 인증키는 공휴일 API와 같은 data.go.kr 키(`S2B_DATA_GO_KR_KEY` 환경변수로 덮어쓰기)이고, 입찰공고정보·계약정보·낙찰정보 3개 서비스에 활용신청이 되어 있어야 합니다(개발계정 서비스당 1,000회/일).

| 소스 | 오퍼레이션 (실제 응답으로 확정) | 기준일 | 필터 |
| --- | --- | --- | --- |
| 계약 | `ao/CntrctInfoService/getCntrctInfoList{Thng,Servc,Cnstwk}` | 등록일시(`inqryDiv=1`) | 서버 필터 없음 → 전체(하루 물품 약 2,700·용역 5,400·공사 4,500건)를 999건씩 받아 로컬에서 교육기관(`cntrctInsttJrsdctnDivNm=교육기관` 또는 기관명) 선별 |
| 입찰공고 | `ad/BidPublicInfoService/getBidPblancListInfo{…}PPSSrch` | 공고게시일시(`inqryDiv=1`) | `dminsttNm=교육청` 서버 필터 (하루 약 200건) |
| 낙찰 | `as/ScsbidInfoService/getScsbidListSttus{…}PPSSrch` | 개찰일시(`inqryDiv=2`) | `dminsttNm=교육청` 서버 필터 (하루 약 120건) |

- 하루치 전체가 요청 약 20건. 원장에는 `kind=g2b:<source>`, `search_term=<업무구분>`으로 기록되며 완료된 날은 건너뜁니다(`--recrawl`로 무시).
- 교육기관 건 중 학교장터와 같은 키워드/제외어에 걸리는 것만 저장합니다. 하루 3,500건 중 10~20건 수준으로 적은데, 에듀테크 소액 건은 대부분 학교장터로 가기 때문입니다. `--all-edu`를 주면 교육기관 건 전체를 저장합니다.
- 계약 레코드의 계약방법(`cntrctCnclsMthdNm`)으로 수의계약 여부를 판정합니다. 나라장터에는 수의계약 전용 서비스가 없습니다.
- 결과는 `g2b_cumulative.json`에 쌓이고, **통합 페이지 `index.html`** 에 학교장터 레코드와 함께 실립니다. 왼쪽 LNB 메뉴 구조는 **대시보드(통합 / S2B 학교장터 / G2B 나라장터) → 세부내역(통합 / S2B 세부내역 / G2B 세부내역 → 입찰공고 · 계약 · 낙찰)** 이며, 대시보드 막대를 클릭하면 같은 출처의 세부내역으로 드릴다운됩니다. 대시보드 금액은 계약 건만 집계하고(입찰공고·낙찰 제외), 나라장터 행은 이름 앞에 `나라장터 · 계약 · 수의계약` 배지가 붙습니다. 마지막에 보던 메뉴는 브라우저에 기억됩니다. 입찰공고의 마감·개찰일, 낙찰의 투찰률·참가수 같은 나라장터 고유 컬럼은 별도 페이지 `g2b.html`에서 봅니다. 화면 폭 900px 이하에서는 LNB가 상단 가로 메뉴로 접힙니다.
- 학교장터 크롤러와 나라장터 수집기 어느 쪽을 실행해도 `index.html`이 두 누적 파일을 읽어 다시 만들어지므로 항상 최신 통합본이 올라갑니다.
- 호출 수는 `outputs/g2b_call_log.json`에 서비스별·일별로 기록되고 800회를 넘기면 그날 수집을 멈춥니다. 속도 제한(오류 22/23)은 60초 후 재시도합니다.

```bash
python g2b_api_crawler.py                                # 전 영업일, 세 소스 전부
python g2b_api_crawler.py --from 20260901 --to 20260930  # 기간 지정 (하루 약 20회 × 일수)
python g2b_api_crawler.py --sources contract --categories 물품,용역 --all-edu
python g2b_api_crawler.py --coverage
python g2b_auto_previous.py                              # 스케줄러/exe용 진입점
```

## 지역 보정 스크립트

지역이 비어 있는 레코드를 단계적으로 채우는 일회성 도구들입니다. 위쪽일수록 가볍고 먼저 돌립니다.

1. `fill_regions_from_existing_text.py` — 이미 가진 사업장 주소·학교명 텍스트로 채움 (네트워크 없음)
2. `fill_missing_regions_from_names.py` — 교육지원청 별칭·구 지명(마산/진해 등) 규칙으로 채움
3. `fill_subregions_from_neis.py` — NEIS 학교 정보로 세부지역 채움
4. `auto_fill_regions_from_business_place.py` — S2B 상세페이지를 다시 조회해 사업장 주소로 채움 (가장 느림)

`build_region_group_dataset.py`는 영업 담당 구역 묶음 데이터셋을 생성합니다.

## 요구 사항

```bash
pip install requests beautifulsoup4 lxml
pip install playwright && python -m playwright install chromium   # 브라우저 모드용
```

### CAPTCHA 처리

- **브라우저 모드**: CAPTCHA가 뜨면 Windows 토스트 + 비프음으로 알리고, 브라우저 창에서 사람이 풀면 **자동으로 감지해 계속**합니다(Enter 입력 불필요). 기본 15분(`--captcha-wait`, 환경변수 `S2B_CAPTCHA_WAIT`, 0이면 무제한) 안에 안 풀리면 체크포인트를 남기고 실행을 접습니다. 다음 실행이 그 페이지부터 이어받습니다.
- **requests 모드**: 10~30분 한 번 쉬었다 재시도하고, 그래도 막히면 남은 작업을 건너뛰고 지금까지 결과를 저장합니다. 같은 IP로 계속 두드려 봐야 나머지도 CAPTCHA라서요.
- 환경변수 `S2B_NOTIFY_WEBHOOK`에 Slack/Discord incoming webhook URL을 넣으면 토스트와 함께 그쪽으로도 알림이 갑니다. 자리에 없을 때 유용합니다.

## 주의

- 차단 방지를 위해 페이지당 18~35초, 키워드당 20~45초 대기합니다. 딜레이를 줄이면 CAPTCHA가 뜹니다.
- 공휴일 API 키와 Supabase 키가 소스에 하드코딩되어 있습니다. `s2b_admin.py`는 `S2B_SUPABASE_URL` / `S2B_SUPABASE_KEY` 환경변수로 덮어쓸 수 있고, 대시보드 HTML은 브라우저 localStorage 값으로 덮어씁니다.
- 수집이 끝나면 기본적으로 GitHub에 자동 push 합니다. 원치 않으면 `--no-github-upload`를 붙이세요.

# MNO Device Sales Dashboard — Session Memory

새 Claude Code 세션이 이 파일을 읽고 즉시 컨텍스트를 잡도록 작성. (mno-ltv-monitor 패턴 자매 프로젝트)

---

## 0. Polaris Colab MUST (강제 규칙)

- 서버 `0.0.0.0:8080` 리슨, `GET /health` → 200 필수
- 파일 쓰기는 `/tmp`만 (재시작 시 소멸), 시크릿은 env로만 (하드코딩 금지)
- 데이터 접근: **Polaris Data Gateway**(auth_key 인증)로 `SELECT`만. startup 1회 조회 후 메모리 캐시 (요청마다 호출 금지). 테이블은 `database.table`. output location/AWS 자격증명 불필요(Gateway가 처리)

## 1. 개요

- 이름: **MNO Device Sales Dashboard** (단말 판매량 본사 관점 모니터링)
- 목적: 전사 + 본부별 + SKU별 단말 판매 분포 / 과·과소 센싱
- 톤: 본사 임원/팀장용. **라이트/다크 테마 둘 다 지원** (우상단 🌙/☀️ 토글, `<body data-theme>`, localStorage persist)
- **디자인: MNO SYNAPSE Design System 준거** (`~/Downloads/design_guide1.html`) — SKT 보라 `#3617CE`(다크 `#7E68FF`) 포인트 전용, near-white 중립 캔버스(`#FAFBFD`), Pretendard + JetBrains Mono(수치). 규칙: 색은 문제/포커스에만 · 카드 좌측 컬러바 금지 · 활성 칩은 brand-soft(면 채움 X) · 장식 이모지 자제. CSS 변수명은 유지하되 값만 SYNAPSE 토큰으로 매핑.
  - **단말군 색(사용자 확정, 무지개-금지 예외)**: 실단말 6종은 뚜렷한 고정색, SIMonly·기타(Etc)는 중립 회색. `frontend`의 `GCOLOR_LIGHT/DARK`(S26 파랑·IP17 초록·A17 앰버·Wide8 보라·ZFlip7 핑크·ZFold7 빨강). `assignColors`가 단말군명→고정색 매핑(미지값은 fallback). ⚠️ 되돌리지 말 것.
  - **단말군 라벨**: `GLABEL`/`glabel()`로 표시명 한글화(SIMonly군/S26군/IP17군/A17군/와이드8군/Z플립7군/Z폴드7군/기타). 전 탭 공통(알림 메시지는 백엔드 문자열이라 예외).
- 자매 레퍼런스: `~/mno-ltv-monitor` (동일 스택·배포 패턴)

## 2. 배포

- **Repo (듀얼 remote)**:
  - `origin` (GitHub 미러): `https://github.com/jenn25ng/jy-mno-device-sales.git`
  - `gitlab` (사내): `https://gitlab.tde.sktelecom.com/CDS/orbit/colab/user-apps/mno-device-sales.git`
- **Polaris URL**: `https://mno-device-sales.colab-mydesk.sktelecom.com` (배포환경 mydesk)
- **스택**: Python 3.12 / FastAPI 0.111 / Uvicorn / 단일 HTML SPA / Docker(python:3.12-slim)

## 3. 환경변수 (Polaris ENV_VARS) — Data Gateway 메모리 캐시 (소문자 권장)

- **필수 4종**: `auth_key`, `user_id`, `app_name`, `database`(기본 `obt_encore_max`). + 테이블 `MART_TABLE_NAME`(기본 `device_sales_summary_daily2`) 또는 `SOURCE_TABLE=db.table`. Gateway URL `DATA_GATEWAY_URL`(기본값 있음).
- **output location/AWS 자격증명 불필요** — Gateway가 자기 workgroup·결과버킷으로 Athena 실행 후 결과를 API로 반환. (md `DATA_GATEWAY_VIBE_GUIDE.md` 참고)
- **선택**: `DATA_WINDOW_MONTHS`(기본 13), `ADMIN_TOKEN`, `FRONTEND_ORIGIN`, `USE_MOCK`
- **mock 모드**: `auth_key` 미설정 또는 `USE_MOCK=1` → Gateway 미호출, mock DataFrame

## 4. 데이터 소스 — 마트가 이미 사전 집계 완료 ⭐

- **접근 ⭐ 메모리 캐시 패턴**: startup에 `backend.data.load_mart()`가 **Polaris Data Gateway(`data_gateway.DataGatewayClient.run_query`)로 마트 전체를 1회 조회 → pandas DataFrame 메모리 보관**(`_CACHE`). 모든 endpoint는 `get_df()`로 메모리를 pandas 집계 → **Gateway 재호출 없음**. auth_key 인증, output location 불필요. `auth_key` 없거나 `USE_MOCK=1`이면 자동 mock DataFrame. (ltv-monitor와 동일 패턴. awswrangler 직접 Athena는 폐기 — Polaris 표준 경로는 Gateway.)
- **윈도우**: 최근 **13개월**(`DATA_WINDOW_MONTHS` 기본 13). 앱이 조회 SQL에 `WHERE exec_ym >= '(오늘-12개월)'` 파티션 필터를 직접 걸어 13개월만 가져옴(마트가 24개월 보유해도 앱은 13개월). mock도 13개월 생성.
- **마트**: `obt_encore_max.device_sales_summary_daily2` — **56 컬럼, 일별 그레인, 파티션키 `exec_ym`**. 스키마: `~/Downloads/MNO_device_sales_컬럼한글명.md`, SQL: `MNO_device_sales_summary_SQL.md`(v3.3, NULL 안전).
- 마트가 차원을 **이미 계산**해 둠 → 앱에서 eqp_series 매핑 불필요:
  - 조직: `mkt_div_org_cd/nm` (본부) · 단말: `device_group`, `sub_model`, `storage`, `mfact`, `sim_only`
  - 가입: `scrb_type`(MNP/기변/신규/010신규), `agree_type` · 채널: `chnl_l/m` · 기타: `comb_gubun`, `fee_group`, `device_tier`
  - 메트릭: `sales_cnt`(핵심), `subscriber_cnt`, `agency_cnt`, 비용/지원금 합계·평균, `ltv_sum/avg` 등
  - 예약 확장: `ext_dim_1~3`, `ext_metric_1~5` (초기 NULL, 룰 변경 시 SQL만 수정)
- 원천(참고): `di_crowd.policy_log_daily` × `di_crowd.mno_eqp_mdl_meta` (join `eqp_mdl_cd`) — 마트가 이걸 집계한 것.

## 5. 단말군 8종 — 마트 `device_group` 실제 값과 동일

`SIMonly` / `S26` / `IP17` / `A17` / `ZFlip7` / `ZFold7` / `Wide8` / `Etc`
- `SIMonly`는 `sim_only` 컬럼으로도 식별. SKU 탭 보유: **S26, IP17** (`SKU_MAP` = sub_model×storage)
- `data_pipeline.DEVICE_GROUPS` 가 이 값과 일치하도록 정렬 완료(Phase A). 실제 sub_model 변형은 Phase B에서 마트 distinct로 확정.

## 6. 본부 9개 (판매 본부 — 표시 순서 고정)

수도권 · 부산 · 대구 · 서부 · 중부 · PS&M · 제휴 · 기업사업본부 · TDS
- `data.HQS` 순서 = 전 탭 표시 순서(`aggregate._order`가 이 순서로 정렬).
- **본부명 정규화(접두 매칭)**: 실마트는 `수도권마케팅본부`/`부산 마케팅본부`처럼 접미사·띄어쓰기 변형으로 옴 → `data._canon_hq`가 HQS **접두로 매칭**해 정규화(수도권/부산/…). 정확 접미사에 의존 안 함 → 표기 변형에 견딤.
- **판매 외 조직 제거**: `#`/`Blank`/`CV추진실(가상)`/`Channel&Device담당`/`Connectivity사업`/`Product&Brand본부` 등 비판매·가상 조직은 `data._filter_hqs`가 적재 직후 HQS 화이트리스트로 전부 제외(제외 내역 로그).

## 7. 메트릭

- 판매건수(row count), 본부내비율, 전사비중, 본부간비중
- **과/과소 지수 = 본부내비율 − 전사비중** (양수=초록=과다, 음수=빨강=과소)

## 8. UI — 6 탭 (라이트/다크 테마, CSS 변수 토큰화)

**전역 날짜 컨트롤바(탭 위, `#ctrlbar`)** — **기간(rangeStart~rangeEnd)이 전 탭 전역**. 바꾸면 `loadPeriod()`가 `/api/overview`(전사개요)+`/api/brief`(나머지) 둘 다 재조회 → 모든 탭 반영. **[일별|기간별] 세그먼트**: 일별=단일 달력, 기간별=시작~종료 2칸(역순 자동보정). 빠른선택[어제/당월누적/전월]은 **데이터 최신일(dataMax) 기준**(어제=최신일). 기본 날짜=데이터 max(init에서 brief 적재 후 loadStatus 재호출로 재동기). 비교 힌트: 다른해면 'YY 표기. **비교·가입유형은 전사 개요 탭에서만**, SIMonly는 전사개요는 도넛 토글·타 탭은 컨트롤바.

1. **전사 개요** — (위 컨트롤바 +) 비교[없음/전일/전주동요일/전월동기간/작년동기간] · **가입유형 필터(전 탭 공통)[전체/신규/MNP 전체/MNO/MVNO/기기변경, 기본 전체]** — `MNP 전체(MNP_ALL)`=MNOMNP+MVNOMNP 합산. `aggregate._scrb_set`가 선택값→scrb_type 집합 매핑(alias로 mock 레거시 MNP/기변도 흡수), `/api/brief`·`/api/overview` 모두 `scrb_type` 반영. 구성: KPI(총 판매 + Top3 단말군 색 랭크, 델타 ▲녹/▼적) / **비교 하이라이트**(비교≠없음일 때, 시장(전체) 증감률 대비 상회/하회 큰 단말군·본부·가입유형 뱃지 — delta.by_group/by_hq/by_scrb 기반) / **도넛(단말군별 비중)+범례+SIMonly토글**(SIMonly OFF 시 해당 군을 범례에 '—'로 유지) / **본부별 100% 세로 누적**(상단 범례+y축) / **단말군×본부 요약표**(셀=건수·본부내비중, 전사합/합계). 데이터=`/api/overview?period_start&period_end&compare_to&scrb_type`. (구 일별추이·가입유형별 막대 패널은 목업 재구성 시 제거)
2. **본부별 분석** — 상단 **로컬 본부 필터**(칩, **전체**+정렬=HQS, 건수 표시. 전체=전사 합산, 비교 패널 생략) / KPI(본부 총 MNP·#1 단말군·S26/IP17/SIMonly) / **포트폴리오 도넛**(+SIMonly토글·범례 클릭토글) / **전사 vs 본부 비중 비교**(over/under-index) / **SKU 드릴다운**(단말군 상세표 행 클릭→선택 단말군의 세부 SKU. `STATE.drillGroup`, build_brief가 전 단말군 SKU 계산) / **단말군 상세표**(본부내비율·전사비중·본부간비중·과과소). 데이터=`/api/brief`(전역 기간)
3. **본부 매트릭스** — 본부×단말군 히트맵(`heat2`). 셀=판매건수+본부내비율%, 배경=단말군 색 color-mix 틴트(강도∝본부내비율), 컬럼=단말군 색 dot, 행=본부 색 dot(`HQPAL`), 합계 열+전사합 행(단말군 색 숫자). 툴팁·셀 등장 애니메이션
4. **알림** — 룰 기반(LLM 미사용, `aggregate.build_alerts`). 4 KPI(전체/긴급/주의/정보) · **일별 판매 추이(알림 발생일 색 강조)** · 카테고리 필터칩(전체/판매량/본부별/단말군/S26군/IP17군/SIM/SKU) · 리치 카드(레벨·태그·제목·지표·**템플릿 문구**·일자). 룰: 판매량 전일대비 급증/급감(기여 상위 본부 문구 조립)·본부 과소/과다·SKU 편중·단말군 최상위. 문구는 트리거 차원을 템플릿에 채워 조립(진짜 자연어는 배치-LLM 옵션). `alerts[]`+`alert_daily[]`. 탭 배지=긴급+주의
5. **S26 SKU** — KPI / SKU별 막대 / SKU×본부 상세표 (달력 '월' 기준 `/api/brief?exec_ym`)
6. **IP17 SKU** — 동일 구조

> 탭 순서: 전사 개요 · 본부별 분석 · 본부 매트릭스 · 알림 · S26군 SKU · IP17군 SKU (`frontend/index.html` `TABS`)

> 실시간은 별도 탭이 아니라 전사개요 빠른선택 칩(⚡실시간=데이터 최신일로 이동). SIMonly 필터·기준월(YYYYMM 입력) 전역 컨트롤은 제거됨.

## 9. 핵심 파일

| 경로 | 역할 |
|---|---|
| `backend/data.py` | **메모리 캐시** — `_CACHE` · `load_mart()`(Gateway 실조회 + mock DataFrame) · `get_df()` · `refresh()` · `cache_meta()` · `diagnostics()` |
| `backend/data_gateway.py` | Polaris Data Gateway 클라이언트(auth_key, start→poll→results) — ltv-monitor 재사용 |
| `backend/aggregate.py` | `build_brief(df, start, end)` — 기간 슬라이스로 6탭 JSON. `meta.unknown_groups`=CANON 외 device_group(신규단말 감지) |
| `backend/main.py` | FastAPI: `/api/brief?period_start&period_end&scrb_type`(전 탭·기간) · `/api/overview?period_start&period_end&compare_to&scrb_type`(전사개요+비교) · status/diagnostics/refresh + SPA mount |
| `frontend/index.html` | 단일 SPA (6탭 전체 UI, 라이트 기본 + 🌙/☀️ 토글) |

> 데이터 계층: **Polaris Data Gateway + 메모리 캐시**(auth_key, output location 불필요). awswrangler 직접 Athena는 폐기.

## 10. Phase 진행

- **A (완료/진행)**: init + git 듀얼 remote + FastAPI/SPA scaffold + Docker + mock 동작
- **B**: 데이터 layer — 실제 `eqp_series_nm` 분포 SQL 결과로 단말군 매핑 확정, `_build_query` 실제 컬럼 확정
- **C**: 백엔드 endpoint — `fetch_rows`를 실제 gateway 행에 맞춤 (build_brief는 행 스키마만 맞으면 재사용)
- **D**: 프론트 6탭 차트 정교화 (도넛 등 — Chart.js 여부 결정)
- **E**: 알림 룰 + 메타 정의 확정
- **F**: 사내 GitLab + Polaris 배포

## 11. 작업 원칙 (karpathy-guidelines)

- Think Before Coding (모호하면 질문, 가정 명시) / Simplicity First / Surgical Changes / Goal-Driven (step별 verify 기준)

## 12. 행 스키마 (build_brief 입력 계약)

`{exec_ym, hq, device_group, sku, sim_only(bool), sales_cnt(int)}` — Phase C에서 gateway SELECT가 이 형태를 내도록 매핑.

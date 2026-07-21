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
  - **단말군 색(사용자 확정, 볼륨 가중형 · 무지개-금지 예외)**: 판매량이 클수록 선명·작을수록 톤다운. 상태색(빨강/앰버/초록)과 카테고리 색 충돌 회피. `frontend`의 `GCOLOR_LIGHT/DARK` — S26 `#4374C4`(블루, 상위·살짝 톤다운)·IP17 `#8360CC`(바이올렛, 상위)·와이드 `#0891B2`(시안)·폴더블7 `#C05B94`(로즈, 핑크 순화)·A17 `#CB7B5B`(테라코타=웜톤, 블루 밸런스)·퀀텀6 `#5CA8A0`(소프트틸)·스타일폴더2 `#9E8FC9`(라벤더)·SIMonly `#8C93A8`·기타 `#C3C8D4`(중립 회색, 유지). `assignColors`가 단말군명→고정색 매핑(미지값은 fallback). ⚠️ 임의로 되돌리지 말 것.
  - **상태색 토큰**: 긴급/문제 `--red #EA002C`(SKT 시그널 레드)·정상/과다 `--green #0E9F6E`·주의 `--warn #F59E0B`(앰버). 매트릭스 히트맵 틴트도 이 red/green RGB와 동기화됨. MNO SYNAPSE/MAMF v3.1 가이드 정렬.
  - **단말군 라벨**: `GLABEL`/`glabel()`로 표시명 한글화(S26군/IP17군/폴더블7군/퀀텀6군/와이드군/A17군/스타일폴더2/SIMonly군/기타). 전사 개요·본부 매트릭스는 `GORDER` 고정 순서(**S26→S25→IP17→IP16→폴더블7→퀀텀6→와이드→A17/16→스타일폴더2→SIMonly→기타**). ⚠️ `DEVICE_GROUPS`(백엔드 canonical)도 GORDER와 동일 순서로 유지. 전 탭 공통(알림 메시지는 백엔드 문자열이라 예외).
- 자매 레퍼런스: `~/mno-ltv-monitor` (동일 스택·배포 패턴)

## 2. 배포

- **Repo (듀얼 remote)**:
  - `origin` (GitHub 미러): `https://github.com/jenn25ng/jy-mno-device-sales.git`
  - `gitlab` (사내): `https://gitlab.tde.sktelecom.com/CDS/orbit/colab/user-apps/mno-device-sales.git`
- **Polaris URL**: `https://mno-device-sales.colab-mydesk.sktelecom.com` (배포환경 mydesk)
- **스택**: Python 3.12 / FastAPI 0.111 / Uvicorn / 단일 HTML SPA / Docker(python:3.12-slim)

## 3. 환경변수 (Polaris ENV_VARS) — Data Gateway 메모리 캐시 (소문자 권장)

- **필수 4종**: `auth_key`, `user_id`, `app_name`, `database`. + 테이블 `MART_TABLE_NAME`(기본 `device_sales_summary_daily3`) 또는 `SOURCE_TABLE=db.table`. Gateway URL `DATA_GATEWAY_URL`(기본값 있음).
- ⭐ **DB(database) 흐름**: 마트 테이블은 **최초 `sandbox_db_max`**(내 샌드박스, 3개월마다 초기화)에 생성/개발 → **데이터 자산화** 후 **상용 `obt_encore_max`**로 이관. 앱 env `database`도 그 시점에 `sandbox_db_max`→`obt_encore_max` swap. (SQL 3파일도 DB명만 동일 swap. 코드 기본값은 `obt_encore_max`=최종상태)
- **output location/AWS 자격증명 불필요** — Gateway가 자기 workgroup·결과버킷으로 Athena 실행 후 결과를 API로 반환. (md `DATA_GATEWAY_VIBE_GUIDE.md` 참고)
- **선택**: `DATA_WINDOW_MONTHS`(기본 13), `ADMIN_TOKEN`, `FRONTEND_ORIGIN`, `USE_MOCK`
- **mock 모드**: `auth_key` 미설정 또는 `USE_MOCK=1` → Gateway 미호출, mock DataFrame

## 4. 데이터 소스 — 마트가 이미 사전 집계 완료 ⭐

- **접근 ⭐ 메모리 캐시 패턴**: startup에 `backend.data.load_mart()`가 **Polaris Data Gateway(`data_gateway.DataGatewayClient.run_query`)로 마트 전체를 1회 조회 → pandas DataFrame 메모리 보관**(`_CACHE`). 모든 endpoint는 `get_df()`로 메모리를 pandas 집계 → **Gateway 재호출 없음**. auth_key 인증, output location 불필요. `auth_key` 없거나 `USE_MOCK=1`이면 자동 mock DataFrame. (ltv-monitor와 동일 패턴. awswrangler 직접 Athena는 폐기 — Polaris 표준 경로는 Gateway.)
- **윈도우**: **2025-01부터 고정**(`DATA_START_YM` 기본 `202501`, 프론트 `MIN_DATE`=2025-01-01과 정합). `_window_start_ym()`=min(롤링 13개월, DATA_START_YM), `_window_yms()`가 시작월~이번 달 전체를 파티션 단위로 분할 조회. 배치 SQL도 `proc_ym >= '202501'`. (구: 롤링 13개월 `DATA_WINDOW_MONTHS`)
- **마트**: `obt_encore_max.device_sales_summary_daily3` — **56 컬럼, 일별 그레인, 파티션키 `exec_ym`**. 스키마: `~/Downloads/MNO_device_sales_컬럼한글명.md`, SQL: `MNO_device_sales_summary_SQL.md`(v3.3, NULL 안전).
- 마트가 차원을 **이미 계산**해 둠 → 앱에서 eqp_series 매핑 불필요:
  - 조직: `mkt_div_org_cd/nm` (본부) · 단말: `device_group`, `sub_model`, `storage`, `mfact`, `sim_only`
  - 가입: `scrb_type`(MNP/기변/신규/010신규), `agree_type` · 채널: `chnl_l/m` · 기타: `comb_gubun`, `fee_group`, `device_tier`
  - 메트릭: `sales_cnt`(핵심), `subscriber_cnt`, `agency_cnt`, 비용/지원금 합계·평균, `ltv_sum/avg` 등
  - 예약 확장: `ext_dim_1~3`, `ext_metric_1~5` (초기 NULL, 룰 변경 시 SQL만 수정)
- **원천(현행) ⭐**: `midp_mos.wl_rslt_f` (회선 실적 팩트, MAMF 원천) — 배치 SQL이 이걸 집계해 마트 생성. (구 `di_crowd.policy_log_daily`는 대체됨)
  - **배치 SQL**: `sql/device_sales_summary_daily3_from_wl_rslt_f.sql` (`DELETE + INSERT INTO`, 최근 13개월, Trino/Athena). 앱 데이터층(Gateway 메모리캐시)은 무변경 — 마트 스키마 그대로라 앱은 손 안 댐.
  - **필터 = (구)H/S 실적**: 데함쓰·특수단말·2nd디바이스·태블릿 제외 → `data_shr_cd='1' AND spcl_eqp_cl_nm='1' AND tblt_exclsv_cl_cd='1' AND second_device_nm='1'` (플래그 1=유지/2=제외). ⚠️ `old_yn`은 "구형단말"이라 필터에 쓰지 말 것(신형 판매 날아감).
  - **sales_cnt** = `new_010_rslt_cnt + mnp_in_rslt_cnt + eqp_chg_rslt_cnt`. 행마다 한 컬럼만 값(나머지 NULL) → 각각 SUM 후 합(직접 `a+b+c`는 NULL 전파로 금지) + `CAST(BIGINT)`(소수 DECIMAL).
  - **scrb_type**: 신규 / MNOMNP(`bchg_biz_co_cd IN ('KTF','LGT')`=직영 KT/LGU+) / MVNOMNP(그외 알뜰폰) / 기기변경. (망 컬럼 `bchg_biz_co_net_cl_cd`는 KT/LGU+/SKT 망이라 MVNO 구분 못 함 — 사업자 코드로 갈라야 함)
  - **판매채널(chnl_l)** ⭐: 원천 `dsnet_chnl_grp_nm`(그룹명: 특판/도매/소매/비즈)을 마트 `chnl_l`에 채움(구 NULL). 배치 SQL이 base→unpiv→agg로 threading, `_FETCH_DIMS`에 `chnl_l` 추가·전역 드롭다운 필터(build_overview/brief `channel` 파라미터). ⚠️ **실채널 뜨려면 배치 SQL 재실행 필요**(현 마트 chnl_l=NULL, mock은 동작).
  - **검증 기준**: 2026-05 총 **388,058건** = MAMF 리포트 일치(신규 38,520·MNO 89,014·MVNO 39,078·기변 221,446).

## 5. 단말군 11종 (v3.6 — 기타에서 S25·IP16 분리, A17/16 통합) — 마트 `device_group` 값과 동일

`S26` / `S25` / `IP17` / `IP16` / `Foldable7` / `A17`(라벨 **A17/16**) / `Quantum6` / `Wide` / `StyleFolder2` / `SIMonly` / `Etc`
- ⭐ **v3.6 변경**: 구 Etc에서 **S25**(`%S25%`)·**IP16**(`%아이폰%16%`/`%IP16%`) 신설, **A16을 A17에 통합**(`%A17% OR %A16%`, 코드는 A17 유지·라벨 "A17/16군"). 색: S25 `#6E9BD8`, IP16 `#A98BDB`. (검증: 2026-05 S25 5,051 / IP16 580 / A17 41,297, Etc 9,847로 축소)
- 고가: S26·IP17·Foldable7(Z플립7/폴드7/플립7FE) · 중저가: A17·Quantum6(갤럭시 퀀텀6)·Wide(와이드8/9)·StyleFolder2 · SIMonly · Etc(기타=구세대 등 미분류)
- **device_group 결정 = 배치 SQL의 CASE (`eqp_mdl_petnm_2` 펫네임 기준, wl_rslt_f)**. 패턴(SIMonly 맨 앞): `%S26%`·`%S25%`·`%아이폰%17%`/`%IP17%`·`%아이폰%16%`/`%IP16%`·`%플립7%`/`%폴드7%`·`%퀀텀6%`·`%WIDE%`(영문!)·`%A17% OR %A16%`·`%스타일폴더%`, 나머지 Etc. 신단말은 CASE에 없으면 Etc → 주기적 펫네임 분포 모니터링으로 감지. ⚠️ 배치 CASE 수정 시 프론트 `GCOLOR/GLABEL/GORDER`+`data.DEVICE_GROUPS`+`aggregate._GLABEL` 동반 수정.
- **SIMonly 정의(확장)** ⭐: ①`usim_indpnd_svc_yn='Y'`(유심독립=순수 SIM) ②자급제/타사망(`mdl_factory_nm` LIKE `블랙리스트%`(OMD)·`%(타사)%`·`%(LGU%`·`%(KTF%`·`MVNO%`) ③**중고단말**(`old_eqp_yn='Y'` — 일반 SK단말이라도 중고면 SIMonly). CASE에서 **맨 앞** → `OMD 갤S26`·중고 S26도 S26 아닌 SIMonly로 감. `raw_series_nm`엔 실기기 펫네임 유지(드릴다운 기기명 표시). (현업 확정 룰: 차세대 sim only 쿼리 #46 기준)
- `sub_model`=NULL(변형은 `raw_series_nm`=펫네임에 포함). `storage`=`eqp_mdl_cd` 접미 근사. `ext_dim_1`(가격군)·비용/LTV·`ext_metric_*`는 NULL(앱 미사용).
- 앱: `GCOLOR`/`GLABEL`(폴더블7군/퀀텀6군/와이드군/스타일폴더2)·`DEVICE_GROUPS`·`CANON_GROUPS`·`_GLABEL` 반영 완료. SKU 탭: **S26, IP17**(`SKU_GROUPS`).

## 6. 본부 10개 (판매 본부 — 표시명 = 리포트 라벨)

수도권 · 부산 · 대구 · 서부 · 중부 · PS&M · 제휴 · 기업사업본부 · TDS · AIR서비스
- ⚠️ **마트 org명 ≠ 표시명**. `_HQ_PREFIX`가 접두 매핑: **PS&M=`유통사업부`(53,837), TDS=`MNO AI마케팅`(25,771), AIR서비스=`air서비스본부`(4,253)**. 나머지는 `수도권마케팅담당`처럼 접두 일치.
- **10개 합 = 388,052**(2026-05, ≈전체). `Connectivity사업`·`Product&Brand본부`(각 소량)는 비판매라 제외.
- `_canon_hq(org)`가 org명 → 표시명 반환. 새 org 추가/개편 시 `HQS`+`_HQ_PREFIX`만 수정.
- `data.HQS` 순서 = 전 탭 표시 순서(`aggregate._order`가 이 순서로 정렬).
- **본부명 정규화(접두 매칭)**: 실마트는 `수도권마케팅본부`/`부산 마케팅본부`처럼 접미사·띄어쓰기 변형으로 옴 → `data._canon_hq`가 HQS **접두로 매칭**해 정규화(수도권/부산/…). 정확 접미사에 의존 안 함 → 표기 변형에 견딤.
- **판매 외 조직 제거**: `#`/`Blank`/`CV추진실(가상)`/`Channel&Device담당`/`Connectivity사업`/`Product&Brand본부` 등 비판매·가상 조직은 `data._filter_hqs`가 적재 직후 HQS 화이트리스트로 전부 제외(제외 내역 로그).

## 7. 메트릭

- 판매건수(row count), 본부내비중, 전사비중, 본부간점유비 (⚠️ v3.5 워딩: 구 본부내비율→본부내비중, 본부간비중→본부간점유비, 전 탭 일괄)
- **과다/과소 지수 = 본부내비중 − 전사비중** (양수=초록=과다, 음수=빨강=과소). 표기는 `%`로 통일(구 `p`=%p 폐기)

## 8. UI — 6 탭 (라이트/다크 테마, CSS 변수 토큰화)

**전역 날짜 컨트롤바(탭 위, `#ctrlbar`)** — **기간(rangeStart~rangeEnd)이 전 탭 전역**. 바꾸면 `loadPeriod()`가 `/api/overview`(전사개요)+`/api/brief`(나머지) 둘 다 재조회 → 모든 탭 반영. **[일별|기간별] 세그먼트**: 일별=단일 달력, 기간별=시작~종료 2칸(역순 자동보정). 빠른선택[어제/당월누적/전월]은 **데이터 최신일(dataMax) 기준**(어제=최신일). 기본 날짜=데이터 max(init에서 brief 적재 후 loadStatus 재호출로 재동기). 비교 힌트: 다른해면 'YY 표기. **비교(전역: 전사개요+본부별)·가입유형·판매채널은 전 탭 공통**, SIMonly는 전사개요는 도넛 토글·타 탭은 컨트롤바. **비교=전역 필터**(전일/전주동요일/전월동기간/작년동기간/**직접설정**=기간 직접입력). ⭐ **전일=워킹데이 기준**(end 이전 최근 '운영일'=그날 total 판매 ≥ `WORKDAY_MIN_SALES` 기본 10 → 휴무·공휴일 건너뜀. `aggregate._operating_days`/`_resolve_compare`, 필터 前 전체 total로 판정) — `compare_to`(+custom 시 `compare_start/end`)를 `/api/overview`·`/api/brief` 둘 다 전달. **판매채널**=드롭다운(`chnl_l` 그룹명, 전 탭 공통), **약정유형**=드롭다운(`agree_type` ← 원천 `agrmt_cl_nm`=선택약정/지원금약정/무약정, 판매채널 옆, 전 탭 공통. 배치 SQL threading 완료 — ⚠️ 실값은 배치 재적재 후 반영). **날짜 하한 = 2025-01-01 하드코딩**(`MIN_DATE`, 데이터가 짧아도 선택 가능).

1. **전사 개요** — (위 컨트롤바 +) 비교[없음/전일/전주동요일/전월동기간/작년동기간] · **가입유형 필터(전 탭 공통)[전체/신규/MNP 전체/MNO/MVNO/기기변경, 기본 전체]** — `MNP 전체(MNP_ALL)`=MNOMNP+MVNOMNP 합산. `aggregate._scrb_set`가 선택값→scrb_type 집합 매핑(alias로 mock 레거시 MNP/기변도 흡수), `/api/brief`·`/api/overview` 모두 `scrb_type` 반영. 구성: KPI(총 판매 + Top3 단말군 색 랭크, 델타 ▲녹/▼적) / **비교 하이라이트**(비교≠없음일 때, 시장(전체) 증감률 대비 상회/하회 큰 단말군·본부·가입유형 뱃지 — delta.by_group/by_hq/by_scrb 기반) / **도넛(단말군별 비중)+범례+SIMonly토글**(SIMonly OFF 시 해당 군을 범례에 '—'로 유지) / **본부별 100% 세로 누적**(상단 범례+y축) / **단말군×일자별 추이**(꺾은선, 단말군별 · KPI 직후 배치, `current.daily_group_series`, 높이 226뷰박스). ⚠️ 구 단말군×본부 요약표(`crossTable`)는 제거(함수는 미사용 잔존). 데이터=`/api/overview?period_start&period_end&compare_to(+custom시 compare_start/end)&scrb_type&channel`.
2. **본부별 분석** — 상단 **로컬 본부 필터**(칩, **전체**+정렬=HQS, 건수 표시. 전체=전사 합산, 비교 패널 생략) / KPI(본부 총 MNP·#1 단말군·S26/IP17/SIMonly) / **포트폴리오 도넛**(+SIMonly토글·범례 클릭토글) / **전사 vs 본부 비중 비교**(over/under-index) / **SKU 드릴다운**(단말군 상세표 행 클릭→선택 단말군의 세부 SKU. `STATE.drillGroup`, build_brief가 전 단말군 SKU 계산) / **단말군 상세표**(본부내비중·전사비중·본부간점유비·과과소) / **비교 하이라이트**(전역 비교 활성 시: 본부 총 증감 + 급증/급감 단말군 movers. `by_hq[].movers/total_delta/portfolio[].delta`). 데이터=`/api/brief`(전역 기간·compare·channel)
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

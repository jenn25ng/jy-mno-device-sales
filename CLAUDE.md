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
- **선택**: `DATA_WINDOW_MONTHS`(24), `ADMIN_TOKEN`, `FRONTEND_ORIGIN`, `USE_MOCK`
- **mock 모드**: `auth_key` 미설정 또는 `USE_MOCK=1` → Gateway 미호출, mock DataFrame

## 4. 데이터 소스 — 마트가 이미 사전 집계 완료 ⭐

- **접근 ⭐ 메모리 캐시 패턴**: startup에 `backend.data.load_mart()`가 **Polaris Data Gateway(`data_gateway.DataGatewayClient.run_query`)로 마트 전체를 1회 조회 → pandas DataFrame 메모리 보관**(`_CACHE`). 모든 endpoint는 `get_df()`로 메모리를 pandas 집계 → **Gateway 재호출 없음**. auth_key 인증, output location 불필요. `auth_key` 없거나 `USE_MOCK=1`이면 자동 mock DataFrame. (ltv-monitor와 동일 패턴. awswrangler 직접 Athena는 폐기 — Polaris 표준 경로는 Gateway.)
- **윈도우**: 최근 **24개월** (마트 SQL v3.3에서 `interval '24' month`로 윈도잉됨 → 앱은 `SELECT *`).
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

## 6. 본부 9개

수도권 · PS&M · 제휴 · 부산 · 서부 · 대구 · 중부 · 기업사업본부 · TDS

## 7. 메트릭

- 판매건수(row count), 본부내비율, 전사비중, 본부간비중
- **과/과소 지수 = 본부내비율 − 전사비중** (양수=초록=과다, 음수=빨강=과소)

## 8. UI — 6 탭 (라이트/다크 테마, CSS 변수 토큰화)

**전역 날짜 컨트롤바(탭 위, `#ctrlbar`)** — 모든 탭이 날짜 기준으로 갱신되므로 탭 상단에 위치. 📅달력(기준일=range end, 기본 데이터 최신일) + 빠른선택[**⚡실시간**(최신일로 점프)/어제/당월누적/전월/최근7·30일]. 달력의 '월'이 월간 탭(2~6)에 자동 적용. **비교·SIMonly 그룹은 전사 개요 탭에서만** 표시(다른 탭은 날짜만). 전역 기준월(YYYYMM) 입력 바는 제거됨.

1. **전사 개요** — (위 컨트롤바 +) 비교[없음/전일/전주동요일/전월동기간/작년동기간] · SIMonly[포함/제외]. KPI(델타 ▲녹/▼적) / 단말군 막대 / 본부별 100% 누적. 데이터=`/api/overview?period_start&period_end&compare_to`
2. **S26 SKU** — KPI / SKU별 막대 / SKU×본부 상세표 (달력 '월' 기준 `/api/brief?exec_ym`)
3. **IP17 SKU** — 동일 구조
4. **본부별 분석** — 본부 chips / 포트폴리오 + 과·과소 지수표
5. **알림** — 긴급/주의/정보 3단계 (과·과소 지수 |값| 임계: ≥12 긴급 / ≥8 주의 / ≥5 정보)
6. **본부 매트릭스** — 본부×단말군 히트맵 (셀=본부내 비율%)

> 실시간은 별도 탭이 아니라 전사개요 빠른선택 칩(⚡실시간=데이터 최신일로 이동). SIMonly 필터·기준월(YYYYMM 입력) 전역 컨트롤은 제거됨.

## 9. 핵심 파일

| 경로 | 역할 |
|---|---|
| `backend/data.py` | **메모리 캐시** — `_CACHE` · `load_mart()`(Gateway 실조회 + mock DataFrame) · `get_df()` · `refresh()` · `cache_meta()` · `diagnostics()` |
| `backend/data_gateway.py` | Polaris Data Gateway 클라이언트(auth_key, start→poll→results) — ltv-monitor 재사용 |
| `backend/aggregate.py` | `build_brief(df, exec_ym)` — pandas groupby로 6탭 JSON 생성 |
| `backend/main.py` | FastAPI: startup `load_mart` · `/health` · `/api/health` · `/api/diagnostics` · `/api/status` · `/api/brief?exec_ym`(탭) · `/api/overview?period_start&period_end&compare_to`(전사개요 시점+비교) · `/api/refresh` + SPA mount |
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

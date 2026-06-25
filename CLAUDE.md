# MNO Device Sales Dashboard — Session Memory

새 Claude Code 세션이 이 파일을 읽고 즉시 컨텍스트를 잡도록 작성. (mno-ltv-monitor 패턴 자매 프로젝트)

---

## 0. Polaris Colab MUST (강제 규칙)

- 서버 `0.0.0.0:8080` 리슨, `GET /health` → 200 필수
- 파일 쓰기는 `/tmp`만 (재시작 시 소멸), 시크릿은 env로만 (하드코딩 금지)
- 데이터 접근: Hive 직접 X. Data Gateway API만. `SELECT/WITH/SHOW/DESCRIBE`만. 테이블은 `database.table` 형식

## 1. 개요

- 이름: **MNO Device Sales Dashboard** (단말 판매량 본사 관점 모니터링)
- 목적: 전사 + 본부별 + SKU별 단말 판매 분포 / 과·과소 센싱
- 톤: 본사 임원/팀장용, 다크 테마
- 자매 레퍼런스: `~/mno-ltv-monitor` (동일 스택·배포 패턴)

## 2. 배포

- **Repo (듀얼 remote)**:
  - `origin` (GitHub 미러): `https://github.com/jenn25ng/jy-mno-device-sales.git`
  - `gitlab` (사내): `https://gitlab.tde.sktelecom.com/CDS/orbit/colab/user-apps/mno-device-sales.git`
- **Polaris URL**: `https://mno-device-sales.colab-mydesk.sktelecom.com` (배포환경 mydesk)
- **스택**: Python 3.12 / FastAPI 0.111 / Uvicorn / 단일 HTML SPA / Docker(python:3.12-slim)

## 3. 환경변수 (Polaris ENV_VARS — 소문자 권장)

- **필수(gateway)**: `auth_key`, `user_id`, `app_name`, `database`
- **테이블**: `SOURCE_TABLE=sandbox_db_max.device_sales_summary_daily` (또는 `SUMMARY_TABLE_NAME`)
- **선택**: `CURRENT_EXEC_YM`(YYYYMM, 디폴트 전월), `ADMIN_TOKEN`, `FRONTEND_ORIGIN`, `USE_MOCK`
- **mock 모드**: `auth_key` 없거나 `USE_MOCK=1` → gateway 호출 없이 가짜 데이터 (로컬/scaffold)

## 4. 데이터 소스

- **Gateway**: `https://polaris-colab.sktelecom.com/api/data-gateway`
- **최종 마트(summary, 사전 집계)**: `sandbox_db_max.device_sales_summary_daily` — 본부×단말군×SKU 단위 가정
- **원천(분석용, Phase B 매핑 확정 시 참조)**:
  - `di_crowd.policy_log_daily` (파티션 Y, `exec_ym`) — `sim_only` 컬럼으로 SIMonly군 분리
  - `di_crowd.mno_eqp_mdl_meta` (파티션 N) — `eqp_series_nm`로 단말군 분류. join key `eqp_mdl_cd`
- **단말군 매핑**: 사용자가 SQL(`eqp_series_nm` 분포) 돌려서 확정 예정 → 확정 후 `data_pipeline.DEVICE_GROUPS`/매핑 갱신

## 5. 단말군 8종 (+ 합계)

`SIMonly군`(=`sim_only='SIM only'`, 별도) / `S26군` / `IP17군` / `A17군` / `Z플립7군` / `Z폴드7군` / `와이드8군` / `기타`
- SKU 탭 보유: **S26군, IP17군** (`SKU_MAP`에 변형 정의)

## 6. 본부 9개

수도권 · PS&M · 제휴 · 부산 · 서부 · 대구 · 중부 · 기업사업본부 · TDS

## 7. 메트릭

- 판매건수(row count), 본부내비율, 전사비중, 본부간비중
- **과/과소 지수 = 본부내비율 − 전사비중** (양수=초록=과다, 음수=빨강=과소)

## 8. UI — 6 탭 (다크 테마)

1. **전사 개요** — KPI(총판매+Top3) / 단말군 막대 / 본부별 100% 누적 / SIMonly 토글
2. **S26군 SKU** — KPI / SKU별 막대 / SKU×본부 상세표
3. **IP17군 SKU** — 동일 구조
4. **본부별 분석** — 본부 chips / 포트폴리오 + 과·과소 지수표
5. **알림** — 긴급/주의/정보 3단계 (과·과소 지수 |값| 임계: ≥12 긴급 / ≥8 주의 / ≥5 정보)
6. **본부 매트릭스** — 본부×단말군 히트맵 (셀=본부내 비율%)

## 9. 핵심 파일

| 경로 | 역할 |
|---|---|
| `backend/data_gateway.py` | Polaris Gateway 클라이언트 (mno-ltv-monitor에서 재사용, 검증됨) |
| `backend/data_loader.py` | env 해석 · `_build_query` · `fetch_rows` (mock fallback) |
| `backend/data_pipeline.py` | `mock_rows` + `build_brief` (행→6탭 집계) |
| `backend/main.py` | FastAPI: `/health` `/api/status` `/api/brief` `/api/refresh` `/api/test-connection` + SPA mount |
| `frontend/index.html` | 단일 SPA (6탭 전체 UI) |

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

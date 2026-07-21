-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  테이블 생성 (최초 1회)  — 파티션 Iceberg
-- ---------------------------------------------------------------------------
-- 목적 : exec_ym 파티션 + Iceberg → 증분(DELETE/INSERT/OPTIMIZE)이 파티션 단위로 처리
-- 순서 : ① 이 DDL로 생성  ② full 백필(_from_wl_rslt_f.sql)  ③ 매일 증분  ④ 주1회 VACUUM
-- DB   : 최초엔 sandbox_db_max. 자산화 후 → obt_encore_max로 swap(+앱 env database).
-- LOCATION : 이 샌드박스는 필수. dev/ 하위 쓰기 가능 프리픽스에 지정(아래 값 확인).
-- ⚠️ 컬럼 정의줄엔 인라인 주석 금지(Athena 파서가 괄호 등에서 MISSING_COLUMN_NAME 냄).
-- 타입 : Athena/Iceberg — string / int / bigint / double
-- ═══════════════════════════════════════════════════════════════════════════

-- (재시도 전) 실패 잔여물 정리:
-- DROP TABLE IF EXISTS sandbox_db_max.device_sales_summary_daily3;
--   + 에러 메시지의 S3 tables/<uuid> 경로에 잔여 파일 있으면 수동 삭제 후 재시도

CREATE TABLE sandbox_db_max.device_sales_summary_daily3 (
  exec_dt string,
  exec_ym string,
  exec_year int,
  exec_month int,
  exec_day int,
  exec_dow string,
  exec_dow_idx bigint,
  mkt_div_org_cd string,
  mkt_div_org_nm string,
  device_group string,
  sub_model string,
  storage string,
  raw_series_nm string,
  brand_nm string,
  mfact string,
  sim_only string,
  scrb_type string,
  agree_type string,
  chnl_l string,
  chnl_m string,
  comb_gubun string,
  fee_group string,
  device_tier string,
  ext_dim_1 string,
  ext_dim_2 string,
  ext_dim_3 string,
  sales_cnt bigint,
  subscriber_cnt bigint,
  agency_cnt bigint,
  model_variety_cnt bigint,
  fee_prod_variety_cnt bigint,
  additional_cost_yn_cnt bigint,
  skt_tot_cost_sum double,
  skt_pr_mny_sum double,
  skt_pr_mny_wire_sum double,
  notc_supm_sum double,
  feeprod_discount_sum double,
  mfact_pr_mny_sum double,
  additional_cost_sum double,
  tot_cost_sum double,
  tot_pr_mny_sum double,
  skt_tot_cost_avg double,
  skt_pr_mny_avg double,
  tot_cost_avg double,
  tot_pr_mny_avg double,
  bas_fee_amt_avg double,
  discount_24m_avg double,
  scrb_arpu_avg double,
  out_prc_avg double,
  ltv_sum double,
  ltv_avg double,
  ext_metric_1 double,
  ext_metric_2 double,
  ext_metric_3 double,
  ext_metric_4 double,
  ext_metric_5 double
)
PARTITIONED BY (exec_ym)
LOCATION 's3://csms-obt-prd-smus/dzd-676c5tmhzlkqxk/ao2yn2jab79zmg/dev/sandbox_db_max/device_sales_summary_daily3/'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format' = 'parquet'
);

-- 컬럼 의미(참고): exec_dt=판매일 YYYYMMDD, exec_ym=판매월(파티션),
--   device_group=단말군11종, sim_only=Y/N, scrb_type=가입유형, agree_type=약정유형,
--   chnl_l=판매채널그룹, sales_cnt=핵심 판매건수. 비용/LTV/ext_*는 앱 미사용(NULL).

-- 주1회 스냅샷 정리(별도 스케줄): VACUUM sandbox_db_max.device_sales_summary_daily3;

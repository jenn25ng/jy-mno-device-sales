-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  테이블 생성 (최초 1회)  — 파티션 Iceberg
-- ---------------------------------------------------------------------------
-- 목적 : exec_ym 파티션 + Iceberg → 증분(DELETE/INSERT/OPTIMIZE)이 파티션 단위로 처리
-- 순서 : ① 이 DDL로 테이블 생성  ② full 백필(_from_wl_rslt_f.sql, proc_ym>='202501')
--        ③ 매일 증분(_incremental.sql)  ④ 주1회 VACUUM
-- ⚠️ DB : 최초엔 반드시 sandbox_db_max(내 샌드박스, 3개월마다 초기화)에 생성.
--         데이터 자산화 완료 후 sandbox_db_max → obt_encore_max 로 교체(+앱 env database 교체).
--         (3개 SQL·앱 env 모두 동일하게 DB명만 swap)
-- ⚠️ LOCATION : 내 프로젝트 버킷의 샌드박스 경로로 교체(예: s3://csms-obt-prd-.../sandbox_db_max/...).
--         샌드박스 DB가 관리 위치를 가지면 LOCATION 줄 생략 가능. 기존 sandbox 테이블
--         `SHOW CREATE TABLE sandbox_db_max.<아무거나>` 로 실제 경로 패턴 확인 권장.
-- 타입 : Athena/Iceberg — string/int/bigint/double
-- ═══════════════════════════════════════════════════════════════════════════

-- (교체 시) DROP TABLE sandbox_db_max.device_sales_summary_daily3;

CREATE TABLE sandbox_db_max.device_sales_summary_daily3 (
  exec_dt                 string,      -- 판매일자 YYYYMMDD
  exec_ym                 string,      -- 판매월 YYYYMM (파티션키)
  exec_year               int,
  exec_month              int,
  exec_day                int,
  exec_dow                string,      -- 요일명
  exec_dow_idx            bigint,      -- 요일 인덱스
  mkt_div_org_cd          string,      -- 본부 코드
  mkt_div_org_nm          string,      -- 본부명
  device_group            string,      -- 단말군(11종)
  sub_model               string,
  storage                 string,
  raw_series_nm           string,      -- 펫네임
  brand_nm                string,
  mfact                   string,
  sim_only                string,      -- Y/N
  scrb_type               string,      -- 가입유형
  agree_type              string,      -- 약정유형
  chnl_l                  string,      -- 판매채널 그룹명
  chnl_m                  string,
  comb_gubun              string,
  fee_group               string,
  device_tier             string,
  ext_dim_1               string,
  ext_dim_2               string,
  ext_dim_3               string,
  sales_cnt               bigint,      -- 핵심 판매건수
  subscriber_cnt          bigint,
  agency_cnt              bigint,
  model_variety_cnt       bigint,
  fee_prod_variety_cnt    bigint,
  additional_cost_yn_cnt  bigint,
  skt_tot_cost_sum        double,
  skt_pr_mny_sum          double,
  skt_pr_mny_wire_sum     double,
  notc_supm_sum           double,
  feeprod_discount_sum    double,
  mfact_pr_mny_sum        double,
  additional_cost_sum     double,
  tot_cost_sum            double,
  tot_pr_mny_sum          double,
  skt_tot_cost_avg        double,
  skt_pr_mny_avg          double,
  tot_cost_avg            double,
  tot_pr_mny_avg          double,
  bas_fee_amt_avg         double,
  discount_24m_avg        double,
  scrb_arpu_avg           double,
  out_prc_avg             double,
  ltv_sum                 double,
  ltv_avg                 double,
  ext_metric_1            double,
  ext_metric_2            double,
  ext_metric_3            double,
  ext_metric_4            double,
  ext_metric_5            double
)
PARTITIONED BY (exec_ym)                              -- ★ 파티션키 = exec_ym
-- LOCATION 생략 = 샌드박스 DB 관리 위치에 자동 배치(권장). 아래 에러 나면 주석 풀고 실경로로:
-- LOCATION 's3://csms-obt-prd-smus/dzd-676c5tmhzlkqxk/dev/sandbox_db_max/device_sales_summary_daily3/'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format'     = 'parquet'
);

-- 주1회 스냅샷 정리 (별도 스케줄)
-- VACUUM sandbox_db_max.device_sales_summary_daily3;

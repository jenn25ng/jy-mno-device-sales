-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  증분(일일) 배치  ←  midp_mos.wl_rslt_f
-- ---------------------------------------------------------------------------
-- 목적   : 매일 아침 최근 2개월(당월 + 전월 late 보정)만 갱신 → 과거 파티션 무변경(부하↓)
-- 전제   : 대상 테이블이 exec_ym 파티션 + row-level DELETE 지원(Athena Iceberg 등).
--          과거 전체 백필은 device_sales_summary_daily3_from_wl_rslt_f.sql (최초 1회) 참고.
-- 범위   : proc_ym >= 전월(YYYYMM). 로직(필터·단말군CASE·threading·컬럼)은 full 배치와 동일.
-- 운영   : 매일 8시 배치 이후(원천 최신) 실행 권장. 앱은 실행 후 재적재(또는 8시 자동).
--          ⚠️ DB명 = sandbox_db_max(초기). 자산화 후 obt_encore_max로 swap.
-- ⚠️ 과거 달(2개월보다 전) 소급 보정은 이 배치가 못 잡음 → 주 1회 full 재적재로 보완.
-- ═══════════════════════════════════════════════════════════════════════════

-- ① 최근 2개월 파티션만 제거 (당월 + 전월)
DELETE FROM sandbox_db_max.device_sales_summary_daily3
WHERE exec_ym >= date_format(date_add('month', -1, current_date), '%Y%m');

-- ② 최근 2개월만 재적재
INSERT INTO sandbox_db_max.device_sales_summary_daily3
  (exec_dt, exec_ym, exec_year, exec_month, exec_day, exec_dow, exec_dow_idx,
   mkt_div_org_cd, mkt_div_org_nm, device_group, sub_model, storage, raw_series_nm,
   brand_nm, mfact, sim_only, scrb_type, agree_type, chnl_l, chnl_m, comb_gubun,
   fee_group, device_tier, ext_dim_1, ext_dim_2, ext_dim_3, sales_cnt, subscriber_cnt,
   agency_cnt, model_variety_cnt, fee_prod_variety_cnt, additional_cost_yn_cnt,
   skt_tot_cost_sum, skt_pr_mny_sum, skt_pr_mny_wire_sum, notc_supm_sum, feeprod_discount_sum,
   mfact_pr_mny_sum, additional_cost_sum, tot_cost_sum, tot_pr_mny_sum, skt_tot_cost_avg,
   skt_pr_mny_avg, tot_cost_avg, tot_pr_mny_avg, bas_fee_amt_avg, discount_24m_avg,
   scrb_arpu_avg, out_prc_avg, ltv_sum, ltv_avg,
   ext_metric_1, ext_metric_2, ext_metric_3, ext_metric_4, ext_metric_5)
WITH base AS (
  SELECT
    proc_dt, proc_ym, mkt_div_org_id, mkt_div_org_nm,
    eqp_mdl_cd, eqp_mdl_petnm_2, mdl_factory_nm, usim_indpnd_svc_yn, old_eqp_yn, bchg_biz_co_cd,
    dsnet_chnl_grp_nm,                                          -- 판매채널 그룹명
    agrmt_cl_nm,                                               -- 약정유형
    new_010_rslt_cnt, mnp_in_rslt_cnt, eqp_chg_rslt_cnt
  FROM midp_mos.wl_rslt_f
  WHERE proc_ym >= date_format(date_add('month', -1, current_date), '%Y%m')   -- ★ 증분: 최근 2개월
    AND data_shr_cd='1' AND spcl_eqp_cl_nm='1'
    AND tblt_exclsv_cl_cd='1' AND second_device_nm='1'
),
unpiv AS (
  SELECT proc_dt, proc_ym, mkt_div_org_id, mkt_div_org_nm, eqp_mdl_cd,
         eqp_mdl_petnm_2, mdl_factory_nm, usim_indpnd_svc_yn, old_eqp_yn, dsnet_chnl_grp_nm, agrmt_cl_nm,
         '신규' AS scrb_type, new_010_rslt_cnt AS cnt
  FROM base WHERE new_010_rslt_cnt IS NOT NULL
  UNION ALL
  SELECT proc_dt, proc_ym, mkt_div_org_id, mkt_div_org_nm, eqp_mdl_cd,
         eqp_mdl_petnm_2, mdl_factory_nm, usim_indpnd_svc_yn, old_eqp_yn, dsnet_chnl_grp_nm, agrmt_cl_nm,
         CASE WHEN bchg_biz_co_cd IN ('KTF','LGT') THEN 'MNOMNP' ELSE 'MVNOMNP' END, mnp_in_rslt_cnt
  FROM base WHERE mnp_in_rslt_cnt IS NOT NULL
  UNION ALL
  SELECT proc_dt, proc_ym, mkt_div_org_id, mkt_div_org_nm, eqp_mdl_cd,
         eqp_mdl_petnm_2, mdl_factory_nm, usim_indpnd_svc_yn, old_eqp_yn, dsnet_chnl_grp_nm, agrmt_cl_nm,
         '기기변경', eqp_chg_rslt_cnt
  FROM base WHERE eqp_chg_rslt_cnt IS NOT NULL
),
agg AS (
  SELECT
    proc_dt AS exec_dt, proc_ym AS exec_ym,
    mkt_div_org_id AS mkt_div_org_cd, mkt_div_org_nm,
    CASE
      WHEN usim_indpnd_svc_yn='Y'
        OR mdl_factory_nm LIKE '블랙리스트%' OR mdl_factory_nm LIKE '%(타사)%'
        OR mdl_factory_nm LIKE '%(LGU%' OR mdl_factory_nm LIKE '%(KTF%'
        OR mdl_factory_nm LIKE 'MVNO%' OR old_eqp_yn='Y'          THEN 'SIMonly'
      WHEN eqp_mdl_petnm_2 LIKE '%S26%'                          THEN 'S26'
      WHEN eqp_mdl_petnm_2 LIKE '%S25%'                          THEN 'S25'
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%17%' OR eqp_mdl_petnm_2 LIKE '%IP17%' THEN 'IP17'
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%16%' OR eqp_mdl_petnm_2 LIKE '%IP16%' THEN 'IP16'
      WHEN eqp_mdl_petnm_2 LIKE '%플립7%' OR eqp_mdl_petnm_2 LIKE '%폴드7%' THEN 'Foldable7'
      WHEN eqp_mdl_petnm_2 LIKE '%퀀텀6%'                        THEN 'Quantum6'
      WHEN eqp_mdl_petnm_2 LIKE '%WIDE%'                        THEN 'Wide'
      WHEN eqp_mdl_petnm_2 LIKE '%A17%' OR eqp_mdl_petnm_2 LIKE '%A16%' THEN 'A17'
      WHEN eqp_mdl_petnm_2 LIKE '%스타일폴더%'                   THEN 'StyleFolder2'
      ELSE 'Etc'
    END AS device_group,
    CAST(NULL AS varchar) AS sub_model,
    regexp_extract(eqp_mdl_cd, '_([0-9]+(?:GB|TB|G|T)?)$', 1) AS storage,
    eqp_mdl_petnm_2 AS raw_series_nm,
    mdl_factory_nm AS mfact,
    CASE WHEN usim_indpnd_svc_yn='Y'
        OR mdl_factory_nm LIKE '블랙리스트%' OR mdl_factory_nm LIKE '%(타사)%'
        OR mdl_factory_nm LIKE '%(LGU%' OR mdl_factory_nm LIKE '%(KTF%'
        OR mdl_factory_nm LIKE 'MVNO%' OR old_eqp_yn='Y'
      THEN 'Y' ELSE 'N' END AS sim_only,
    scrb_type,
    dsnet_chnl_grp_nm AS chnl_l,
    agrmt_cl_nm AS agree_type,
    CAST(SUM(cnt) AS BIGINT) AS sales_cnt
  FROM unpiv
  GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13
)
SELECT
  exec_dt, exec_ym,
  CAST(substr(exec_dt,1,4) AS integer)                      AS exec_year,
  CAST(substr(exec_dt,5,2) AS integer)                      AS exec_month,
  CAST(substr(exec_dt,7,2) AS integer)                      AS exec_day,
  date_format(date_parse(exec_dt,'%Y%m%d'),'%W')            AS exec_dow,
  CAST(day_of_week(date_parse(exec_dt,'%Y%m%d')) AS bigint) AS exec_dow_idx,
  mkt_div_org_cd, mkt_div_org_nm,
  device_group, sub_model, storage, raw_series_nm,
  CAST(NULL AS varchar)  AS brand_nm,
  mfact, sim_only, scrb_type,
  agree_type, chnl_l,
  CAST(NULL AS varchar)  AS chnl_m,
  CAST(NULL AS varchar)  AS comb_gubun,
  CAST(NULL AS varchar)  AS fee_group,
  CAST(NULL AS varchar)  AS device_tier,
  CAST(NULL AS varchar)  AS ext_dim_1,
  CAST(NULL AS varchar)  AS ext_dim_2,
  CAST(NULL AS varchar)  AS ext_dim_3,
  sales_cnt,
  sales_cnt              AS subscriber_cnt,
  CAST(NULL AS bigint)   AS agency_cnt,
  CAST(NULL AS bigint)   AS model_variety_cnt,
  CAST(NULL AS bigint)   AS fee_prod_variety_cnt,
  CAST(NULL AS bigint)   AS additional_cost_yn_cnt,
  CAST(NULL AS double)   AS skt_tot_cost_sum,
  CAST(NULL AS double)   AS skt_pr_mny_sum,
  CAST(NULL AS double)   AS skt_pr_mny_wire_sum,
  CAST(NULL AS double)   AS notc_supm_sum,
  CAST(NULL AS double)   AS feeprod_discount_sum,
  CAST(NULL AS double)   AS mfact_pr_mny_sum,
  CAST(NULL AS double)   AS additional_cost_sum,
  CAST(NULL AS double)   AS tot_cost_sum,
  CAST(NULL AS double)   AS tot_pr_mny_sum,
  CAST(NULL AS double)   AS skt_tot_cost_avg,
  CAST(NULL AS double)   AS skt_pr_mny_avg,
  CAST(NULL AS double)   AS tot_cost_avg,
  CAST(NULL AS double)   AS tot_pr_mny_avg,
  CAST(NULL AS double)   AS bas_fee_amt_avg,
  CAST(NULL AS double)   AS discount_24m_avg,
  CAST(NULL AS double)   AS scrb_arpu_avg,
  CAST(NULL AS double)   AS out_prc_avg,
  CAST(NULL AS double)   AS ltv_sum,
  CAST(NULL AS double)   AS ltv_avg,
  CAST(NULL AS double)   AS ext_metric_1,
  CAST(NULL AS double)   AS ext_metric_2,
  CAST(NULL AS double)   AS ext_metric_3,
  CAST(NULL AS double)   AS ext_metric_4,
  CAST(NULL AS double)   AS ext_metric_5
FROM agg
;

-- ③ 파일 최적화 (증분 write로 생긴 소파일 compaction — 최근 2개월 파티션만)
OPTIMIZE sandbox_db_max.device_sales_summary_daily3
REWRITE DATA USING BIN_PACK
WHERE exec_ym >= date_format(date_add('month', -1, current_date), '%Y%m');

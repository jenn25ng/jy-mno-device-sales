-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  재적재 배치  ←  midp_mos.wl_rslt_f
-- ---------------------------------------------------------------------------
-- 소스   : midp_mos.wl_rslt_f (회선 실적 팩트, MAMF 원천)  ※ 구 policy_log_daily 대체
-- 윈도우 : 2025-01부터 고정 (proc_ym >= '202501', 프론트 날짜 하한 2025-01-01과 정합)
-- 필터   : (구)H/S 실적 = 데함쓰·특수단말·2nd디바이스·태블릿 제외
--          → data_shr_cd='1' AND spcl_eqp_cl_nm='1' AND tblt_exclsv_cl_cd='1' AND second_device_nm='1'
--          (플래그 1=해당아님(유지)/2=제외대상. old_yn은 "구형단말"이라 필터에 쓰지 않음)
-- 판매   : sales_cnt = new_010_rslt_cnt + mnp_in_rslt_cnt + eqp_chg_rslt_cnt
--          ※ 행마다 한 컬럼만 값(나머지 NULL) → 각각 SUM 후 CAST(BIGINT). a+b+c 직접합은 NULL 전파로 금물
-- 가입유형: 신규 / MNOMNP(bchg_biz_co_cd IN 'KTF','LGT') / MVNOMNP(그외) / 기기변경
-- 단말군 : eqp_mdl_petnm_2 CASE (9종). SIMonly = 유심독립(usim_indpnd_svc_yn='Y')
--          + 자급제/타사망(mdl_factory_nm: 블랙리스트%·%(타사)%·%(LGU%·%(KTF%·MVNO%)
--          + 중고단말(old_eqp_yn='Y', 일반 SK단말이라도 중고면 SIMonly)
-- 검증   : 2026-05 총 388,058건 = MAMF 리포트 일치
--          (신규 38,520 / MNO 89,014 / MVNO 39,078 / 기변 221,446, device_group 9종)
-- 대상   : sandbox_db_max.device_sales_summary_daily3 (56컬럼, 스키마 무변경)
--          ⚠️ 최초엔 sandbox_db_max. 자산화 후 → obt_encore_max로 DB명 swap(+앱 env database)
--          비용/LTV/ext_* 등 앱 미사용 메트릭은 NULL. subscriber_cnt는 sales_cnt로 대체.
-- 엔진   : Trino/Athena 문법 (date_parse·day_of_week·regexp_extract·element_at 미사용)
-- ═══════════════════════════════════════════════════════════════════════════

DELETE FROM sandbox_db_max.device_sales_summary_daily3;

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
    dsnet_chnl_grp_nm,                                          -- 판매채널 그룹명(특판/도매/소매/비즈)
    agrmt_cl_nm,                                                -- 약정유형(선택약정/지원금약정 등)
    new_010_rslt_cnt, mnp_in_rslt_cnt, eqp_chg_rslt_cnt
  FROM midp_mos.wl_rslt_f
  WHERE proc_ym >= '202501'   -- 2025-01부터 고정(프론트 날짜 하한 2025-01-01과 정합)
    AND data_shr_cd='1' AND spcl_eqp_cl_nm='1'
    AND tblt_exclsv_cl_cd='1' AND second_device_nm='1'
),
unpiv AS (   -- 가입유형별 건수 컬럼 → scrb_type 행 (행마다 한 컬럼만 값)
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
        OR mdl_factory_nm LIKE '블랙리스트%'
        OR mdl_factory_nm LIKE '%(타사)%'
        OR mdl_factory_nm LIKE '%(LGU%'
        OR mdl_factory_nm LIKE '%(KTF%'
        OR mdl_factory_nm LIKE 'MVNO%'          -- MVNO로 시작하는 단말
        OR old_eqp_yn='Y'                        -- 일반 SK단말인데 중고여부 Y
                                                 THEN 'SIMonly'
      WHEN eqp_mdl_petnm_2 LIKE '%S26%'          THEN 'S26'
      WHEN eqp_mdl_petnm_2 LIKE '%S25%'          THEN 'S25'     -- 신설(구 Etc에서 분리)
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%17%'
        OR eqp_mdl_petnm_2 LIKE '%IP17%'         THEN 'IP17'
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%16%'
        OR eqp_mdl_petnm_2 LIKE '%IP16%'         THEN 'IP16'    -- 신설(구 Etc에서 분리)
      WHEN eqp_mdl_petnm_2 LIKE '%플립7%'
        OR eqp_mdl_petnm_2 LIKE '%폴드7%'         THEN 'Foldable7'
      WHEN eqp_mdl_petnm_2 LIKE '%퀀텀6%'         THEN 'Quantum6'
      WHEN eqp_mdl_petnm_2 LIKE '%WIDE%'         THEN 'Wide'    -- 펫네임은 영문 WIDE8 등
      WHEN eqp_mdl_petnm_2 LIKE '%A17%'
        OR eqp_mdl_petnm_2 LIKE '%A16%'          THEN 'A17'     -- A17/16 통합(라벨 A17/16, 코드는 A17 유지)
      WHEN eqp_mdl_petnm_2 LIKE '%스타일폴더%'    THEN 'StyleFolder2'
      ELSE 'Etc'
    END AS device_group,
    CAST(NULL AS varchar) AS sub_model,                              -- 변형은 raw_series_nm에 포함
    regexp_extract(eqp_mdl_cd, '_([0-9]+(?:GB|TB|G|T)?)$', 1) AS storage,
    eqp_mdl_petnm_2 AS raw_series_nm,
    mdl_factory_nm AS mfact,
    CASE WHEN usim_indpnd_svc_yn='Y'
        OR mdl_factory_nm LIKE '블랙리스트%' OR mdl_factory_nm LIKE '%(타사)%'
        OR mdl_factory_nm LIKE '%(LGU%' OR mdl_factory_nm LIKE '%(KTF%'
        OR mdl_factory_nm LIKE 'MVNO%' OR old_eqp_yn='Y'
      THEN 'Y' ELSE 'N' END AS sim_only,
    scrb_type,
    dsnet_chnl_grp_nm AS chnl_l,                                     -- 판매채널 그룹명
    agrmt_cl_nm AS agree_type,                                       -- 약정유형
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

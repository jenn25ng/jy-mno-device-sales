-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  증분(일일) 배치  ←  midp_mos.wl_rslt_f
-- ---------------------------------------------------------------------------
-- 목적   : 매일 아침 최근 2개월(당월 + 전월 late 보정)만 갱신 → 과거 파티션 무변경(부하↓)
-- 전제   : 대상 테이블이 exec_ym 파티션 + row-level DELETE 지원(Athena Iceberg 등).
--          과거 전체 백필은 device_sales_summary_daily3_from_wl_rslt_f.sql (최초 1회) 참고.
-- 범위   : proc_ym >= 전월(YYYYMM). 로직(필터·단말군CASE·threading·컬럼)은 full 배치와 동일.
-- 운영   : 매일 8시 배치 이후(원천 최신) 실행 권장. 앱은 실행 후 재적재(또는 8시 자동).
--          대상 = obt_encore_max(자산화 완료, 운영 DB). ※ 스키마 생성만 sandbox_db_max(create.sql).
-- ⚠️ 과거 달(2개월보다 전) 소급 보정은 이 배치가 못 잡음 → 주 1회 full 재적재로 보완.
-- ═══════════════════════════════════════════════════════════════════════════

-- ① 최근 2개월 파티션만 제거 (당월 + 전월)
DELETE FROM obt_encore_max.device_sales_summary_daily3
WHERE exec_ym >= date_format(date_add('month', -1, current_date), '%Y%m');

-- ② 최근 2개월만 재적재
INSERT INTO obt_encore_max.device_sales_summary_daily3
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
    CASE                                                             -- device_group: 펫네임 CASE(마스터엔 깔끔한 단말군 없음)
      WHEN usim_indpnd_svc_yn='Y'
        OR mdl_factory_nm LIKE '블랙리스트%' OR mdl_factory_nm LIKE '%(타사)%'
        OR mdl_factory_nm LIKE '%(LGU%' OR mdl_factory_nm LIKE '%(KTF%'
        OR mdl_factory_nm LIKE 'MVNO%' OR old_eqp_yn='Y'          THEN 'SIMonly'
      WHEN eqp_mdl_petnm_2 LIKE '%S26%'                          THEN 'S26'
      WHEN eqp_mdl_petnm_2 LIKE '%S25%'                          THEN 'S25'
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%17%' OR eqp_mdl_petnm_2 LIKE '%IP17%' THEN 'IP17'
      WHEN eqp_mdl_petnm_2 LIKE '%아이폰%16%' OR eqp_mdl_petnm_2 LIKE '%IP16%' THEN 'IP16'
      WHEN eqp_mdl_petnm_2 LIKE '%플립8%' OR eqp_mdl_petnm_2 LIKE '%폴드8%' THEN 'Foldable8'   -- 신제품(8/4)
      WHEN eqp_mdl_petnm_2 LIKE '%플립7%' OR eqp_mdl_petnm_2 LIKE '%폴드7%' THEN 'Foldable7'
      WHEN eqp_mdl_petnm_2 LIKE '%퀀텀6%'                        THEN 'Quantum6'
      WHEN eqp_mdl_petnm_2 LIKE '%WIDE%'                        THEN 'Wide'
      WHEN eqp_mdl_petnm_2 LIKE '%A17%' OR eqp_mdl_petnm_2 LIKE '%A16%' THEN 'A17'
      WHEN eqp_mdl_petnm_2 LIKE '%스타일폴더%'                   THEN 'StyleFolder2'
      ELSE 'Etc'
    END AS device_group,
    CAST(NULL AS varchar) AS sub_model,
    -- 용량 우선순위: ① 폴더블8 단말코드 확정 → ② 모델명 접미 추출 → ③ 폴더블8 신규코드 base=256
    COALESCE(
      CASE
        WHEN eqp_mdl_cd IN ('A7GJ','A7GK','A7GL','A7GM','A7GN','A7GP','A7HU','A7HV','A7HW','A7HX','A7HY','A7HZ') THEN '1024'
        WHEN eqp_mdl_cd IN ('A7GC','A7GD','A7GE','A7GF','A7GG','A7GH','A7HN','A7HP','A7HQ','A7HR','A7HS','A7HT','A7FZ','A7G1','A7G2','A7G3','A7G4','A7G5') THEN '512'
        WHEN eqp_mdl_cd IN ('A7CX','A7G6','A7G7','A7G8','A7G9','A7GA','A7CY','A7HH','A7HJ','A7HK','A7HL','A7HM','A7CZ','A7FU','A7FV','A7FW','A7FX','A7FY') THEN '256'
      END,
      CASE
        WHEN regexp_like(mdl.eqp_mdl_nm, '_[0-9]+T[B]?$')                -- _1T / _1TB → *1024
          THEN CAST(CAST(regexp_extract(mdl.eqp_mdl_nm, '_([0-9]+)T[B]?$', 1) AS integer) * 1024 AS varchar)
        WHEN regexp_like(mdl.eqp_mdl_nm, '_[0-9]+G[B]?$')               -- _512G / _512GB
          THEN regexp_extract(mdl.eqp_mdl_nm, '_([0-9]+)G[B]?$', 1)
        WHEN regexp_like(mdl.eqp_mdl_nm, '_(64|128|256|512|1024|2048)$') -- _512 / _1024 (단위 없는 갤럭시)
          AND eqp_mdl_petnm_2 NOT LIKE '%아이폰%'                        -- 아이폰 제외(맨숫자=세대/연도 오추출 방지)
          THEN regexp_extract(mdl.eqp_mdl_nm, '_([0-9]+)$', 1)
        ELSE NULL
      END,
      CASE WHEN eqp_mdl_petnm_2 LIKE '%플립8%' OR eqp_mdl_petnm_2 LIKE '%폴드8%'
        THEN '256' END
    ) AS storage,
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
    cc.color_nm AS color_ext,                                        -- 색상(색상마스터 조인→디코딩) → ext_dim_1
    CAST(SUM(cnt) AS BIGINT) AS sales_cnt
  FROM unpiv
  -- 모델 마스터: del_flag='N' 최신 1건 dedup(eqp_mdl_cd 1:1 → fan-out 방지)
  LEFT JOIN (
    SELECT eqp_mdl_cd, eqp_mdl_nm FROM (
      SELECT eqp_mdl_cd, eqp_mdl_nm,
             ROW_NUMBER() OVER (PARTITION BY eqp_mdl_cd ORDER BY audit_dtm DESC) AS rn
      FROM midp.td_zeqp_eqp_mdl WHERE del_flag = 'N'
    ) WHERE rn = 1
  ) mdl ON unpiv.eqp_mdl_cd = mdl.eqp_mdl_cd
  LEFT JOIN (
    SELECT eqp_mdl_cd, MAX(color_cd) AS color_cd
    FROM midp.td_zeqp_eqp_color WHERE del_flag = 'N' GROUP BY eqp_mdl_cd
  ) clr ON unpiv.eqp_mdl_cd = clr.eqp_mdl_cd
  LEFT JOIN (
    SELECT color_cd, MAX(color_nm) AS color_nm FROM midp_tmt.mmkt_color_cd_c GROUP BY color_cd
  ) cc ON clr.color_cd = cc.color_cd
  GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14
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
  color_ext              AS ext_dim_1,                              -- 색상(매핑 dim 조인 결과)
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

-- ③ 파일 최적화 (증분 write로 생긴 소파일 compaction)
-- ⚠️ Athena OPTIMIZE의 WHERE는 '상수' 파티션 술어만 지원 — date_format(current_date) 같은
--    동적 함수식은 push-down 안 돼 GENERIC_INTERNAL_ERROR(Unexpected FilterNode) 발생.
--    (DELETE/INSERT는 동적식 OK, OPTIMIZE만 상수 요구)
-- → WHERE 없이 전체 최적화: Iceberg BIN_PACK은 이미 적정 크기 파일은 건너뛰므로
--    과거 파티션은 사실상 무비용, 소파일 생긴 최근 파티션만 압축됨.
-- (특정 월만 하려면 리터럴로: WHERE exec_ym >= '202606'  ← 스케줄러가 전월 YYYYMM 주입)
OPTIMIZE obt_encore_max.device_sales_summary_daily3
REWRITE DATA USING BIN_PACK;

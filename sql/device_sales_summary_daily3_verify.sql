-- ═══════════════════════════════════════════════════════════════════════════
-- device_sales_summary_daily3  배치 검산 쿼리 모음 (8/4 폴더블8 출시 대비)
-- ---------------------------------------------------------------------------
-- 순서: [0] 배포 전 스모크 → full 재적재 실행 → [1~6] 적재 후 검산.
-- 하이브리드 배치는 midp.td_zeqp_eqp_mdl(용량) + midp.td_zeqp_eqp_color +
--   midp_tmt.mmkt_color_cd_c(색상)를 eqp_mdl_cd로 조인. 폴더블8 용량은 단말코드로 확정.
-- ⚠️ 사내망(Athena/Trino): 식별자/alias 영문, 한글은 주석에만.
-- ═══════════════════════════════════════════════════════════════════════════


-- ───────────────────────────────────────────────────────────────────────────
-- [0] 배포 전 스모크 — 새로 조인하는 스키마 권한/카탈로그 확인 (❗ full DELETE 전에 먼저!)
--     하나라도 실패하면 full 재적재 하지 말 것(전체 DELETE 후 INSERT 실패 시 마트가 빔).
-- ───────────────────────────────────────────────────────────────────────────
SELECT 1 AS ok FROM midp.td_zeqp_eqp_mdl    LIMIT 1;
SELECT 1 AS ok FROM midp.td_zeqp_eqp_color  LIMIT 1;
SELECT 1 AS ok FROM midp_tmt.mmkt_color_cd_c LIMIT 1;


-- ───────────────────────────────────────────────────────────────────────────
-- [1] 조인키 유일성 — fan-out(판매건수 중복집계) 위험 사전 점검. 전부 0건이어야 정상.
-- ───────────────────────────────────────────────────────────────────────────
-- 1-a) 모델 마스터: del_flag='N' 최신1건 dedup 후 eqp_mdl_cd 유일한지
SELECT eqp_mdl_cd, COUNT(*) AS c
FROM (
  SELECT eqp_mdl_cd,
         ROW_NUMBER() OVER (PARTITION BY eqp_mdl_cd ORDER BY audit_dtm DESC) AS rn
  FROM midp.td_zeqp_eqp_mdl WHERE del_flag = 'N'
) WHERE rn = 1
GROUP BY eqp_mdl_cd HAVING COUNT(*) > 1;

-- 1-b) 색상코드 마스터: color_cd 유일한지
SELECT color_cd, COUNT(*) AS c
FROM midp_tmt.mmkt_color_cd_c
GROUP BY color_cd HAVING COUNT(*) > 1;


-- ───────────────────────────────────────────────────────────────────────────
-- [2] fan-out 종단 검증 — 마트 total ≈ 원천 total(같은 월). 마트 > 원천이면 fan-out 발생.
-- ───────────────────────────────────────────────────────────────────────────
WITH base_filtered AS (
  SELECT new_010_rslt_cnt, mnp_in_rslt_cnt, eqp_chg_rslt_cnt
  FROM midp_mos.wl_rslt_f
  WHERE proc_ym = '202607'
    AND data_shr_cd='1' AND spcl_eqp_cl_nm='1'
    AND tblt_exclsv_cl_cd='1' AND second_device_nm='1'
)
SELECT
  (SELECT COALESCE(SUM(new_010_rslt_cnt),0)
        + COALESCE(SUM(mnp_in_rslt_cnt),0)
        + COALESCE(SUM(eqp_chg_rslt_cnt),0) FROM base_filtered) AS source_total_no_join,
  (SELECT SUM(sales_cnt) FROM obt_encore_max.device_sales_summary_daily3
   WHERE exec_ym = '202607')                                    AS mart_total_after_join;
-- 두 값이 (거의) 같아야 정상. mart_total이 더 크면 조인 fan-out.


-- ───────────────────────────────────────────────────────────────────────────
-- [3] 용량(storage)·색상(ext_dim_1) 채움율 — 기종별. NULL 많은 기종 파악.
-- ───────────────────────────────────────────────────────────────────────────
SELECT device_group,
       COUNT(*)                                        AS rows_total,
       COUNT_IF(storage IS NOT NULL AND storage <> '') AS storage_filled,
       COUNT_IF(ext_dim_1 IS NOT NULL)                 AS color_filled,
       ROUND(COUNT_IF(storage IS NOT NULL AND storage <> '') * 100.0 / COUNT(*), 1) AS sto_pct,
       ROUND(COUNT_IF(ext_dim_1 IS NOT NULL)           * 100.0 / COUNT(*), 1) AS clr_pct
FROM obt_encore_max.device_sales_summary_daily3
WHERE exec_ym = '202608'          -- 폴더블8 데이터가 있는 월(출시 후)로 조정
GROUP BY device_group
ORDER BY rows_total DESC;


-- ───────────────────────────────────────────────────────────────────────────
-- [4] 폴더블8 전용 검증 — 용량(256/512/1024) × 색상 조합이 제대로 나오는지.
-- ───────────────────────────────────────────────────────────────────────────
SELECT raw_series_nm, storage, ext_dim_1 AS color, SUM(sales_cnt) AS sales
FROM obt_encore_max.device_sales_summary_daily3
WHERE device_group = 'Foldable8' AND exec_ym = '202608'
GROUP BY raw_series_nm, storage, ext_dim_1
ORDER BY raw_series_nm, storage, color;
-- 기대: 폴드8 울트라/폴드8=256·512·1024, 플립8=256·512. 색상은 블랙/화이트/바이올렛/라이트퍼플/라이트핑크.
-- storage NULL 이 있으면 → 단말코드 목록(배치 CASE)에 빠진 신규 폴더블8 코드 있는지 확인.


-- ───────────────────────────────────────────────────────────────────────────
-- [5] 용량 추출 규칙 미리보기 — 기종별 eqp_mdl_nm → 뽑히는 용량(배치와 동일 로직).
--     적재 전에도 실행 가능(마스터만 조회). NULL 많이 나오는 기종 사전 파악용.
-- ───────────────────────────────────────────────────────────────────────────
SELECT DISTINCT
  w.eqp_mdl_petnm_2,
  m.eqp_mdl_nm,
  CASE
    WHEN w.eqp_mdl_cd IN ('A7GJ','A7GK','A7GL','A7GM','A7GN','A7GP','A7HU','A7HV','A7HW','A7HX','A7HY','A7HZ') THEN '1024'
    WHEN w.eqp_mdl_cd IN ('A7GC','A7GD','A7GE','A7GF','A7GG','A7GH','A7HN','A7HP','A7HQ','A7HR','A7HS','A7HT','A7FZ','A7G1','A7G2','A7G3','A7G4','A7G5') THEN '512'
    WHEN w.eqp_mdl_cd IN ('A7CX','A7G6','A7G7','A7G8','A7G9','A7GA','A7CY','A7HH','A7HJ','A7HK','A7HL','A7HM','A7CZ','A7FU','A7FV','A7FW','A7FX','A7FY') THEN '256'
    WHEN regexp_like(m.eqp_mdl_nm, '_[0-9]+T[B]?$')
      THEN CAST(CAST(regexp_extract(m.eqp_mdl_nm, '_([0-9]+)T[B]?$', 1) AS integer) * 1024 AS varchar)
    WHEN regexp_like(m.eqp_mdl_nm, '_[0-9]+G[B]?$')
      THEN regexp_extract(m.eqp_mdl_nm, '_([0-9]+)G[B]?$', 1)
    WHEN regexp_like(m.eqp_mdl_nm, '_(64|128|256|512|1024|2048)$')
      AND w.eqp_mdl_petnm_2 NOT LIKE '%아이폰%'
      THEN regexp_extract(m.eqp_mdl_nm, '_([0-9]+)$', 1)
    WHEN w.eqp_mdl_petnm_2 LIKE '%플립8%' OR w.eqp_mdl_petnm_2 LIKE '%폴드8%' THEN '256'
    ELSE NULL
  END AS storage_extracted
FROM midp_mos.wl_rslt_f w
JOIN midp.td_zeqp_eqp_mdl m ON w.eqp_mdl_cd = m.eqp_mdl_cd AND m.del_flag = 'N'
WHERE w.proc_ym = '202607'
  AND w.data_shr_cd='1' AND w.spcl_eqp_cl_nm='1'
  AND w.tblt_exclsv_cl_cd='1' AND w.second_device_nm='1'
ORDER BY w.eqp_mdl_petnm_2
LIMIT 300;


-- ───────────────────────────────────────────────────────────────────────────
-- [6] A7xx 단말코드 과거 재사용 점검 — 출시 전(202608 이전) 데이터에 폴더블8 코드가
--     등장하면 과거 판매가 폴더블8로 오염될 수 있음. 0건이어야 안전.
-- ───────────────────────────────────────────────────────────────────────────
SELECT proc_ym, eqp_mdl_cd, eqp_mdl_petnm_2, COUNT(*) AS c
FROM midp_mos.wl_rslt_f
WHERE eqp_mdl_cd IN (
  'A7CX','A7G6','A7G7','A7G8','A7G9','A7GA','A7GC','A7GD','A7GE','A7GF','A7GG','A7GH',
  'A7GJ','A7GK','A7GL','A7GM','A7GN','A7GP','A7CY','A7HH','A7HJ','A7HK','A7HL','A7HM',
  'A7HN','A7HP','A7HQ','A7HR','A7HS','A7HT','A7HU','A7HV','A7HW','A7HX','A7HY','A7HZ',
  'A7CZ','A7FU','A7FV','A7FW','A7FX','A7FY','A7FZ','A7G1','A7G2','A7G3','A7G4','A7G5'
) AND proc_ym < '202608'
GROUP BY proc_ym, eqp_mdl_cd, eqp_mdl_petnm_2
ORDER BY proc_ym;

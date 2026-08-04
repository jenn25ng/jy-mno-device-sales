-- ═══════════════════════════════════════════════════════════════════════════
-- foldable8_model_map  —  폴더블8 단말코드(eqp_mdl_cd = A7xx) → 속성 매핑
-- ---------------------------------------------------------------------------
-- 목적 : 원천 wl_rslt_f.eqp_mdl_cd 는 A7xx 짧은코드(용량·색상 접미 없음) →
--        용량/색상을 여기서 조인해 마트에 채운다.
--        · storage   → 마트 storage 컬럼(현재 regex 추출이 A7xx에선 NULL이라 깨짐)
--        · color_nm  → 마트 ext_dim_1(색상 슬롯, 현재 NULL)
-- 근거 : 폴더블7 실측(eqp_mdl_cd=A6xx 짧은코드, storage_by_rule 전부 NULL) →
--        A7xx도 동일 체계. 삼성 공식 마스터(48행, 2026-08-04 출시) 기준.
-- 사용 : 배치(from_wl_rslt_f / incremental)의 agg 단계에서
--        LEFT JOIN foldable8_model_map m ON base.eqp_mdl_cd = m.dvc_cd
--        → storage = COALESCE(m.storage_gb, <기존 regex>), ext_dim_1 = m.color_nm
-- 확장 : 전체 단말로 넓히려면 같은 구조(dvc_cd→속성)에 행을 추가하거나,
--        제조사 모델 마스터를 상시 dim 테이블로 적재해 조인(권장, 아래 주석 참고).
-- ⚠️ 사내망(Athena/Trino): 식별자/alias는 영문, 한글은 주석·데이터값에만.
-- ═══════════════════════════════════════════════════════════════════════════

-- 인라인 매핑(48행). 배치에 CTE로 붙이거나, 아래를 CREATE TABLE 로 상시화해도 됨.
WITH foldable8_model_map (dvc_cd, device_group, model_nm, storage_gb, color_nm) AS (
  VALUES
    -- 갤럭시 Z 폴드8 울트라 (SM-F976, base=256 / _512G=512 / _1T=1024)
    ('A7CX','Foldable8','울트라','256','블랙/검정'),
    ('A7G6','Foldable8','울트라','256','블랙/검정'),
    ('A7G7','Foldable8','울트라','256','바이올렛'),
    ('A7G8','Foldable8','울트라','256','바이올렛'),
    ('A7G9','Foldable8','울트라','256','화이트/하양'),
    ('A7GA','Foldable8','울트라','256','화이트/하양'),
    ('A7GC','Foldable8','울트라','512','블랙/검정'),
    ('A7GD','Foldable8','울트라','512','블랙/검정'),
    ('A7GE','Foldable8','울트라','512','바이올렛'),
    ('A7GF','Foldable8','울트라','512','바이올렛'),
    ('A7GG','Foldable8','울트라','512','화이트/하양'),
    ('A7GH','Foldable8','울트라','512','화이트/하양'),
    ('A7GJ','Foldable8','울트라','1024','블랙/검정'),
    ('A7GK','Foldable8','울트라','1024','블랙/검정'),
    ('A7GL','Foldable8','울트라','1024','바이올렛'),
    ('A7GM','Foldable8','울트라','1024','바이올렛'),
    ('A7GN','Foldable8','울트라','1024','화이트/하양'),
    ('A7GP','Foldable8','울트라','1024','화이트/하양'),
    -- 갤럭시 Z 폴드8 (SM-F971, base=256 / _512G=512 / _1T=1024)
    ('A7CY','Foldable8','폴드','256','블랙/검정'),
    ('A7HH','Foldable8','폴드','256','블랙/검정'),
    ('A7HJ','Foldable8','폴드','256','화이트/하양'),
    ('A7HK','Foldable8','폴드','256','화이트/하양'),
    ('A7HL','Foldable8','폴드','256','라이트퍼플'),
    ('A7HM','Foldable8','폴드','256','라이트퍼플'),
    ('A7HN','Foldable8','폴드','512','블랙/검정'),
    ('A7HP','Foldable8','폴드','512','블랙/검정'),
    ('A7HQ','Foldable8','폴드','512','화이트/하양'),
    ('A7HR','Foldable8','폴드','512','화이트/하양'),
    ('A7HS','Foldable8','폴드','512','라이트퍼플'),
    ('A7HT','Foldable8','폴드','512','라이트퍼플'),
    ('A7HU','Foldable8','폴드','1024','블랙/검정'),
    ('A7HV','Foldable8','폴드','1024','블랙/검정'),
    ('A7HW','Foldable8','폴드','1024','화이트/하양'),
    ('A7HX','Foldable8','폴드','1024','화이트/하양'),
    ('A7HY','Foldable8','폴드','1024','라이트퍼플'),
    ('A7HZ','Foldable8','폴드','1024','라이트퍼플'),
    -- 갤럭시 Z 플립8 (SM-F776, base=256 / _512G=512, 1T 없음)
    ('A7CZ','Foldable8','플립','256','블랙/검정'),
    ('A7FU','Foldable8','플립','256','블랙/검정'),
    ('A7FV','Foldable8','플립','256','화이트/하양'),
    ('A7FW','Foldable8','플립','256','화이트/하양'),
    ('A7FX','Foldable8','플립','256','라이트핑크'),
    ('A7FY','Foldable8','플립','256','라이트핑크'),
    ('A7FZ','Foldable8','플립','512','블랙/검정'),
    ('A7G1','Foldable8','플립','512','블랙/검정'),
    ('A7G2','Foldable8','플립','512','화이트/하양'),
    ('A7G3','Foldable8','플립','512','화이트/하양'),
    ('A7G4','Foldable8','플립','512','라이트핑크'),
    ('A7G5','Foldable8','플립','512','라이트핑크')
)
SELECT dvc_cd, device_group, model_nm, storage_gb, color_nm
FROM foldable8_model_map
ORDER BY model_nm, CAST(storage_gb AS integer), color_nm;

-- ───────────────────────────────────────────────────────────────────────────
-- [배치 조인 예시]  from_wl_rslt_f.sql / incremental.sql 의 agg 단계에 적용
-- ───────────────────────────────────────────────────────────────────────────
--   WITH foldable8_model_map (...) AS ( VALUES ... ),          -- 위 CTE 재사용
--   ...
--   agg AS (
--     SELECT ...,
--       -- 용량: 마스터 우선, 없으면 기존 regex(구형 단말 호환)
--       COALESCE(m.storage_gb,
--                regexp_extract(eqp_mdl_cd, '_([0-9]+(?:GB|TB|G|T)?)$', 1)) AS storage,
--       ...
--       m.color_nm AS ext_dim_1                                -- 색상 슬롯
--     FROM unpiv u
--     LEFT JOIN foldable8_model_map m ON u.eqp_mdl_cd = m.dvc_cd
--     GROUP BY ...
--   )
--
-- [상시화 권장]  런치마다 CTE를 늘리는 대신 dim 테이블로:
--   CREATE TABLE obt_encore_max.device_model_map (
--     dvc_cd varchar, device_group varchar, model_nm varchar,
--     storage_gb varchar, color_nm varchar
--   );
--   -- 신규 출시 때마다 제조사 마스터를 INSERT → 배치는 이 테이블만 조인(전체 단말 확장).

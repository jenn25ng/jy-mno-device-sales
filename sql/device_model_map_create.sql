-- ═══════════════════════════════════════════════════════════════════════════
-- device_model_map  —  단말코드(eqp_mdl_cd) → 속성 매핑 상시 dim  (최초 1회 생성)
-- ---------------------------------------------------------------------------
-- 목적 : 원천 wl_rslt_f.eqp_mdl_cd 는 A7xx 짧은코드(용량·색상 접미 없음).
--        용량/색상을 이 dim에서 조인해 마트에 채운다.
--          · storage_gb → 마트 storage (현재 regex 추출이 짧은코드에선 NULL → 깨짐)
--          · color_nm   → 마트 ext_dim_1 (색상 슬롯)
-- 운영 : 신규 단말 출시 때마다 제조사 마스터를 INSERT (아래 템플릿) → 배치는 이 표만 조인.
--        전체 단말로 확장 가능(현재 폴더블8 48행 시드).
-- 근거 : 폴더블7 실측 eqp_mdl_cd=A6xx(짧은코드, storage regex NULL) → A7xx도 동일.
--        삼성 공식 마스터(폴더블8, 2026-08-04) 색상명·용량 기준.
-- ⚠️ 사내망(Athena/Trino): 식별자/alias는 영문, 한글은 주석·데이터값(색상명 등)에만.
--    LOCATION 필수 환경이면 WITH에 location='s3://.../device_model_map/' 추가.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE obt_encore_max.device_model_map
WITH (table_type = 'ICEBERG', format = 'PARQUET') AS
SELECT dvc_cd, device_group, model_nm, storage_gb, color_nm
FROM (VALUES
    -- ── 갤럭시 Z 폴드8 울트라 (SM-F976, base=256 / _512G=512 / _1T=1024) ──
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
    -- ── 갤럭시 Z 폴드8 (SM-F971, base=256 / _512G=512 / _1T=1024) ──
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
    -- ── 갤럭시 Z 플립8 (SM-F776, base=256 / _512G=512, 1T 없음) ──
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
) AS t (dvc_cd, device_group, model_nm, storage_gb, color_nm);

-- ───────────────────────────────────────────────────────────────────────────
-- [신규 출시 때 행 추가 템플릿]  제조사 마스터(단말코드·용량·색상)를 그대로 매핑
--   INSERT INTO obt_encore_max.device_model_map
--     (dvc_cd, device_group, model_nm, storage_gb, color_nm)
--   VALUES
--     ('B1AA','S27','기본','256','블랙'),
--     ... ;
--
-- [배치 조인]  from_wl_rslt_f.sql / incremental.sql 의 agg 단계:
--   FROM unpiv
--   LEFT JOIN obt_encore_max.device_model_map m ON unpiv.eqp_mdl_cd = m.dvc_cd
--   → storage = COALESCE(m.storage_gb, <기존 regex>)  /  ext_dim_1 = m.color_nm
-- ───────────────────────────────────────────────────────────────────────────

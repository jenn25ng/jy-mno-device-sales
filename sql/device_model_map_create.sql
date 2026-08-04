-- ═══════════════════════════════════════════════════════════════════════════
-- device_model_map  —  단말코드(eqp_mdl_cd) → 속성 매핑 상시 dim  (최초 1회 생성)
-- ---------------------------------------------------------------------------
-- 목적 : 원천 wl_rslt_f.eqp_mdl_cd 는 A7xx 짧은코드(용량·색상 접미 없음).
--        용량/색상/단말군을 이 dim에서 조인해 마트에 채운다.
--          · device_group → 마트 device_group (펫네임 CASE보다 정확 — 코드 확정값)
--          · storage_gb   → 마트 storage (regex 추출이 짧은코드에선 NULL → 깨짐)
--          · color_nm     → 마트 ext_dim_1 (색상 슬롯)
-- 운영 : 신규 단말 출시 때마다 제조사 마스터를 INSERT (아래 템플릿). 배치는 이 표만 조인.
-- 근거 : 폴더블7 실측 eqp_mdl_cd=A6xx(짧은코드, storage regex NULL) → A7xx도 동일.
--        삼성 공식 마스터(폴더블8, 2026-08-04) 색상명·용량 기준(48행).
--
-- ⚠️ [실행 전 필수 확인]  ── SQL 검수 지적 반영 ──
--   ① LOCATION: obt_encore_max 는 관리 위치 자동생성이 안 돼 CTAS/CREATE에 LOCATION 필수
--      (daily3 create.sql 관례와 동일). 아래 LOCATION 은 daily3와 같은 프리픽스 패턴 —
--      대상 DB의 실제 쓰기 가능 S3 경로로 반드시 확인/치환할 것.
--   ② 순서: 이 파일(dim) 먼저 성공 → 그다음 from_wl_rslt_f(full 백필) → incremental.
--   ③ 거버넌스: 관례상 sandbox_db_max 에서 먼저 만들어 검증 후 obt_encore_max 로 자산화
--      (DB명·LOCATION만 바꿔 동일 실행). 급하면 obt_encore_max 직접 생성도 가능.
-- ⚠️ 사내망(Athena/Trino): 식별자/alias는 영문, 한글은 주석·데이터값(색상명 등)에만.
-- ═══════════════════════════════════════════════════════════════════════════

-- ① 테이블 생성 (Iceberg + 명시적 LOCATION)
CREATE TABLE IF NOT EXISTS obt_encore_max.device_model_map (
  dvc_cd        string,   -- 단말코드 = wl_rslt_f.eqp_mdl_cd (조인키, A7xx)
  device_group  string,   -- 단말군 확정값 (예: Foldable8)
  model_nm      string,   -- 모델 (울트라/폴드/플립 등, 참고용)
  storage_gb    string,   -- 용량 (256 / 512 / 1024)
  color_nm      string    -- 색상명 (블랙/검정 등)
)
LOCATION 's3://csms-obt-prd-smus/dzd-676c5tmhzlkqxk/ao2yn2jab79zmg/dev/sandbox_db_max/device_model_map/'
TBLPROPERTIES (
  'table_type' = 'ICEBERG',
  'format' = 'parquet'
);

-- ② 폴더블8 48행 시드 (재실행 시 중복 방지 위해 필요하면 먼저 DELETE)
-- DELETE FROM obt_encore_max.device_model_map WHERE device_group = 'Foldable8';
INSERT INTO obt_encore_max.device_model_map
  (dvc_cd, device_group, model_nm, storage_gb, color_nm)
VALUES
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
  ('A7G5','Foldable8','플립','512','라이트핑크');

-- ───────────────────────────────────────────────────────────────────────────
-- [검산]  실행 후 확인 (SQL 검수 권장)
--   -- 시드 무결성(중복 dvc_cd 0건 기대)
--   SELECT dvc_cd, COUNT(*) c FROM obt_encore_max.device_model_map
--   GROUP BY dvc_cd HAVING COUNT(*) > 1;
--   -- 백필 후 커버리지: Foldable8 행의 color/storage 채움율
--   SELECT device_group, COUNT(*) rows,
--          COUNT_IF(ext_dim_1 IS NOT NULL) color_filled,
--          COUNT_IF(storage IS NOT NULL) storage_filled
--   FROM obt_encore_max.device_sales_summary_daily3
--   WHERE device_group='Foldable8' GROUP BY device_group;
--   -- A7xx 코드가 과거(출시 전) 데이터에 재사용됐는지(0건 기대)
--   SELECT proc_ym, eqp_mdl_cd, COUNT(*) c FROM midp_mos.wl_rslt_f
--   WHERE eqp_mdl_cd IN (SELECT dvc_cd FROM obt_encore_max.device_model_map)
--     AND proc_ym < '202608' GROUP BY proc_ym, eqp_mdl_cd;
--
-- [신규 출시 때 행 추가 템플릿]
--   INSERT INTO obt_encore_max.device_model_map
--     (dvc_cd, device_group, model_nm, storage_gb, color_nm)
--   VALUES ('B1AA','S27','기본','256','블랙'), ... ;
-- ───────────────────────────────────────────────────────────────────────────

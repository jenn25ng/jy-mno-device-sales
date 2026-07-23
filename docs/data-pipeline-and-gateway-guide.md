# 데이터 적재 + Polaris Gateway 로딩 최적화 가이드 (지식공유)

> Colab 앱에서 **① Iceberg 마트를 적재(배치)** 하고 **② Polaris Data Gateway로 앱 메모리에 로딩** 하는 전 과정과,
> `mno-device-sales`를 만들며 실제로 겪은 **함정·에러·최적화**를 한곳에 정리한 문서.
> 새로 비슷한 앱 만드는 분들은 이 문서의 "겪은 이슈" 표부터 보면 삽질을 줄일 수 있음.

## 전체 아키텍처
```
[원천 대용량 팩트]                [Iceberg 마트]              [앱 메모리 캐시]
midp_mos.wl_rslt_f  --배치(SQL)--> obt_encore_max.       --Gateway 조회--> pandas DataFrame
(회선 실적, 수억행)   집계·필터      device_sales_summary_    (auth_key)        (_CACHE, 요청은 여기서 집계)
                                   daily3 (일별, ~130만행)
```
- **배치 계층**: 원천을 스캔·집계해 마트에 적재 (무거움 → **증분 필수**)
- **앱 계층**: 이미 집계된 작은 마트를 Gateway로 읽어 메모리 캐시 (가벼움 → full 재로드로 충분)
- ⭐ **두 계층의 부하 성격이 다르다**는 게 핵심. 최적화 포인트도 다르다.

---

# Part A. 데이터 적재 (Iceberg 마트 배치)

## A-1. 테이블 설계
- Athena/Trino **Iceberg**, 파티션키 = `exec_ym`(YYYYMM). row-level DELETE 지원 → 증분 DELETE/INSERT 가능.
- DB 흐름: **최초엔 내 `sandbox_db_max`** 에 생성·검증 → **자산화** 후 상용 `obt_encore_max`로.
- 앱 env `database`도 그 시점에 swap. SQL의 대상 DB명도 동일하게.

## A-2. 배치 스케줄 (권장)
| 주기 | 작업 | 범위 |
|---|---|---|
| **최초 1회** | full 백필 | 2025-01~현재 전체 (DELETE 전체 + INSERT) |
| **매일** | 증분 | 당월+전월만 (DELETE→INSERT→OPTIMIZE) |
| **주 1회** | full 재적재 | 전체 (2개월보다 오래된 소급 보정 정합) |
| **주 1회** | VACUUM | 오래된 스냅샷 파일 정리 |
- 증분이 당월뿐 아니라 **전월까지** 가는 이유: 원천에 취소·소급 보정이 며칠 늦게 반영됨.

## A-3. ⚠️ 실제로 겪은 에러 & 해결 (배치)
| 증상 | 원인 | 해결 |
|---|---|---|
| `CREATE TABLE` → **MISSING_COLUMN_NAME at position N** | **컬럼 정의줄의 인라인 주석**(특히 `-- ...(파티션키)`의 **괄호**)이 파서를 깨뜨림 | 컬럼 정의부엔 **인라인 주석 금지**. 주석은 헤더/하단에 몰기 |
| `CREATE TABLE` → **LOCATION을 지정하라** | 샌드박스는 **관리 위치 자동 안 됨** = LOCATION 필수 | 쓰기 가능한 `.../dev/...` 프리픽스로 `LOCATION 's3://.../<db>/<table>/'` 지정. (에러 메시지의 S3 경로에서 쓰기 프리픽스 확인 가능) |
| `INSERT`/CTAS → **MISSING_COLUMN_NAME at position 3** | 최종 SELECT의 **계산식 컬럼이 무명**(`CAST(substr(...) AS int)`, `CAST(NULL AS varchar)` 등) → Athena가 쓸 컬럼명을 못 정함 | 모든 계산식 컬럼에 **`AS 별칭`** 부여 |
| `OPTIMIZE ... WHERE exec_ym >= ...` → **GENERIC_INTERNAL_ERROR: Unexpected FilterNode** | Athena Iceberg **OPTIMIZE의 WHERE는 범위 파티션 술어(`>=`)를 push-down 못 함**(리터럴이어도 동일). DELETE/INSERT는 됨, OPTIMIZE만 유독 제한 | **WHERE 없이 전체 OPTIMIZE**. BIN_PACK은 이미 적정 크기 파일은 **건너뛰므로** 과거 파티션 사실상 무비용. (특정 월만 하려면 범위 대신 `= / IN` 등호만) |
| `SUM` 합이 `a+b+c`인데 결과 blank | 행마다 한 컬럼만 값·나머지 NULL → **직접 `a+b+c`는 NULL 전파** | 각 컬럼 **따로 SUM 후 합**, `CAST(BIGINT)` |
| "5/1~5/30 총계 > 5/1~5/31" 역전 | `sales_cnt`는 **순 판매(net)**, 취소는 **음수**로 그 처리일에 기록 | 버그 아님. 마지막날/미완성일 음수 가능 |

## A-4. OPTIMIZE 실패 후 남은 파일 안내 문구
- 에러 끝의 "manually clean the data at location '...'"은 Athena **상투 문구**. OPTIMIZE는 커밋 전 실패라 **테이블은 안전**, orphan은 주1회 VACUUM이 정리. 손 안 대도 됨.

---

# Part B. Gateway 로딩 + 앱 메모리 캐시

## B-1. Polaris Data Gateway 패턴
- **auth_key 인증**으로 `SELECT`만. **output location/AWS 자격증명 불필요**(Gateway가 자기 workgroup·결과버킷으로 Athena 실행 후 결과를 API로 반환).
- 흐름: `start_query(sql)` → `poll_until_done(qid)` → `get_all_results(qid)`(페이지네이션) → 타입 캐스팅.
- 필수 env: `auth_key`, `user_id`, `app_name`, `database` (+ `MART_TABLE_NAME` 또는 `SOURCE_TABLE=db.table`). 코드는 대소문자 변형 허용.

## B-2. 메모리 캐시 패턴
- **startup에 `load_mart()` 1회** 실행 → 마트 전체를 pandas DataFrame으로 메모리 보관(`_CACHE`).
- 모든 endpoint는 `get_df()`로 **메모리에서 pandas 집계** → **Gateway 재호출 없음**.
- 갱신은 `POST /api/refresh` (배치 끝난 뒤 하루 1회).
- ⚠️ **env는 startup에 1회만 읽음** → env 바꾸면 **재배포/재시작**해야 반영.

## B-3. 조회 최적화 — 그레인 축소
- `SELECT *` 대신 **대시보드가 실제 쓰는 차원만 projection + GROUP BY**(`_FETCH_DIMS`)로 조회 → 행수·메모리 급감.
- 단, 필터를 추가하면 차원이 늘어 **행수가 곱으로 증가**함(주의, 아래 B-4 참고).

## B-4. ⚠️ 실제로 겪은 이슈 & 해결 (Gateway/앱) — ★핵심
| 증상 | 원인 | 해결 |
|---|---|---|
| reload 시 **ALB 504** | full 로드가 수 분 → **동기 응답이 60초 초과** | `refresh_async`: **트리거만 하고 즉시 반환**(fire-and-return) + 프론트가 `/api/diagnostics` 폴링해 완료 감지 |
| 적재 행이 **일부 유실**(기대보다 적음) | Gateway **next_token 페이지네이션이 경계에서 행을 흘림** | ① `get_all_results`에 **빈-페이지 재시도** ② **완결성 체크**: 전체 `COUNT(*)`와 대조해 로그(부족하면 error) |
| 순차 조회가 **7~8분** | 월별 조회가 각각 Athena 왕복(수 초)인데 **순차** | **월별 ThreadPoolExecutor 병렬**(min(6, months)) → 벽시계를 "가장 느린 한 달"로 |
| 로드가 **다시 수 분으로 느려짐** (⭐가장 값진 교훈) | 월당 `SELECT * FROM (GROUP BY) OFFSET n LIMIT 900`을 **페이지마다 실행** = **페이지마다 집계 재계산 O(n²)**. 게다가 **판매채널·약정유형 차원 추가로 행수 ~12배** → 월당 페이지 6→67개로 폭증(월당 쿼리 6→67회, 총 ~1,200회) | `run_query`가 **이미 next_token 페이지네이션 완비**(빈페이지 재시도) → **월당 단일 실행**으로 변경. 혹시 유실되면 **OFFSET 방식으로 자동 폴백**(완결성 비교). → 쿼리 ~1,200회→~36회, **수 분→수십 초** |
| env 바꿨는데 **여전히 옛 테이블/설정** | env는 **startup 1회** 로드 | **재배포/재시작**. 진단 드로어에서 연결 테이블 확인 |
| 데이터가 **옛것**(신규 컬럼값 NULL, 신규 분류 없음) | 앱 **메모리 캐시가 stale**(값 채워지기 전 로드) | `POST /api/refresh` (마트가 옛것이면 **마트부터 재적재**) |
| 필터 드롭다운이 **보였다 안 보였다** | 옵션을 **데이터 값에서만** 생성 → 로딩 중/미적재 시 빈 목록 → 숨김 | **표준 목록 폴백**으로 항상 렌더(위치 고정) |

## B-5. next_token vs OFFSET — 왜 이렇게 됐나 (요약)
- Gateway 응답은 **페이지당 ~1000행 캡**. 1000행 넘으면 next_token 다중 페이지.
- next_token이 **경계에서 행을 흘리는** 현상이 있었음 → 초기엔 회피책으로 `OFFSET/LIMIT STEP<1000`(각 조회를 단일 페이지로).
- 그런데 OFFSET은 **페이지마다 쿼리(집계) 재실행** → 데이터 커지면 O(n²)로 폭발.
- 결국 **next_token + 빈페이지 재시도 + 완결성 체크(+OFFSET 폴백)** 조합이 정답. (빠르면서 무손실)

## B-6. 앱 캐시는 왜 증분 안 하나? (자주 나오는 질문)
- 마트는 **이미 집계된 작은 데이터**(~130만행)라 full 재로드도 병렬로 수십 초.
- 앱은 날짜 필터(2025-01~) 때문에 **전 구간을 항상 메모리에 들고** 있어야 함.
- 증분 캐시(2개월만 갱신+병합)는 **드리프트 위험**(오래된 달 소급보정 못 잡음) + 복잡도↑ → **YAGNI**.
- 결론: **배치는 증분(원천 대용량 스캔), 앱은 full 재로드(마트 작음)**. 각 계층에 맞는 최적화.

---

# Part C. 운영 체크리스트 & 교훈

## 새 데이터 반영 절차
1. (마트) full 백필 or 증분 배치 실행 → 마트에 값 채움
2. (필요시) `OPTIMIZE`(WHERE 없이) + 주1회 `VACUUM`
3. (앱) env `database`/`MART_TABLE_NAME` 확인 → 바꿨으면 **재배포**
4. (앱) `POST /api/refresh`로 메모리 캐시 갱신
5. 진단 드로어에서 **연결 테이블·행수·기간** 확인 (로그 `N행 / 기대 M` 일치?)

## 핵심 교훈 (한 줄씩)
- **Athena Iceberg OPTIMIZE**: 범위 파티션 술어 불가 → **무WHERE 전체** or 등호/IN만.
- **INSERT/CTAS**: 계산식 컬럼 전부 **`AS 별칭`**.
- **CREATE**: 컬럼 정의줄 **인라인 주석 금지**, 샌드박스는 **LOCATION 필수**.
- **Gateway**: 페이지당 ~1000행 캡 → 대용량은 **next_token + 완결성 체크**. OFFSET 페이징은 O(n²) 함정.
- **차원 추가 = 행수 곱증가**: 필터용 컬럼 추가 시 로드 비용 급증 주의.
- **env는 startup 1회**: 바꾸면 **재배포**.
- **부하 계층 분리**: 배치=증분, 앱=full 재로드.
- **sales_cnt는 net**: 취소 음수 → 총계 역전은 정상.

---

## 참고 파일 (이 레포)
- 배치 SQL: `sql/device_sales_summary_daily3_create.sql`(DDL) · `_from_wl_rslt_f.sql`(full) · `_incremental.sql`(증분)
- 앱 데이터층: `backend/data.py`(메모리 캐시·월별 병렬 로드·완결성) · `backend/data_gateway.py`(Gateway 클라이언트·페이지네이션)
- 워터마크: `docs/watermark-howto.md`

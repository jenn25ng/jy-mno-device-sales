# Data Gateway API — Vibe Coding 참고 가이드

이 문서는 AI IDE(Cursor, Windsurf, Kiro 등)에서 vibe coding 시 Data Gateway API를 활용하기 위한 참고 자료입니다.

> 📌 **이 문서의 범위**
> - **이 문서(DATA_GATEWAY_VIBE_GUIDE)**: **이미 배포된 앱**에서 Data Gateway 를 통해 **실시간으로 데이터를 쿼리**해 가져올 때 사용합니다.
> - **앱 생성 / DB 연결**은 `VIBE_CODING_GUIDE` 문서를 참고하세요. (앱을 만들고 DB 를 직접 연결하는 단계)
>
> ⚠️ Data Gateway 는 `DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` 같은 **DB 직접 접속용 환경변수를 사용하지 않습니다.**
> 인증은 오직 `DATA_GATEWAY_AUTH_KEY` 로만 이루어지며, 조회 대상은 SQL 안에 **`DB.Table` 형태로 직접 명시**합니다.

## Base URL

| 환경 | Base URL |
|------|----------|
| PRD | `https://polaris-colab.sktelecom.com` |

모든 API 경로는 `/api/data-gateway/` 하위에 있습니다.

---

## API 목록

| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/data-gateway/start-query-execution` | Athena 쿼리 실행 |
| POST | `/api/data-gateway/get-query-execution` | 쿼리 상태 조회 |
| POST | `/api/data-gateway/get-query-results` | 쿼리 결과 조회 |
| POST | `/api/data-gateway/stop-query-execution` | 쿼리 중지 |

---

## 인증 방식

| app_type | auth_key | app_name | database.table 검증 | 용도 |
|----------|----------|----------|---------------------|------|
| `app` | 필수 | 필수 | auth_key에 등록된 테이블만 허용 | Polaris Colab에 배포한 앱 |

> 🚨 **auth_key 보안 — 반드시 준수**
>
> **`auth_key`는 절대로 소스 코드나 Git repository에 저장하지 마세요.**
>
> `auth_key` 및 DB 연결 관련 인증 정보는 **Polaris Colab → App → Environment 탭**에서 환경변수로 등록하여 관리합니다.
>
> ### 환경변수 등록 방법
>
> 1. Polaris Colab 접속 → 해당 App 선택
> 2. **Environment** 탭 클릭
> 3. 아래 환경변수를 등록:
>
> | 환경변수 키 | 용도 | 예시 값 |
> |-------------|------|---------|
> | `DATA_GATEWAY_AUTH_KEY` | Data Gateway 인증 키 (**Data Gateway 사용 시 이 키만 필요**) | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
>
> > ⚠️ **`DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` 은 Data Gateway 와 무관합니다.**
> > 이 변수들은 앱이 **DB 에 직접 접속(드라이버/JDBC)** 할 때만 쓰는 값으로, Data Gateway API 호출에는 사용되지 않습니다.
> > 특히 **`DB_NAME` 값을 Data Gateway 쿼리의 DB 명으로 넣지 마세요.** Data Gateway 쿼리는 SQL 안에
> > `DB.Table` 형태로 대상을 직접 명시합니다. (아래 "쿼리 작성 시 DB.Table 명시" 참고)
>
> ### ❌ 금지 사항
>
> - 코드에 `auth_key` 또는 DB 비밀번호를 하드코딩
> - `.env` 파일을 Git에 커밋 (반드시 `.gitignore`에 추가)
> - README, 문서, 주석에 실제 키 값 노출
>
> ### ✅ 코드에서 사용 방법
>
> ```python
> import os
>
> # Colab App Environment 탭에서 등록한 환경변수를 읽어옵니다
> # Data Gateway 사용 시 필요한 값은 AUTH_KEY 뿐입니다 (DB_HOST/DB_NAME 등은 불필요)
> AUTH_KEY = os.environ["DATA_GATEWAY_AUTH_KEY"]
> ```
>
> ```javascript
> // Node.js
> const AUTH_KEY = process.env.DATA_GATEWAY_AUTH_KEY;
> ```

---

## 쿼리 제한사항

**SELECT 쿼리만 허용됩니다.** 데이터 변경/삭제를 방지하기 위해 서버에서 쿼리 타입을 검증합니다.

| 허용 | 차단 |
|------|------|
| `SELECT` | `INSERT` |
| `WITH` (CTE) | `UPDATE` |
| `SHOW` | `DELETE` |
| `DESCRIBE` | `DROP` / `CREATE` / `ALTER` / `CTAS` |

### 쿼리 작성 시 DB.Table 명시 (MUST)

Data Gateway 쿼리는 **조회 대상을 `DB.Table` (데이터베이스명.테이블명) 형태로 정확히 명시**해야 합니다.
**기본(default) DB 가 없으므로**, 테이블명만 단독으로 쓰면 어느 DB 인지 알 수 없어 쿼리가 실패합니다.

> 🚫 **앱에 연결된 `DB_NAME` 환경변수를 쿼리의 DB 명으로 사용하지 마세요.**
> `DB_NAME` 은 앱이 직접 접속하는 DB(드라이버 연결)용 값이며, Data Gateway 가 조회하는 대상 카탈로그의
> DB 명과 다릅니다. Data Gateway 쿼리에는 **`auth_key` 에 등록된 실제 `DB.Table`** 을 그대로 적어야 합니다.

#### ✅ 올바른 예시

```sql
-- DB.Table 을 정확히 명시
SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10;
SELECT col_a, col_b FROM my_db.orders WHERE dt = '2026-06-01' LIMIT 100;
```

#### ❌ 잘못된 예시

```sql
-- 테이블명만 단독 사용 — 기본 DB 가 없어 실패
SELECT * FROM base_demo_pred_monthly LIMIT 10;

-- 앱의 DB_NAME 환경변수(직접 연결용)를 DB 명으로 잘못 사용
-- → auth_key 에 등록된 실제 카탈로그 DB.Table 과 달라 UNAUTHORIZED_TABLES 오류
SELECT * FROM ${DB_NAME}.orders;
```

### Query 구문 작성 가이드

Athena 는 스캔한 데이터 양에 비례해 비용이 발생하고, 대량 스캔은 클러스터 부하를 유발합니다.
아래 규칙을 반드시 준수하세요.

| # | 규칙 | 설명 |
|---|------|------|
| 1 | **조건절 없는 전체 조회 금지** | `SELECT * FROM <table>` 처럼 WHERE 절이 없는 쿼리는 **꼭 필요한 경우에만** 실행하세요. 불필요한 풀 스캔은 비용 증가와 시스템 과부하를 초래합니다. |
| 2 | **파티션 테이블은 파티션 조건 필수** | 파티션이 있는 테이블을 조회할 때는 반드시 파티션 컬럼을 WHERE 절에 지정하세요. 파티션 조건 없이 조회하면 전체 파티션을 스캔하여 시간과 비용이 급증합니다. |
| 3 | **테스트 쿼리에는 LIMIT 필수** | 데이터를 확인하거나 쿼리를 검증하는 목적이라면 반드시 `LIMIT`을 붙여 실행하세요. |

#### ✅ 올바른 예시

```sql
-- 파티션 테이블: 파티션 조건 + LIMIT
SELECT * FROM my_db.daily_logs
WHERE dt = '2026-06-01'
LIMIT 100;

-- 집계 쿼리: 필요한 기간만 지정
SELECT COUNT(*) FROM my_db.orders
WHERE order_date BETWEEN DATE '2026-06-01' AND DATE '2026-06-30';
```

#### ❌ 금지/주의 예시

```sql
-- 조건절 없는 풀 스캔 (금지)
SELECT * FROM my_db.daily_logs;

-- 파티션 테이블인데 파티션 미지정 (금지)
SELECT * FROM my_db.daily_logs WHERE user_id = '12345';

-- 테스트 목적인데 LIMIT 없음 (주의)
SELECT * FROM my_db.orders WHERE dt = '2026-06-01';
```

### Athena 서비스 제한 (AWS 기준)

| 항목 | 제한값 | 비고 |
|------|--------|------|
| DML 쿼리 타임아웃 | **30분** (기본) | 최대 240분까지 증가 요청 가능 |
| DDL 쿼리 타임아웃 | 600분 | 조정 불가 |
| 동시 실행 DML 쿼리 (ap-northeast-2) | **100개** | 실행 중 + 대기 중 합산 |
| 쿼리 문자열 최대 길이 | **262,144 bytes** (UTF-8) | 조정 불가 |

> 📖 출처: [Amazon Athena Service Quotas](https://docs.aws.amazon.com/general/latest/gr/athena.html#amazon-athena-limits)

---

## 에러 응답 형식

모든 에러는 아래 형식으로 반환됩니다.

```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "FORBIDDEN_QUERY_TYPE",
    "message": "허용되지 않는 쿼리 타입입니다: INSERT. SELECT 쿼리만 실행할 수 있습니다.",
    "details": null,
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

### 에러 코드 목록

| HTTP 상태 | error_code | error_type | 설명 | 발생 API |
|-----------|------------|------------|------|----------|
| 400 | `FORBIDDEN_QUERY_TYPE` | user_error | SELECT/WITH/SHOW/DESCRIBE 외 쿼리 실행 시도 | start-query-execution |
| 400 | `QUERY_RESULT_UNAVAILABLE` | user_error | 쿼리가 아직 완료되지 않았거나 결과를 조회할 수 없음 | get-query-results |
| 403 | `INVALID_AUTH_KEY` | user_error | auth_key가 유효하지 않거나 user_id/app_name과 매칭되지 않음 | start-query-execution |
| 403 | `UNAUTHORIZED_TABLES` | user_error | SQL에서 참조한 테이블이 auth_key에 등록되지 않음 | start-query-execution |
| 404 | `QUERY_NOT_FOUND` | user_error | 존재하지 않는 query_execution_id | get-query-execution |
| 503 | `EXTERNAL_SERVICE_ERROR` | system_error | Athena 서비스 호출 실패 (네트워크, 권한 등) | 모든 API |
| 504 | (게이트웨이) | system_error | 게이트웨이(ALB) 응답 대기 시간(기본 60초) 초과. 표준 JSON 형식이 아닌 게이트웨이 응답. 주로 `get-query-results`를 `max_results` 없이 대용량 결과에 호출할 때 발생 | get-query-results (주로) |
| 413 | (게이트웨이) | system_error | 요청 본문(Request Body) 크기가 ALB/프록시 제한(기본 1MB) 초과. FE에서 대량의 쿼리 결과를 POST body로 백엔드에 전송할 때 발생. 결과 데이터는 FE가 중계하지 말고 백엔드가 `query_execution_id`로 직접 조회해야 함 | 앱 자체 API (Data Gateway 아님) |

> **참고**: `error_type`이 `user_error`이면 클라이언트 측 문제, `system_error`이면 서버 측 문제입니다.
>
> ⚠️ **504 Gateway Timeout 해결**: `get-query-results` 는 항상 `max_results`(예: 1000)를 지정해 페이지네이션하세요.
> `max_results` 없이 대용량 결과를 한 번에 조회하면 게이트웨이 60초 제한을 초과해 504가 발생합니다.
> 자세한 내용은 "3. 결과 조회 > 페이지네이션" 섹션을 참고하세요.
>
> ⚠️ **413 Request Entity Too Large 해결**: 쿼리 결과(rows)를 FE → BE 로 POST body에 담아 전송하지 마세요.
> 수천~수만 건의 결과 데이터를 JSON으로 보내면 ALB body size 제한(1MB)을 초과합니다.
> 대신 백엔드가 `query_execution_id`를 받아 Data Gateway에서 직접 결과를 가져와 처리하세요.
> 자세한 내용은 ["프론트엔드 주도 폴링 > 결과 집계 시 413 방지"](#결과-집계-시-413-방지) 섹션을 참고하세요.

---

## 쿼리 실행 전체 흐름

```
start-query-execution → query_execution_id 획득
        ↓
get-query-execution   → 상태 폴링 (3~5초 간격, 최대 30분)
        ↓
    SUCCEEDED?
     ├─ Yes → get-query-results → 결과 데이터
     └─ No  → FAILED/CANCELLED → state_change_reason 확인
```

### 폴링 권장 사항

- **폴링 간격**: 3~5초 (내부 시스템 과부하 방지를 위해 최소 3초 이상 유지)
- **최대 대기 시간**: 30분 (Athena DML 쿼리 기본 타임아웃)
- **최대 재시도 횟수**: 450회 (4초 간격 × 30분) 또는 타임아웃 기반 종료 권장
- **타임아웃 초과 시**: `stop-query-execution`으로 쿼리를 명시적으로 중지

> 💡 **대시보드/SPA 라면 이 폴링 루프를 프론트엔드가 직접 돌리는 것을 권장합니다.**
> 백엔드가 한 요청 안에서 `start → 폴링 → 결과`를 끝까지 기다리면(블로킹) 게이트웨이 60초 제한에
> 걸려 504가 발생할 수 있습니다. 자세한 패턴은 ["프론트엔드 주도 폴링 (대시보드 권장 패턴)"](#프론트엔드-주도-폴링-대시보드-권장-패턴) 섹션을 참고하세요.

---

## 1. 쿼리 실행

```http
POST {BASE_URL}/api/data-gateway/start-query-execution
Content-Type: application/json

{
  "query_string": "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
  "user_id": "1111903",
  "app_type": "app",
  "app_name": "my-data-app",
  "auth_key": "your-auth-key"
}
```

**Response (200):**
```json
{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4"
}
```

### 필드 설명

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `query_string` | string | ✅ | 실행할 SQL. `auth_key`로 인증된 `DB.table`만 SELECT 가능. 최대 262,144 bytes (UTF-8) |
| `user_id` | string | ✅ | Polaris Colab의 사용자 user_id |
| `app_type` | string | ✅ | `app` (고정값) |
| `app_name` | string | ✅ | Polaris Colab에 배포한 앱 이름 |
| `auth_key` | string | ✅ | 인증 키 (UUID4). 이 키에 허가된 `DB.table`만 조회 가능 |

### 에러 응답 예시

**차단된 쿼리 타입 (400):**
```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "FORBIDDEN_QUERY_TYPE",
    "message": "허용되지 않는 쿼리 타입입니다: INSERT. SELECT 쿼리만 실행할 수 있습니다.",
    "details": null,
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

**잘못된 auth_key (403):**
```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "INVALID_AUTH_KEY",
    "message": "유효하지 않은 auth_key이거나 권한이 없습니다.",
    "details": null,
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

**미인가 테이블 접근 (403):**
```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "UNAUTHORIZED_TABLES",
    "message": "auth_key에 등록되지 않은 테이블이 포함되어 있습니다: ['secret.data']. 해당 테이블에 대한 auth_key 발급이 필요합니다. POST /api/data-gateway/register-auth-key 를 통해 발급받으세요.",
    "details": {"unauthorized_tables": ["secret.data"]},
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

---

## 2. 상태 조회

```http
POST {BASE_URL}/api/data-gateway/get-query-execution
Content-Type: application/json

{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4"
}
```

**Response (200):**
```json
{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4",
  "query": "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
  "status": "SUCCEEDED",
  "state_change_reason": null,
  "submission_time": "2026-04-15T07:00:00+00:00",
  "completion_time": "2026-04-15T07:00:03+00:00",
  "data_scanned_bytes": 1048576,
  "execution_time_ms": 3200,
  "output_location": "s3://athena-results/...",
  "workgroup": "workgroup-ai1lfugdveq13s-d7pe3pxik6ukns"
}
```

### 상태 값

| status | 의미 | 다음 액션 |
|--------|------|-----------|
| `SUBMITTED` | 제출됨 | 폴링 계속 |
| `RUNNING` | 실행 중 | 폴링 계속 |
| `SUCCEEDED` | 완료 | get-query-results 호출 |
| `FAILED` | 실패 | state_change_reason 확인 |
| `CANCELLED` | 취소됨 | 종료 |

### 에러 응답 예시

**존재하지 않는 쿼리 (404):**
```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "QUERY_NOT_FOUND",
    "message": "존재하지 않는 쿼리입니다: invalid-query-id",
    "details": null,
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

---

## 3. 결과 조회

```http
POST {BASE_URL}/api/data-gateway/get-query-results
Content-Type: application/json

{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4"
}
```

**Response (200):**
```json
{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4",
  "columns": [
    {"name": "svc_mgmt_num", "type": "varchar"},
    {"name": "base_ym", "type": "varchar"},
    {"name": "pred_value", "type": "double"}
  ],
  "rows": [
    ["1234567890", "202603", "0.85"],
    ["1234567891", "202603", "0.72"]
  ],
  "next_token": null
}
```

### 페이지네이션

대량 데이터를 조회할 때 `max_results`와 `next_token`을 사용합니다.

> 🚨 **504 Gateway Timeout 방지 — `max_results` 를 반드시 지정하세요 (중요)**
>
> `get-query-results` 를 `max_results` 없이 호출하면 Athena 가 **전체 결과를 한 번에 fetch** 합니다.
> 대용량 테이블이면 이 과정이 게이트웨이(ALB) 응답 제한(**기본 60초**)을 초과해 **504 Gateway Timeout** 이 발생합니다.
>
> - ✅ **권장**: `max_results` 를 **1000 이하**로 지정하고, `next_token` 으로 페이지를 이어서 조회
> - ❌ **지양**: `max_results` 없이 대용량 결과를 한 번에 조회 (504 위험)
> - 한 번에 많은 행이 필요하면, 쿼리 단계에서 `LIMIT` 으로 결과 크기 자체를 줄이는 것도 방법입니다.
> - 504 는 **앱 코드가 아니라 게이트웨이가 반환**하므로, 표준 JSON 에러 형식이 아닐 수 있습니다.

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `query_execution_id` | string | ✅ | Athena 쿼리 실행 ID |
| `max_results` | int | ⚠️ 권장 | 최대 결과 행 수 (최소 1). **미입력 시 전체 반환 → 대용량이면 504 위험.** 1000 이하 권장 |
| `next_token` | string | ❌ | 이전 응답의 `next_token` 값 |

**페이지네이션 요청 예시:**
```json
{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4",
  "max_results": 100,
  "next_token": null
}
```

**페이지네이션 응답 (다음 페이지 있음):**
```json
{
  "query_execution_id": "d3f991c6-...",
  "columns": [...],
  "rows": [...],
  "next_token": "AYADeJ..."
}
```

`next_token`이 `null`이면 마지막 페이지입니다. `null`이 아니면 다음 요청에 `next_token`을 포함하여 다음 페이지를 조회합니다.

> ⚠️ **모든 값은 문자열로 반환됩니다.** Athena는 결과를 `VarCharValue`로 직렬화하므로, `double`, `integer` 등의 타입도 문자열(`"0.85"`, `"123"`)로 반환됩니다. 클라이언트에서 `columns[].type`을 참고하여 적절한 타입 변환을 수행하세요.

### 에러 응답 예시

**쿼리 미완료 상태에서 결과 조회 (400):**
```json
{
  "detail": {
    "error_type": "user_error",
    "error_code": "QUERY_RESULT_UNAVAILABLE",
    "message": "쿼리 결과를 조회할 수 없습니다: d3f991c6-.... 원인: Query has not yet finished.",
    "details": {
      "athena_error_code": "InvalidRequestException",
      "athena_error_message": "Query has not yet finished."
    },
    "timestamp": "2026-04-15T07:00:00+00:00"
  }
}
```

---

## 4. 쿼리 중지

```http
POST {BASE_URL}/api/data-gateway/stop-query-execution
Content-Type: application/json

{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4"
}
```

**Response (200):**
```json
{
  "query_execution_id": "d3f991c6-bfab-4569-9686-de674eff69e4",
  "status": "CANCELLED"
}
```

### 필드 설명

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `query_execution_id` | string | ✅ | 중지할 Athena 쿼리 실행 ID |

> **참고**: 이미 완료(`SUCCEEDED`)되거나 실패(`FAILED`)한 쿼리에 대해 중지를 호출해도 에러가 발생하지 않습니다. 상태는 변경되지 않으며, 실행 중(`RUNNING`) 또는 제출됨(`SUBMITTED`) 상태의 쿼리만 실제로 취소됩니다.

---

## 전체 흐름 코드 (Python)

```python
import os
import time
import requests

BASE = os.getenv(
    "DATA_GATEWAY_URL",
    "https://polaris-colab.sktelecom.com/api/data-gateway",
)
HEADERS = {"Content-Type": "application/json"}
MAX_POLL_SECONDS = 30 * 60  # Athena DML 쿼리 타임아웃: 30분
POLL_INTERVAL = 4  # 폴링 간격 (초) — 내부 시스템 과부하 방지를 위해 3~5초 권장

# 1. 쿼리 실행
start = requests.post(f"{BASE}/start-query-execution", headers=HEADERS, json={
    "query_string": "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
    "user_id": "1111903",
    "app_type": "app",
    "app_name": "my-data-app",
    "auth_key": os.getenv("DATA_GATEWAY_AUTH_KEY", "your-auth-key"),
})
start.raise_for_status()
start_data = start.json()

qid = start_data["query_execution_id"]
print(f"Started: {qid}")

# 2. 상태 폴링 (타임아웃 적용)
elapsed = 0
status = None
while elapsed < MAX_POLL_SECONDS:
    status_resp = requests.post(f"{BASE}/get-query-execution", headers=HEADERS, json={
        "query_execution_id": qid,
    }).json()
    status = status_resp["status"]
    print(f"Status: {status} ({elapsed}s elapsed)")
    if status == "SUCCEEDED":
        break
    elif status in ("FAILED", "CANCELLED"):
        print(f"Error: {status_resp.get('state_change_reason')}")
        break
    time.sleep(POLL_INTERVAL)
    elapsed += POLL_INTERVAL

if elapsed >= MAX_POLL_SECONDS and status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
    print("Timeout! Stopping query...")
    requests.post(f"{BASE}/stop-query-execution", headers=HEADERS, json={
        "query_execution_id": qid,
    })

# 3. 결과 조회 (max_results 로 페이지 크기 제한 — 504 Gateway Timeout 방지)
if status == "SUCCEEDED":
    result = requests.post(f"{BASE}/get-query-results", headers=HEADERS, json={
        "query_execution_id": qid,
        "max_results": 1000,  # 미지정 시 전체 fetch → 대용량이면 504 위험
    }).json()
    print(f"Columns: {[c['name'] for c in result['columns']]}")
    print(f"Row count: {len(result['rows'])}")
    for row in result["rows"][:5]:
        print(row)
    # 전체 결과가 필요하면 result["next_token"] 으로 다음 페이지를 이어서 조회
```

### 페이지네이션 예제 (Python)

```python
def get_all_results(qid, page_size=1000):
    """페이지네이션으로 전체 결과를 조회합니다."""
    all_rows = []
    columns = []
    next_token = None

    while True:
        body = {"query_execution_id": qid, "max_results": page_size}
        if next_token:
            body["next_token"] = next_token

        result = requests.post(
            f"{BASE}/get-query-results", headers=HEADERS, json=body,
        ).json()

        if not columns:
            columns = result["columns"]
        all_rows.extend(result["rows"])

        next_token = result.get("next_token")
        if not next_token:
            break

    return {"columns": columns, "rows": all_rows}
```

---

## 전체 흐름 코드 (Java)

Java 11+ `HttpClient`를 사용한 예제입니다. JSON 파싱에는 Jackson(`com.fasterxml.jackson.databind`)을 사용합니다.

```java
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;

public class DataGatewayExample {

    // 환경변수로 URL 관리 권장
    private static final String BASE = System.getenv().getOrDefault(
        "DATA_GATEWAY_URL",
        "https://polaris-colab.sktelecom.com/api/data-gateway"
    );
    private static final ObjectMapper mapper = new ObjectMapper();
    private static final long MAX_POLL_MS = 30 * 60 * 1000L; // 30분
    private static final long POLL_INTERVAL_MS = 4000L; // 3~5초 권장 (과부하 방지)
    private static final HttpClient client = HttpClient.newHttpClient();

    public static void main(String[] args) throws Exception {
        String authKey = System.getenv().getOrDefault("DATA_GATEWAY_AUTH_KEY", "your-auth-key");

        // 1. 쿼리 실행
        Map<String, String> startBody = Map.of(
            "query_string", "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
            "user_id", "1111903",
            "app_type", "app",
            "app_name", "my-data-app",
            "auth_key", authKey
        );

        Map<String, Object> startResp = post("/start-query-execution", startBody);
        String qid = (String) startResp.get("query_execution_id");
        System.out.println("Started: " + qid);

        // 2. 상태 폴링 (타임아웃 적용)
        String status;
        long elapsed = 0;
        while (true) {
            Map<String, Object> statusResp = post("/get-query-execution",
                Map.of("query_execution_id", qid));
            status = (String) statusResp.get("status");
            System.out.printf("Status: %s (%ds elapsed)%n", status, elapsed / 1000);

            if ("SUCCEEDED".equals(status)) {
                break;
            } else if ("FAILED".equals(status) || "CANCELLED".equals(status)) {
                System.out.println("Error: " + statusResp.get("state_change_reason"));
                break;
            }

            if (elapsed >= MAX_POLL_MS) {
                System.out.println("Timeout! Stopping query...");
                post("/stop-query-execution", Map.of("query_execution_id", qid));
                break;
            }

            Thread.sleep(POLL_INTERVAL_MS);
            elapsed += POLL_INTERVAL_MS;
        }

        // 3. 결과 조회 (max_results 로 페이지 크기 제한 — 504 Gateway Timeout 방지)
        if ("SUCCEEDED".equals(status)) {
            Map<String, Object> result = post("/get-query-results",
                Map.of("query_execution_id", qid, "max_results", 1000));

            @SuppressWarnings("unchecked")
            List<Map<String, String>> columns = (List<Map<String, String>>) result.get("columns");
            @SuppressWarnings("unchecked")
            List<List<String>> rows = (List<List<String>>) result.get("rows");

            System.out.println("Columns: " + columns.stream()
                .map(c -> c.get("name")).toList());
            System.out.println("Row count: " + rows.size());
            rows.stream().limit(5).forEach(System.out::println);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> post(String path, Map<String, ?> body) throws Exception {
        String json = mapper.writeValueAsString(body);
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(BASE + path))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(json))
            .timeout(Duration.ofSeconds(30))
            .build();

        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        return mapper.readValue(response.body(), Map.class);
    }
}
```

### Maven 의존성

```xml
<dependency>
    <groupId>com.fasterxml.jackson.core</groupId>
    <artifactId>jackson-databind</artifactId>
    <version>2.17.0</version>
</dependency>
```

---

## 전체 흐름 코드 (Node.js)

Node.js 18+ 내장 `fetch`를 사용한 예제입니다. Node.js 18 미만이면 `node-fetch` 패키지를 설치하세요.

```javascript
// 환경변수로 URL 관리 권장
const BASE = process.env.DATA_GATEWAY_URL ||
  "https://polaris-colab.sktelecom.com/api/data-gateway";
const AUTH_KEY = process.env.DATA_GATEWAY_AUTH_KEY || "your-auth-key";
const MAX_POLL_MS = 30 * 60 * 1000; // Athena DML 쿼리 타임아웃: 30분
const POLL_INTERVAL_MS = 4000; // 3~5초 권장 (과부하 방지)

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errorBody = await res.text();
    throw new Error(`HTTP ${res.status}: ${errorBody}`);
  }
  return res.json();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  // 1. 쿼리 실행
  const startResp = await post("/start-query-execution", {
    query_string: "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
    user_id: "1111903",
    app_type: "app",
    app_name: "my-data-app",
    auth_key: AUTH_KEY,
  });

  const qid = startResp.query_execution_id;
  console.log(`Started: ${qid}`);

  // 2. 상태 폴링 (타임아웃 적용)
  let status;
  let elapsed = 0;
  while (elapsed < MAX_POLL_MS) {
    const statusResp = await post("/get-query-execution", {
      query_execution_id: qid,
    });
    status = statusResp.status;
    console.log(`Status: ${status} (${elapsed / 1000}s elapsed)`);

    if (status === "SUCCEEDED") {
      break;
    } else if (status === "FAILED" || status === "CANCELLED") {
      console.log(`Error: ${statusResp.state_change_reason}`);
      break;
    }
    await sleep(POLL_INTERVAL_MS);
    elapsed += POLL_INTERVAL_MS;
  }

  if (elapsed >= MAX_POLL_MS && !["SUCCEEDED", "FAILED", "CANCELLED"].includes(status)) {
    console.log("Timeout! Stopping query...");
    await post("/stop-query-execution", { query_execution_id: qid });
  }

  // 3. 결과 조회 (max_results 로 페이지 크기 제한 — 504 Gateway Timeout 방지)
  if (status === "SUCCEEDED") {
    const result = await post("/get-query-results", {
      query_execution_id: qid,
      max_results: 1000,  // 미지정 시 전체 fetch → 대용량이면 504 위험
    });

    console.log("Columns:", result.columns.map((c) => c.name));
    console.log(`Row count: ${result.rows.length}`);
    result.rows.slice(0, 5).forEach((row) => console.log(row));
  }
}

main().catch(console.error);
```

### TypeScript 버전

TypeScript 프로젝트에서 사용할 경우 타입을 추가한 버전입니다.

```typescript
const BASE = process.env.DATA_GATEWAY_URL ||
  "https://polaris-colab.sktelecom.com/api/data-gateway";
const AUTH_KEY = process.env.DATA_GATEWAY_AUTH_KEY || "your-auth-key";
const MAX_POLL_MS = 30 * 60 * 1000;
const POLL_INTERVAL_MS = 4000; // 3~5초 권장 (과부하 방지)

interface Column {
  name: string;
  type: string;
}

interface StartQueryResponse {
  query_execution_id: string;
}

interface QueryExecutionResponse {
  query_execution_id: string;
  query: string;
  status: "SUBMITTED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  state_change_reason: string | null;
  submission_time: string;
  completion_time: string | null;
  data_scanned_bytes: number;
  execution_time_ms: number;
  output_location: string;
  workgroup: string;
}

interface QueryResultsResponse {
  query_execution_id: string;
  columns: Column[];
  rows: string[][];
  next_token: string | null;
}

/** 에러 응답 형식 */
interface ErrorDetail {
  error_type: "user_error" | "system_error";
  error_code: string;
  message: string;
  details: Record<string, unknown> | null;
  timestamp: string;
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errorBody = await res.text();
    throw new Error(`HTTP ${res.status}: ${errorBody}`);
  }
  return res.json() as Promise<T>;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main(): Promise<void> {
  // 1. 쿼리 실행
  const startResp = await post<StartQueryResponse>("/start-query-execution", {
    query_string: "SELECT * FROM cpm.base_demo_pred_monthly LIMIT 10",
    user_id: "1111903",
    app_type: "app",
    app_name: "my-data-app",
    auth_key: AUTH_KEY,
  });

  const qid = startResp.query_execution_id;
  console.log(`Started: ${qid}`);

  // 2. 상태 폴링 (타임아웃 적용)
  let status: QueryExecutionResponse["status"];
  let elapsed = 0;
  while (elapsed < MAX_POLL_MS) {
    const statusResp = await post<QueryExecutionResponse>("/get-query-execution", {
      query_execution_id: qid,
    });
    status = statusResp.status;
    console.log(`Status: ${status} (${elapsed / 1000}s elapsed)`);

    if (status === "SUCCEEDED") {
      break;
    } else if (status === "FAILED" || status === "CANCELLED") {
      console.log(`Error: ${statusResp.state_change_reason}`);
      break;
    }
    await sleep(POLL_INTERVAL_MS);
    elapsed += POLL_INTERVAL_MS;
  }

  if (elapsed >= MAX_POLL_MS && !["SUCCEEDED", "FAILED", "CANCELLED"].includes(status!)) {
    console.log("Timeout! Stopping query...");
    await post("/stop-query-execution", { query_execution_id: qid });
  }

  // 3. 결과 조회 (max_results 로 페이지 크기 제한 — 504 Gateway Timeout 방지)
  if (status! === "SUCCEEDED") {
    const result = await post<QueryResultsResponse>("/get-query-results", {
      query_execution_id: qid,
      max_results: 1000,  // 미지정 시 전체 fetch → 대용량이면 504 위험
    });

    console.log("Columns:", result.columns.map((c) => c.name));
    console.log(`Row count: ${result.rows.length}`);
    result.rows.slice(0, 5).forEach((row) => console.log(row));
  }
}

main().catch(console.error);
```

---

## 프론트엔드 주도 폴링 (대시보드 권장 패턴)

대시보드/SPA처럼 화면에서 쿼리를 실행할 때는, **백엔드가 한 번의 요청 안에서
`start → 폴링 → 결과`를 끝까지 기다리는(singleton/블로킹) 방식**을 쓰지 마세요.
대신 **폴링 루프를 프론트엔드가 직접 주도**하는 것을 권장합니다.

```
[FE] start-query-execution  → query_execution_id 받고 즉시 리턴 (대기 X)
        ↓
[FE] get-query-execution    → status 확인
        ↓ RUNNING/SUBMITTED 이면
[FE] 3~5초 대기 (setTimeout)
[FE] get-query-execution    → status 다시 확인   ┐
        ↓ ... 완료될 때까지 반복 ...             ┘ ← 폴링 루프는 FE 가 돈다
        ↓ SUCCEEDED 면
[FE] get-query-results      → 결과 렌더링
```

### 왜 프론트엔드에서 도는가

| 항목 | ❌ 백엔드 블로킹(singleton) | ✅ 프론트엔드 주도 폴링 |
|------|------------------------------|--------------------------|
| 폴링 주체 | 백엔드가 한 요청에서 끝까지 대기 | 프론트엔드가 단계별로 개별 호출 |
| 호출 단위 | `start + 폴링 + 결과` = 1콜(장시간) | `start` / `get-execution`(반복) / `get-results` 분리(짧은 콜) |
| 504 위험 | **높음** (장시간 1콜 → 게이트웨이 60초 초과) | **낮음** (각 콜이 수 초 안에 끝남) |
| UI | 응답 올 때까지 멈춤 | 논블로킹, 진행상태(로딩) 표시 가능 |
| 병렬 | 어려움 | `Promise.all` 로 여러 위젯 동시 로드 용이 |

### 🚨 보안 — `auth_key` 는 프론트엔드에 두지 않는다

"프론트엔드가 폴링을 주도한다"는 것은 **폴링 루프(타이머/반복 호출) 로직을 FE 가 갖는다**는
의미이지, FE 가 `auth_key` 를 들고 Data Gateway 를 직접 호출한다는 뜻이 **아닙니다.**
(이 문서 상단 "인증 방식" 의 `auth_key` 보안 규칙은 그대로 유효합니다.)

따라서 아래 구조를 사용하세요.

```
[Frontend]  ──(짧은 호출)──▶  [내 앱의 백엔드 프록시]  ──(auth_key 첨부)──▶  [Data Gateway]
   폴링 루프                     auth_key 보관(.env)            start/status/results
   (FE 가 주도)                  요청을 그대로 즉시 릴레이        (각 콜 즉시 반환)
```

- FE 는 **자기 앱 백엔드의 얇은 프록시 엔드포인트** 3개(start/status/results)만 호출합니다.
- 백엔드 프록시는 `auth_key` 를 환경변수에서 읽어 첨부하고 Data Gateway 응답을 **그대로 즉시 반환**합니다.
  (백엔드에서 폴링하지 않으므로 각 프록시 콜은 1초 내 종료 → 504 없음)
- 폴링 간격/타임아웃/중지 판단은 **FE 가** 관리합니다.

### React 예제 (커스텀 훅)

아래는 FE 가 자기 백엔드 프록시(`/api/dg/*`)를 호출하며 폴링하는 예시입니다.
`auth_key` 는 등장하지 않습니다 (백엔드 프록시가 첨부).

```typescript
import { useEffect, useRef, useState } from "react";

const POLL_INTERVAL_MS = 4000;          // 3~5초 권장
const MAX_POLL_MS = 30 * 60 * 1000;     // Athena DML 타임아웃: 30분

type Status = "SUBMITTED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

interface QueryResult {
  columns: { name: string; type: string }[];
  rows: string[][];
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// 내 앱 백엔드의 얇은 프록시 (auth_key 는 백엔드가 첨부)
async function post<T>(path: string, body: object, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api/dg${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

/**
 * 프론트엔드 주도 폴링 훅.
 * start → get-execution 반복 폴링 → SUCCEEDED 시 get-results 까지 수행한다.
 */
export function useAthenaQuery(sql: string | null) {
  const [status, setStatus] = useState<Status | "IDLE">("IDLE");
  const [data, setData] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sql) return;

    const ctrl = new AbortController();
    let cancelled = false;
    let qid: string | null = null;

    (async () => {
      try {
        setStatus("SUBMITTED");
        setData(null);
        setError(null);

        // 1) start — 즉시 query_execution_id 반환
        const started = await post<{ query_execution_id: string }>(
          "/start", { query_string: sql }, ctrl.signal,
        );
        qid = started.query_execution_id;

        // 2) FE 주도 폴링 (3~5초 간격, 최대 30분)
        const deadline = Date.now() + MAX_POLL_MS;
        while (!cancelled) {
          const s = await post<{ status: Status; state_change_reason: string | null }>(
            "/status", { query_execution_id: qid }, ctrl.signal,
          );
          setStatus(s.status);

          if (s.status === "SUCCEEDED") break;
          if (s.status === "FAILED" || s.status === "CANCELLED") {
            throw new Error(s.state_change_reason ?? `Query ${s.status}`);
          }
          if (Date.now() > deadline) {
            await post("/stop", { query_execution_id: qid }, ctrl.signal); // 타임아웃 → 중지
            throw new Error("Query timeout (30m)");
          }
          await sleep(POLL_INTERVAL_MS);
        }

        // 3) 결과 조회 (max_results 로 504 방지)
        if (!cancelled) {
          const result = await post<QueryResult>(
            "/results", { query_execution_id: qid, max_results: 1000 }, ctrl.signal,
          );
          setData(result);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();

    // 언마운트/조건 변경 시: 폴링 중단 + 실행 중 쿼리 중지
    return () => {
      cancelled = true;
      ctrl.abort();
      if (qid) {
        post("/stop", { query_execution_id: qid }).catch(() => {});
      }
    };
  }, [sql]);

  return { status, data, error };
}
```

### 여러 위젯 동시 로드 (병렬)

각 위젯이 위 훅을 독립적으로 쓰면 React 가 알아서 동시에 폴링합니다.
명령형으로 한 번에 모으려면 `Promise.all` 로 각 쿼리의 "start→폴링→결과" 헬퍼를 병렬 실행하세요.
(아래 "성능 최적화 — 병렬 조회" 참고)

> ⚠️ **주의**
> - **컴포넌트 언마운트/필터 변경 시 폴링을 반드시 중단**하고, 실행 중이면 `stop-query-execution` 으로 정리하세요. (위 훅의 cleanup 참고)
> - 폴링 간격은 **3~5초** 를 지키세요(과부하 방지). `setInterval` 보다, 이전 응답을 받은 뒤 다음 호출을 예약하는 방식(`await sleep` 또는 재귀 `setTimeout`)이 호출 중첩을 막아 안전합니다.
> - `get-results` 는 항상 `max_results`(예: 1000)를 지정해 페이지네이션하세요(504 방지).
> - `auth_key` 는 **절대 FE 코드/번들에 포함하지 마세요.** 반드시 백엔드 프록시가 첨부합니다.

### 결과 집계 시 413 방지

쿼리 결과를 화면에 그리기 전에 **집계(aggregation)**가 필요할 때, 결과 데이터를
FE → BE 로 POST body에 담아 보내면 **413 Request Entity Too Large** 가 발생합니다.
(ALB 기본 body size 제한: 1MB, 수천 건만 돼도 초과)

#### ❌ 잘못된 패턴 — FE가 결과를 받아서 BE에 다시 전송 (413 위험)

```
[FE] get-query-results → rows (수만 건)
[FE] POST /api/aggregate  body: { rows: [...수만 건...] }  ← 413 발생!
[BE] 집계 후 응답
```

```javascript
// ❌ 이렇게 하면 대용량 데이터에서 413 발생
const results = await fetch(`/api/query/results?id=${qid}`);
const data = await results.json();

// rows 를 다시 백엔드로 전송 — body size 제한 초과!
await fetch('/api/aggregate', {
  method: 'POST',
  body: JSON.stringify({ columns: data.columns, rows: data.rows }),  // 수 MB
});
```

#### ✅ 올바른 패턴 — BE가 query_execution_id로 직접 결과 조회 후 집계

```
[FE] start → 폴링 → SUCCEEDED 확인
[FE] GET /api/aggregate?id=<query_execution_id>   ← ID만 전달 (수 바이트)
[BE] get-query-results (페이지네이션) → 집계 → 응답
```

```javascript
// ✅ query_execution_id 만 전달 — 백엔드가 직접 Data Gateway 에서 결과를 가져옴
const aggRes = await fetch(`/api/aggregate?id=${qid}`);
const dashboard = await aggRes.json();
renderCharts(dashboard);
```

```python
# ✅ 백엔드: query_execution_id 로 Data Gateway 에서 직접 결과 fetch + 집계
@app.get("/api/aggregate")
def aggregate(id: str):
    all_rows = []
    columns = []
    next_token = None

    while True:
        page = get_query_results(id, max_results=1000, next_token=next_token)
        if not columns:
            columns = page["columns"]
        all_rows.extend(page["rows"])
        next_token = page.get("next_token")
        if not next_token:
            break

    # 서버에서 집계 후 경량 JSON 반환
    return compute_dashboard(columns, all_rows)
```

**핵심 원칙**: 대량 데이터는 항상 **서버 간(BE ↔ Data Gateway)** 에서 이동하고,
FE ↔ BE 간에는 **경량 파라미터(ID)와 집계 결과만** 오가게 하세요.

---

## 성능 최적화 — 병렬 조회 & 결과 캐시

대시보드처럼 **여러 쿼리를 한 화면에서 동시에 보여줘야 하는 경우**, 쿼리를 순차로 실행하면
화면 로딩이 매우 느려집니다. 각 쿼리가 `start → 폴링 → 결과`까지 수 초씩 걸리므로,
4개를 순차로 돌리면 그 시간이 그대로 합산됩니다.

아래 두 가지 패턴으로 체감 속도를 크게 개선할 수 있습니다.

| 패턴 | 효과 | 적용 상황 |
|------|------|-----------|
| **병렬 조회** (ThreadPoolExecutor 등) | N개 쿼리를 동시에 실행 → 전체 시간 ≈ 가장 느린 쿼리 1개 | 서로 독립적인 여러 쿼리를 한 번에 로드 |
| **결과 캐시** (TTL 60초) | 같은 조건 재조회 시 Athena 재실행 없이 즉시 반환 | 동일 필터로 반복 조회, 새로고침, 동시 사용자 |

> 💡 이 가이드의 예제는 `run_query(sql)` 처럼 **`start → 폴링 → 결과`까지 한 번에 처리하는 헬퍼**가
> 이미 있다고 가정합니다. (앞 절의 "전체 흐름 코드"를 함수로 감싼 형태) 그 헬퍼를
> 병렬 실행하거나 캐시로 감싸는 방식입니다.

---

### 1. 병렬 조회 (ThreadPoolExecutor 패턴)

서로 의존성이 없는 쿼리들은 한꺼번에 제출(submit)하고, 결과만 나중에 모아서(result)
받으면 됩니다. 핵심은 **"submit을 먼저 다 해두고, result는 나중에 모은다"** 입니다.

#### Python

```python
from concurrent.futures import ThreadPoolExecutor

# run_query(sql) 는 start → 폴링 → 결과까지 반환하는 헬퍼라고 가정
# (앞 절 "전체 흐름 코드 (Python)" 를 함수로 감싼 형태)

def load_dashboard(date_to, pgs, lts):
    # 4개의 독립 쿼리를 동시에 실행 → 전체 시간 ≈ 가장 느린 1개
    with ThreadPoolExecutor(max_workers=4) as ex:
        # ① submit: 즉시 반환되는 Future 핸들만 받아둔다 (블로킹 X)
        fk = ex.submit(fetch_kpi,    date_to, pgs, lts)
        ft = ex.submit(fetch_trend,  date_to, pgs, lts)
        fm = ex.submit(fetch_mtd,    date_to, pgs, lts)
        fd = ex.submit(fetch_detail, date_to, pgs, lts)

        # ② result: 여기서 각 작업 완료를 기다린다 (이미 병렬로 도는 중)
        kpi    = fk.result()
        trend  = ft.result()
        mtd    = fm.result()
        detail = fd.result()

    return kpi, trend, mtd, detail


def fetch_kpi(date_to, pgs, lts):
    """각 fetch 함수 내부에서 run_query 를 호출한다."""
    sql = f"SELECT new_cnt, churn_cnt FROM my_db.daily WHERE \"date\" = DATE '{date_to}'"
    return run_query(sql)
```

> ⚠️ **주의**
> - `max_workers` 는 동시에 띄울 쿼리 수에 맞춰 설정 (보통 3~8). Athena 동시 실행 한도(100)와
>   서버 부하를 고려해 과도하게 키우지 말 것.
> - `submit` 단계에서는 절대 블로킹되지 않습니다. `result()` 를 호출하는 순간 대기가 시작되므로,
>   **모든 submit 을 먼저 끝낸 뒤 result 를 모아야** 병렬 효과가 납니다.
>   (submit → result → submit → result 순서로 하면 사실상 순차 실행이 됩니다.)
> - 한 쿼리가 실패하면 해당 `result()` 에서 예외가 발생합니다. 화면 전체가 죽지 않게
>   개별 try/except 로 감싸는 것을 권장합니다.

#### Java

Java 에서는 `ExecutorService` + `Future` 또는 `CompletableFuture` 를 사용합니다.

```java
import java.util.List;
import java.util.concurrent.*;

ExecutorService pool = Executors.newFixedThreadPool(4);
try {
    // ① submit: Future 핸들만 받아둔다
    Future<Map<String, Object>> fk = pool.submit(() -> runQuery(sqlKpi));
    Future<Map<String, Object>> ft = pool.submit(() -> runQuery(sqlTrend));
    Future<Map<String, Object>> fm = pool.submit(() -> runQuery(sqlMtd));
    Future<Map<String, Object>> fd = pool.submit(() -> runQuery(sqlDetail));

    // ② get: 완료를 기다려 결과 수집 (이미 병렬로 실행 중)
    Map<String, Object> kpi    = fk.get();
    Map<String, Object> trend  = ft.get();
    Map<String, Object> mtd    = fm.get();
    Map<String, Object> detail = fd.get();
} finally {
    pool.shutdown();  // 풀 정리 필수
}
```

`CompletableFuture` 로 더 간결하게 쓸 수도 있습니다.

```java
import java.util.concurrent.CompletableFuture;

CompletableFuture<Map<String, Object>> kpi   = CompletableFuture.supplyAsync(() -> runQuery(sqlKpi));
CompletableFuture<Map<String, Object>> trend = CompletableFuture.supplyAsync(() -> runQuery(sqlTrend));
CompletableFuture<Map<String, Object>> mtd   = CompletableFuture.supplyAsync(() -> runQuery(sqlMtd));

// 모두 끝날 때까지 대기
CompletableFuture.allOf(kpi, trend, mtd).join();
System.out.println(kpi.join());
```

#### Node.js / TypeScript

JavaScript 는 단일 스레드지만 **I/O(네트워크) 는 비동기로 동시에 진행**되므로,
`Promise.all` 로 여러 쿼리를 동시에 실행하면 됩니다. (별도 스레드 풀 불필요)

```javascript
// runQuery(sql) 는 start → 폴링 → 결과까지 반환하는 async 헬퍼라고 가정

async function loadDashboard(dateTo, pgs, lts) {
  // 4개 쿼리를 동시에 시작하고, 모두 끝날 때까지 한 번에 대기
  const [kpi, trend, mtd, detail] = await Promise.all([
    fetchKpi(dateTo, pgs, lts),
    fetchTrend(dateTo, pgs, lts),
    fetchMtd(dateTo, pgs, lts),
    fetchDetail(dateTo, pgs, lts),
  ]);

  return { kpi, trend, mtd, detail };
}
```

> ⚠️ `Promise.all` 은 **하나라도 reject 되면 전체가 실패**합니다. 일부 실패를 허용하고
> 싶다면 `Promise.allSettled` 를 사용해 성공/실패를 개별 처리하세요.

```typescript
const results = await Promise.allSettled([
  fetchKpi(dateTo, pgs, lts),
  fetchTrend(dateTo, pgs, lts),
]);

for (const r of results) {
  if (r.status === "fulfilled") {
    console.log("OK:", r.value);
  } else {
    console.error("FAIL:", r.reason);
  }
}
```

---

### 2. 결과 캐시 (TTL 60초)

같은 조건으로 반복 조회할 때마다 Athena 를 다시 실행하면 느리고 비용도 듭니다.
**쿼리 문자열(또는 조회 조건)을 키로 결과를 60초간 캐시**해 두면, 그 사이 동일 조회는
즉시 캐시 값을 반환합니다. 60초가 지나면 캐시가 만료되어 다시 Athena 를 실행합니다.

핵심 동작:
1. 캐시 키 = SQL 문자열 (또는 조회 조건 조합)
2. 캐시에 있고 `현재시각 - 저장시각 < 60초` → **저장된 결과 즉시 반환**
3. 없거나 만료 → 실제 쿼리 실행 후 결과를 `(결과, 현재시각)` 으로 저장

#### Python

```python
import time

_CACHE: dict[str, tuple[float, object]] = {}   # key -> (저장시각, 결과)
_CACHE_TTL = 60  # 초

def run_query_cached(sql: str):
    """SQL 단위로 결과를 60초간 캐시한다."""
    now = time.time()
    hit = _CACHE.get(sql)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]                # 캐시 히트 → 즉시 반환

    result = run_query(sql)          # 캐시 미스 → 실제 실행
    _CACHE[sql] = (now, result)
    return result
```

`functools.lru_cache` 는 TTL 을 지원하지 않으므로, 위처럼 직접 시각을 비교하거나
`cachetools.TTLCache` 를 사용하면 됩니다.

```python
# cachetools 사용 시 (pip install cachetools)
from cachetools import TTLCache

_TTL_CACHE = TTLCache(maxsize=256, ttl=60)  # 60초 자동 만료

def run_query_cached(sql: str):
    if sql in _TTL_CACHE:
        return _TTL_CACHE[sql]
    result = run_query(sql)
    _TTL_CACHE[sql] = result
    return result
```

> 💡 병렬 조회와 함께 쓰면 효과가 배가됩니다. 각 `fetch_*` 함수가 `run_query` 대신
> `run_query_cached` 를 호출하도록 바꾸기만 하면 됩니다.

#### Java

`ConcurrentHashMap` 으로 thread-safe 한 TTL 캐시를 간단히 구현할 수 있습니다.

```java
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

private static final long CACHE_TTL_MS = 60_000L;  // 60초
private static final Map<String, long[]> CACHE_TIME = new ConcurrentHashMap<>();
private static final Map<String, Object>  CACHE_DATA = new ConcurrentHashMap<>();

static Object runQueryCached(String sql) {
    long now = System.currentTimeMillis();
    long[] ts = CACHE_TIME.get(sql);
    if (ts != null && now - ts[0] < CACHE_TTL_MS) {
        return CACHE_DATA.get(sql);   // 캐시 히트
    }
    Object result = runQuery(sql);    // 캐시 미스
    CACHE_TIME.put(sql, new long[]{now});
    CACHE_DATA.put(sql, result);
    return result;
}
```

운영 환경에서는 Caffeine 같은 캐시 라이브러리 사용을 권장합니다.

```java
// Caffeine 사용 시 (com.github.ben-manes.caffeine:caffeine)
import com.github.benmanes.caffeine.cache.*;
import java.time.Duration;

Cache<String, Object> cache = Caffeine.newBuilder()
    .expireAfterWrite(Duration.ofSeconds(60))
    .maximumSize(256)
    .build();

Object result = cache.get(sql, key -> runQuery(key));  // 없으면 실행 후 자동 저장
```

#### Node.js / TypeScript

```typescript
interface CacheEntry {
  at: number;          // 저장 시각 (ms)
  data: unknown;
}

const CACHE = new Map<string, CacheEntry>();
const CACHE_TTL_MS = 60_000;  // 60초

async function runQueryCached(sql: string): Promise<unknown> {
  const now = Date.now();
  const hit = CACHE.get(sql);
  if (hit && now - hit.at < CACHE_TTL_MS) {
    return hit.data;            // 캐시 히트 → 즉시 반환
  }

  const data = await runQuery(sql);  // 캐시 미스 → 실제 실행
  CACHE.set(sql, { at: now, data });
  return data;
}
```

> ⚠️ **공통 주의사항**
> - 위 예제들은 **프로세스 메모리 캐시**라 인스턴스가 여러 개면 인스턴스마다 따로 캐시됩니다.
>   여러 인스턴스가 캐시를 공유해야 하면 Redis 등 외부 캐시를 사용하세요.
> - 캐시 키에는 **조회 조건을 모두 포함**해야 합니다. (날짜, 필터, 페이지 등) SQL 문자열을
>   그대로 키로 쓰면 조건이 바뀔 때 자동으로 키가 달라져 안전합니다.
> - 실시간성이 중요한 데이터는 TTL 을 짧게(혹은 캐시 미적용), 변동이 적은 집계성 데이터는
>   더 길게 잡아 상황에 맞게 조정하세요.
> - 메모리 누수를 막기 위해 `maxsize` / `maximumSize` 등 **최대 항목 수 제한**을 두는 것이 좋습니다.

---

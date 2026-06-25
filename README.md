# MNO Device Sales Dashboard

단말(휴대폰) 판매량을 **본사 관점**(전사 + 본부별 + SKU별)으로 보여주는 대시보드.
SK텔레콤 사내 **Polaris Colab**에 배포되며, startup에 집계 마트
(`sandbox_db_max.device_sales_summary_daily2`)를 **awswrangler로 Athena 조회 → pandas
메모리 캐시**에 적재하고, 모든 화면은 메모리에서 즉시 집계합니다(요청마다 Athena 호출 X).

## 스택

- Python 3.12 · FastAPI · Uvicorn · 단일 HTML SPA(**라이트 기본 + 🌙/☀️ 다크 토글**, CSS 변수 토큰화 + localStorage)
- 데이터: **awswrangler(`wr.athena`) → pandas 메모리 캐시** (최근 24개월)
- Docker (`python:3.12-slim`), Polaris Colab (port 8080, `/health`)

## 구조

```
mno-device-sales/
├── Dockerfile / requirements.txt / .dockerignore / .env.example
├── CLAUDE.md                 # 세션 컨텍스트 (스펙·배포·아키텍처)
├── backend/
│   ├── main.py               # FastAPI: startup load_mart + /health /api/health /api/status /api/brief /api/refresh
│   ├── data.py               # 메모리 캐시: load_mart(awswrangler/mock) · get_df · refresh
│   └── aggregate.py          # build_brief(df, exec_ym) — pandas로 6탭 집계
└── frontend/
    └── index.html            # 단일 SPA (6탭 전체 UI, 라이트 기본)
```

## 6 탭

1. 전사 개요 · 2. S26군 SKU · 3. IP17군 SKU · 4. 본부별 분석 · 5. 알림 · 6. 본부 매트릭스

핵심 지표 **과/과소 지수 = 본부내비율 − 전사비중** (양수=과다/초록, 음수=과소/빨강).

## 로컬 실행 (mock 모드)

`ATHENA_OUTPUT_LOCATION`이 없거나 `USE_MOCK=1`이면 자동으로 mock DataFrame으로 동작 →
사내망/AWS 없이 6탭 UI 확인 가능.

```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.10+ (배포 3.12)
pip install -r requirements.txt
USE_MOCK=1 uvicorn backend.main:app --reload --port 8080
# → http://localhost:8080  (mock 데이터로 6탭 렌더)
```

> 참고: 코드가 `X | None` 타입 표기를 사용하므로 **Python 3.10+** 에서 실행. 데이터 로직만 단독 검증:
> `python -c "from backend.data import load_mart; from backend.aggregate import build_brief; print(build_brief(load_mart()))"`

## 배포 (Polaris Colab)

1. 사내 GitLab `main`에 push → Polaris 앱이 빌드/배포
2. Polaris 포털 **ENV_VARS**: `AWS_REGION` / `ATHENA_OUTPUT_LOCATION` / `DATABASE` / `MART_TABLE_NAME`
   (+ AWS 자격증명은 역할/키 표준 방식). 마트(v3.3, 24개월)는 사용자가 production에서 적재.
3. URL: `https://mno-device-sales.colab-mydesk.sktelecom.com`

## Remotes

| remote | URL |
|---|---|
| `origin` (GitHub) | https://github.com/jenn25ng/jy-mno-device-sales.git |
| `gitlab` (사내) | https://gitlab.tde.sktelecom.com/CDS/orbit/colab/user-apps/mno-device-sales.git |

## 상태

6탭 UI(라이트 기본) + awswrangler 메모리 캐시 데이터 계층 + pandas 집계 와이어링 완료(mock 검증).
다음: 정책팀 샘플에 맞춘 탭별 위젯 정밀화 / 실제 마트 연결(사용자 배포 시).
자세한 내용은 `CLAUDE.md` 참고.

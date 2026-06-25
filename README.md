# MNO Device Sales Dashboard

단말(휴대폰) 판매량을 **본사 관점**(전사 + 본부별 + SKU별)으로 보여주는 대시보드.
SK텔레콤 사내 **Polaris Colab**에 배포되며, 데이터는 **Data Gateway API**를 통해
집계 마트(`sandbox_db_max.device_sales_summary_daily`)에서 조회합니다.

> 자매 프로젝트 `mno-ltv-monitor`와 동일한 스택·배포 패턴(FastAPI + 단일 HTML SPA).

## 스택

- Python 3.12 · FastAPI · Uvicorn · 단일 HTML SPA(다크 테마)
- Docker (`python:3.12-slim`), Polaris Colab (port 8080, `/health`)
- 데이터: Polaris Data Gateway (Athena, `SELECT`만)

## 구조

```
mno-device-sales/
├── Dockerfile / requirements.txt / .dockerignore / .env.example
├── CLAUDE.md                 # 세션 컨텍스트 (스펙·배포·Phase)
├── backend/
│   ├── main.py               # FastAPI: /health /api/status /api/brief /api/refresh
│   ├── data_gateway.py       # Polaris Gateway 클라이언트 (검증된 재사용)
│   ├── data_loader.py        # env 해석 · SQL 빌드 · fetch (mock fallback)
│   └── data_pipeline.py      # mock_rows + build_brief (행 → 6탭 집계)
└── frontend/
    └── index.html            # 단일 SPA (6탭 전체 UI)
```

## 6 탭

1. 전사 개요 · 2. S26군 SKU · 3. IP17군 SKU · 4. 본부별 분석 · 5. 알림 · 6. 본부 매트릭스

핵심 지표 **과/과소 지수 = 본부내비율 − 전사비중** (양수=과다/초록, 음수=과소/빨강).

## 로컬 실행 (mock 모드)

`auth_key`가 없으면 자동으로 mock 데이터로 동작하므로 사내망 없이도 UI 확인 가능.

```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.12 권장
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8080
# → http://localhost:8080  (mock 데이터로 6탭 렌더)
```

> 참고: 코드가 `X | None` 타입 표기를 사용하므로 **Python 3.10+** 에서 실행하세요
> (배포 컨테이너는 3.12). 로컬이 3.9면 데이터 로직만 단독 검증 가능:
> `python -c "from backend.data_pipeline import mock_rows, build_brief; ..."`

## 배포 (Polaris Colab)

1. 사내 GitLab `main`에 push → Polaris 앱이 빌드/배포
2. Polaris 포털 **ENV_VARS**에 `auth_key` / `user_id` / `app_name` / `database` (소문자) +
   `SOURCE_TABLE` 주입
3. URL: `https://mno-device-sales.colab-mydesk.sktelecom.com`

## Remotes

| remote | URL |
|---|---|
| `origin` (GitHub) | https://github.com/jenn25ng/jy-mno-device-sales.git |
| `gitlab` (사내) | https://gitlab.tde.sktelecom.com/CDS/orbit/colab/user-apps/mno-device-sales.git |

## 상태

**Phase A (scaffold)** — 구조·배포 설정·mock 동작 완료.
다음: B(단말군 매핑 확정) → C(실제 gateway 쿼리) → D(차트 정교화) → E(알림 룰) → F(배포).
자세한 진행은 `CLAUDE.md` 참고.

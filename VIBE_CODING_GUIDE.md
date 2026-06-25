# Polaris Colab 앱 개발 가이드

## AI IDE 적용 방법

구분선(`---`) 이후 내용을 아래 파일로 프로젝트 루트에 복사하세요.

- **Cursor**: `.cursor/rules/colab.mdc` 또는 `.cursorrules`
- **GitHub Copilot**: `.github/copilot-instructions.md`
- **Claude Code**: `CLAUDE.md`
- **OpenAI Codex**: `AGENTS.md`
- **Kiro**: `.kiro/steering/VIBE_CODING_GUIDE.md`
- **Windsurf**: `.windsurfrules`

---

# Polaris Colab 배포 규칙

이 앱은 Polaris Colab 플랫폼에서 컨테이너로 호스팅됩니다.

## 핵심 규칙 (MUST)

1. 서버는 반드시 `0.0.0.0:8080`에서 리슨. 다른 포트 사용 시 배포 실패.
2. `GET /health` 엔드포인트 필수. 없으면 컨테이너가 반복 재시작됨.
3. DB 접속 정보는 환경변수로 읽기. 하드코딩 시 배포 환경에서 연결 불가.

## 포트 8080 설정

```python
# Python Flask
app.run(host="0.0.0.0", port=8080)
# Python FastAPI
uvicorn.run(app, host="0.0.0.0", port=8080)
```

```javascript
// Express
app.listen(8080, "0.0.0.0", () => {});
```

## 헬스체크 엔드포인트

```python
# Flask
@app.route("/health")
def health():
    return "ok", 200

# FastAPI
@app.get("/health")
def health():
    return {"status": "ok"}
```

```javascript
// Express
app.get("/health", (req, res) => res.send("ok"));
```

## DB 연결 (환경변수 MUST)

환경변수: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
MariaDB 포트 3306, PostgreSQL 포트 5432

```python
import os
db_host = os.getenv("DB_HOST")
db_port = os.getenv("DB_PORT", "3306")
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_name = os.getenv("DB_NAME")
```

```javascript
const dbHost = process.env.DB_HOST;
const dbPort = process.env.DB_PORT || "3306";
const dbUser = process.env.DB_USER;
const dbPassword = process.env.DB_PASSWORD;
const dbName = process.env.DB_NAME;
```

## NEVER DO

```python
app.run()                          # 기본 포트 5000 사용 -> 배포 실패
app.run(host="localhost")          # 외부 접속 불가
conn = pymysql.connect(host="10.20.30.40", password="secret")  # 하드코딩
open("/data/file.csv", "w")       # 재시작 시 소멸, /tmp만 사용
```

```html
<!-- 외부 CDN 사용 — 외부망 차단으로 로드 실패 -->
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
```

## Dockerfile (MUST — 직접 작성 권장)

자동 생성 Dockerfile은 프로젝트 구조를 잘못 인식하여 배포 실패를 유발할 수 있습니다. **반드시 직접 작성하세요.**

### Python 프로젝트

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

USER 1000

CMD ["python", "app.py"]
```

### Node.js 프로젝트

```dockerfile
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --only=production

COPY . .

EXPOSE 8080

USER 1000

CMD ["node", "index.js"]
```

### .dockerignore (MUST)

로컬 개발 환경 파일이 이미지에 포함되면 빌드 실패 또는 용량 초과가 발생합니다.

```
.venv
node_modules
.git
__pycache__
*.pyc
*.md
.env
```

## 네트워크 제약 (MUST — 중요)

**배포 환경(컨테이너 런타임)은 외부망 연결이 차단되어 있습니다.**

이로 인해 아래 사항을 반드시 준수해야 합니다:

| 제약 | 설명 |
|------|------|
| **CDN 사용 불가** | 브라우저에서 외부 CDN으로 JS/CSS 라이브러리를 로드할 수 없음. 모든 프론트엔드 라이브러리는 **빌드 시점에 번들에 포함**해야 함. |
| **런타임 pip/npm install 불가** | 컨테이너 실행 중 `pip install`이나 `npm install` 수행 불가. **Dockerfile 빌드 단계에서 모든 의존성을 설치** 완료해야 함. |
| **외부 API 호출 제한** | 컨테이너에서 인터넷 외부 서비스(공개 API, 외부 webhook 등)로의 아웃바운드 요청 불가. 내부망 서비스만 호출 가능. |

### ✅ 올바른 방법 — 빌드 시점에 모든 의존성 포함

```dockerfile
# Dockerfile 에서 빌드 단계에 의존성 설치 완료
FROM node:20-slim AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci                          # 빌드 시점에 설치 (외부망 접근 가능)
COPY . .
RUN npm run build                   # 번들에 모든 라이브러리 포함

FROM node:20-slim
WORKDIR /app
COPY --from=builder /app .
EXPOSE 8080
USER 1000
CMD ["node", "server.js"]
```

### ❌ 금지 — 런타임에 외부 리소스 의존

```html
<!-- CDN 로드 — 외부망 차단으로 로드 실패 -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR" rel="stylesheet">
```

```dockerfile
# 런타임에 설치 시도 — 외부망 차단으로 실패
CMD ["sh", "-c", "pip install flask && python app.py"]
```

### 프론트엔드 라이브러리 사용 방법

CDN 대신 `npm install`로 패키지를 설치하고, 빌드 시 번들에 포함시키세요.

```bash
# 로컬에서 패키지 설치 → package.json 에 기록
npm install chart.js
npm install @fontsource/noto-sans-kr   # 웹폰트도 npm 패키지로
```

```javascript
// 코드에서 import → 빌드 시 번들에 포함됨
import Chart from "chart.js/auto";
import "@fontsource/noto-sans-kr";
```

## 기타 제약

- 앱은 non-root(UID 1000)로 실행됨. root 권한 필요한 작업 불가.
- 파일 쓰기는 `/tmp`만 가능. 영구 저장은 DB 사용.
- 의존성 파일(`requirements.txt` 또는 `package.json`) 필수. 없으면 빌드 실패.
- Node.js 프로젝트는 `package-lock.json`을 반드시 repo에 포함. 없으면 의존성 버전 충돌로 빌드 실패할 수 있음.
- SPA(React/Vite/Svelte)는 `base: '/'`로 설정.

## 배포 이미지 보안 제약사항

### 베이스 이미지

- Dockerfile 작성 시 **최신 안정 버전의 베이스 이미지**를 사용하세요.
- 오래된 이미지는 알려진 CVE(보안 취약점)가 패치되지 않은 상태일 수 있습니다.

```dockerfile
# ✅ 최신 안정 버전 태그 명시
FROM python:3.12-slim
FROM node:20-slim

# ❌ 오래된 버전 또는 태그 미지정 (예측 불가)
FROM python:3.8
FROM node:16
FROM ubuntu:20.04
```

### 패키지 취약점 점검 (MUST)

의존성 패키지를 결정할 때 **반드시 취약점 점검을 수행**하고, 취약한 버전이 발견되면 사용하지 마세요.

| 언어 | 점검 명령 | 설명 |
|------|-----------|------|
| Node.js | `npm audit` | 알려진 취약점이 있는 패키지 탐지 |
| Node.js | `npm audit fix` | 자동으로 안전한 버전으로 업데이트 |
| Python | `pip-audit` | Python 패키지 취약점 점검 (`pip install pip-audit`) |
| Python | `safety check` | requirements.txt 기반 취약점 스캔 |

#### 점검 절차

```bash
# Node.js — 취약점이 있으면 배포 전 반드시 해결
npm audit
# critical/high 취약점이 있으면 배포 금지
npm audit --audit-level=high

# Python — pip-audit 사용
pip-audit -r requirements.txt
```

#### 규칙

1. **`critical` 또는 `high` 등급 취약점이 있는 패키지는 사용 금지** — 안전한 버전으로 업그레이드하거나 대체 패키지를 사용하세요.
2. Dockerfile 빌드 전에 로컬에서 취약점 점검을 완료하세요.
3. 점검 결과에 수정 불가능한 취약점이 남아있다면, 해당 패키지의 필요성을 재검토하세요.

## 의존성 버전 관리 (MUST)

배포 환경의 빌드 실패 원인 1순위는 **의존성 버전 불일치**입니다. 버전을 정확히 고정하지 않으면 로컬에서는 동작하던 코드가 빌드 시점에는 깨질 수 있습니다.

### 핵심 규칙

1. **모든 의존성 버전을 명시적으로 고정** (`>=`, `^`, `~`, latest 사용 금지).
2. **빌드 전 로컬에서 클린 환경으로 충분히 검증**.
3. **lock 파일을 반드시 repo에 포함** (`package-lock.json`, `poetry.lock`, `Pipfile.lock`).

### Python 의존성

```text
# requirements.txt — 정확한 버전 명시
fastapi==0.109.0
uvicorn==0.27.0
sqlalchemy==2.0.25
pymysql==1.1.0
pydantic==2.5.3
```

```text
# 잘못된 예시 (NEVER DO)
fastapi              # 버전 미명시 — 배포 시점의 latest 가 깨질 수 있음
fastapi>=0.100       # 범위 지정 — 환경마다 다른 버전 설치
fastapi~=0.109       # 호환 버전 — 빌드마다 변경 가능
```

**검증 명령**:
```bash
# 클린 환경에서 새로 설치하여 확인
python -m venv /tmp/test-env
/tmp/test-env/bin/pip install -r requirements.txt
/tmp/test-env/bin/python app.py
```

### Node.js 의존성

```json
// package.json — 정확한 버전 명시 (^, ~ 제거 권장)
{
  "dependencies": {
    "express": "4.18.2",
    "axios": "1.6.5",
    "pg": "8.11.3"
  },
  "engines": {
    "node": "20.x"
  }
}
```

```json
// 잘못된 예시 (NEVER DO)
{
  "dependencies": {
    "express": "*",         // 매번 latest — 깨질 수 있음
    "axios": "^1.0.0",      // 1.x.x 모두 허용 — 환경별 차이 발생
    "pg": "latest"          // 절대 사용 금지
  }
}
```

**검증 명령**:
```bash
# package-lock.json 반드시 포함
npm ci   # lock 파일 기반 정확 설치 (npm install 아님)

# 클린 빌드 확인
rm -rf node_modules
npm ci
npm run build
```

### 빌드 전 사전 테스트 체크리스트

배포 트리거 전에 반드시 로컬에서 확인:

- [ ] 모든 의존성 버전이 `==` 또는 정확한 숫자로 고정되어 있는가?
- [ ] lock 파일(`package-lock.json` / `requirements.txt` 핀 / `poetry.lock`)이 repo에 커밋되어 있는가?
- [ ] **클린 환경**에서 의존성을 새로 설치 → 정상 빌드되는가?
- [ ] 헬스체크(`GET /health`)가 로컬 실행 시 200을 반환하는가?
- [ ] (선택) Docker 가 있다면 이미지를 빌드해 컨테이너에서도 검증

### 검증 방법: Docker 없이 (권장 — 모두에게 가능)

대부분의 의존성/실행 문제는 **클린 가상환경**에서 잡을 수 있습니다. Docker 없이도 충분합니다.

**Python — venv 클린 검증**

```bash
# 기존 venv 영향 없는 임시 환경 생성
python -m venv /tmp/clean-env
source /tmp/clean-env/bin/activate

# 의존성 설치 (lock 또는 requirements.txt 기준)
pip install --no-cache-dir -r requirements.txt

# 앱 실행 + 헬스체크
python app.py &
APP_PID=$!
sleep 3
curl -f http://localhost:8080/health && echo "OK" || echo "FAILED"
kill $APP_PID

deactivate
rm -rf /tmp/clean-env
```

**Node.js — 클린 검증**

```bash
# 기존 node_modules 제거 후 lock 기준 설치
rm -rf node_modules
npm ci   # package-lock.json 기반 정확 설치

# 빌드 (SPA 인 경우)
npm run build

# 앱 실행 + 헬스체크
node index.js &
APP_PID=$!
sleep 3
curl -f http://localhost:8080/health && echo "OK" || echo "FAILED"
kill $APP_PID
```

**Node 버전 강제 (nvm 사용 시)**

```bash
# Dockerfile 의 base image 와 일치시켜 검증
nvm use 20
node --version   # v20.x.x 확인
```

### 검증 방법: Docker 가 있다면 (선택)

Docker 가 설치되어 있다면 배포 환경과 가장 비슷한 조건에서 검증할 수 있습니다. 없어도 위 클린 환경 검증으로 충분히 대체 가능.

```bash
docker build -t myapp:test .
docker run --rm -d -p 8080:8080 --name myapp-test myapp:test
sleep 3
curl -f http://localhost:8080/health || echo "FAILED"
docker logs myapp-test
docker stop myapp-test
```

### Docker 없이도 잡을 수 있는 / 잡기 어려운 문제

| 문제 유형 | venv/npm 검증 | Docker 검증 필요 |
|---|---|---|
| 의존성 버전 충돌 | ✅ 잡힘 | - |
| 누락된 패키지 | ✅ 잡힘 | - |
| 코드 import 에러 | ✅ 잡힘 | - |
| `localhost` 바인딩 실수 | ✅ 잡힘 | - |
| 헬스체크 미구현 | ✅ 잡힘 | - |
| Base image OS 라이브러리 누락 | ❌ 못 잡음 | ✅ 필요 |
| Non-root(UID 1000) 권한 문제 | ❌ 못 잡음 | ✅ 필요 |
| `/run`, `/var` 쓰기 권한 문제 | ❌ 못 잡음 | ✅ 필요 |

> Docker 없이도 80% 이상의 문제는 사전에 잡을 수 있습니다.
> Non-root 권한 / OS 라이브러리 관련 문제는 배포 후 로그에서 빠르게 확인하세요.

### 자주 발생하는 실패 사례

| 증상 | 원인 | 해결 |
|---|---|---|
| 로컬은 OK, 배포 실패 | 의존성 버전 미고정 | `==` 로 정확히 고정 |
| `npm ci` 에러 | lock 파일 누락/손상 | `package-lock.json` 재생성 후 커밋 |
| Python `ModuleNotFoundError` | 빠진 패키지 | 클린 venv 에서 검증 |
| 다른 Python/Node 버전 | base image 불일치 | Dockerfile 의 base image 버전 명시 (`python:3.12-slim`, `node:20-slim`) |

## 자기검증 체크리스트

코드 작성 후 확인:
- [ ] 서버가 `0.0.0.0:8080`에서 리슨하는가?
- [ ] `GET /health`가 200을 반환하는가?
- [ ] DB 정보를 환경변수에서 읽는가?
- [ ] `localhost`로 바인딩하고 있지 않은가?
- [ ] `requirements.txt` 또는 `package.json`이 루트에 있는가?
- [ ] 모든 의존성 버전이 `==` 또는 정확한 숫자로 고정되어 있는가? (`>=`, `^`, `~`, `latest` 금지)
- [ ] Node.js 프로젝트라면 `package-lock.json`이 repo에 포함되어 있는가?
- [ ] 클린 환경에서 의존성을 새로 설치하여 빌드/실행 검증을 마쳤는가?
- [ ] `Dockerfile`이 루트에 있고, `EXPOSE 8080`과 `USER 1000`이 포함되어 있는가?
- [ ] `.dockerignore`에 `.venv`, `node_modules`, `.git`이 포함되어 있는가?

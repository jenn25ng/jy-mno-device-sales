# 특정 사번만 접근 허용하기 (사번 allowlist)

> Colab mydesk 뒤 앱에서 **지정한 사번만** 화면을 보게 막는 방법. SSO 프록시가 넣어주는 `x-auth-user`(사번)를 서버에서 명단과 대조.

## 원리
```
[사용자] → [mydesk SSO 프록시] --(x-auth-user=사번 주입)--> [앱: 명단 대조 → 밖이면 403]
```
- 자체 로그인·암호 **불필요**(SSO가 이미 인증). 서버에서 사번만 확인하면 됨.

## ⭐ 전제 — 헤더 위조 방지 확인 (필수, 1분)
프록시가 **클라이언트가 보낸 가짜 헤더를 덮어써야** 안전. 배포 앱에서 F12 콘솔(`allow pasting` 후):
```js
fetch('/api/me', { headers: { 'x-auth-user': '0000000' } }).then(r=>r.json()).then(console.log)
```
- 결과가 **본인 실제 사번** → 프록시가 덮어씀 = **안전** ✅ (진행)
- 결과가 **`0000000`(가짜)** → 통과됨 = allowlist 뚫림 ❌ (IdP 인증 등 다른 방법 필요)

## 구현 (FastAPI 예시)
```python
import os
from starlette.responses import HTMLResponse

ALLOWED = {s.strip() for s in os.getenv("ALLOWED_SABUNS", "").split(",") if s.strip()}

@app.middleware("http")
async def sabun_gate(request, call_next):
    if ALLOWED:                                   # 비어 있으면 게이트 OFF(로컬/개발 편의)
        sabun = (request.headers.get("x-auth-user") or "").strip()
        if sabun not in ALLOWED:
            return HTMLResponse("<h3>접근 권한이 없습니다.</h3>", status_code=403)
    return await call_next(request)
```
- 모든 요청(페이지 + `/api/*`)에 적용 → 명단 밖은 어디도 못 봄.
- `health` 등 헬스체크 경로는 예외 필요 시 `request.url.path`로 통과 처리.

## 명단 관리 (env)
Polaris **Environment** 탭에 추가:
```
ALLOWED_SABUNS = 1112917,2233445,3344556
```
- 추가/제거 = env 수정 후 재시작. **관리자 콘솔 불필요**(공격 표면만 늘어남).
- 값이 **없으면 게이트 OFF** → 로컬/mock에선 헤더 없이도 자동 통과(개발 편의).

## 주의
- **반드시 서버에서 막을 것.** 프론트에서 숨기면 = 우회 가능.
- 시크릿(`auth_key` 등)은 계속 **env로만**, 코드/문서에 절대 X.
- 소스코드 노출 통제는 **GitLab repo 권한**으로 (프론트 JS는 원리상 브라우저에 노출되나 비밀 없음).

## 한 줄 요약
`ALLOWED_SABUNS` env에 사번 나열 → 미들웨어가 `x-auth-user` 대조 → 밖이면 403. (전제: 프록시 헤더 strip 확인)

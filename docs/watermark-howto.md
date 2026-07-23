# Colab 앱에 뷰어별 워터마크 넣는 법 (재사용 가이드)

> 화면에 **보는 사람의 사번·이름·날짜**를 대각선 반투명 타일로 깔아 유출을 억제·추적하는 방법.
> Polaris Colab(mydesk) 뒤에 있는 웹앱이면 **로그인 구현 없이** 바로 적용 가능.
> 실제 적용 예: `mno-device-sales` (main.py `/api/me` + frontend `initWatermark`).

---

## 원리 한 줄
Colab mydesk 앞단 **SSO 프록시가 로그인 신원을 HTTP 헤더로 주입**해 줌 → 서버가 그 헤더를 읽어 프론트에 내려주고 → 프론트가 화면 전체에 SVG 타일로 깐다.

```
[사용자] → [Colab SSO 프록시] --(x-auth-user, x-sm-name 헤더 주입)--> [내 앱]
                                                                        ├ /api/me : 헤더 읽어 신원 반환
                                                                        └ initWatermark : 전 화면 오버레이
```

---

## 0단계 — (보통 생략) 헤더 스펙
**Colab mydesk는 플랫폼 표준으로 아래 SSO 헤더를 모든 user-app에 주입**한다. 따라서 대부분 **바로 1·2단계로** 가면 된다.

| 헤더 | 값 | 용도 |
|---|---|---|
| `x-auth-user` | 사번 (예: 1112917) | 워터마크 핵심 |
| `x-sm-name` | 이름 | 표시용 |
| `x-sm-email` | 이메일 | 선택 |
| `x-sm-dept` / `x-sm-deptcode` | 부서 | 선택 |
| `x-sm-company` / `x-sm-upper` | 회사/상위조직 | 선택 |

**선택(권장) — 빠른 점검**: 워터마크가 신원 없으면 **조용히 안 뜨므로**, 배포 후 안 보이면 헤더부터 확인.
mydesk가 아닌 다른 환경이거나 헤더 이름이 다를 때만 필요.
```python
@app.get("/api/whoami")     # ⚠️ 임시 진단 — 확인 후 제거
def whoami(request: Request):
    return {"headers": sorted(request.headers.keys())}
```
`https://<앱>.colab-mydesk.sktelecom.com/api/whoami` 접속(SSO 로그인 상태) → `x-auth-user`가 목록에 있으면 OK.
`{"detail":"Not Found"}`면 재배포 필요, 헤더가 없으면 플랫폼 담당에게 "user-app SSO 헤더 스펙" 문의.

---

## 1단계 — 백엔드: 신원 반환 API (`/api/me`)
요청자 **본인** 헤더만 읽어 반환. 값이 URL-encoded일 수 있으니 `unquote`.

```python
from urllib.parse import unquote
from fastapi import Request

def _hdr(request: Request, *names: str) -> str:
    for n in names:
        v = request.headers.get(n)
        if v:
            return unquote(v).strip()
    return ""

@app.get("/api/me")
def me(request: Request):
    return {
        "sabun": _hdr(request, "x-auth-user", "x-sm-empno", "x-sm-uid"),
        "name":  _hdr(request, "x-sm-name"),
        "email": _hdr(request, "x-sm-email"),   # 필요 시
        "dept":  _hdr(request, "x-sm-dept"),    # 필요 시
    }
```

---

## 2단계 — 프론트: SVG 타일 오버레이 (`initWatermark`)
페이지 로드 시 `/api/me`를 받아 라벨을 만들고, 회전 텍스트가 든 SVG를 `background-image`로 전 화면에 반복.

```js
function initWatermark(){
  fetch('/api/me').then(r=>r.ok?r.json():null).then(me=>{
    if(!me) return;
    const parts=[me.sabun, me.name].filter(Boolean);
    if(!parts.length) return;                 // 신원 없으면(로컬/개발) 생략
    const d=new Date(), p=n=>String(n).padStart(2,'0');
    const label=parts.join(' ')+'   '+d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());
    const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const tw=360, th=210;
    const svg="<svg xmlns='http://www.w3.org/2000/svg' width='"+tw+"' height='"+th+"'>"
      +"<text x='12' y='"+(th*0.6)+"' transform='rotate(-24 "+(tw/2)+" "+(th/2)+")' "
      +"font-family='sans-serif' font-size='15' fill='rgba(130,130,150,0.20)'>"+esc(label)+"</text></svg>";
    let wm=document.getElementById('watermark');
    if(!wm){ wm=document.createElement('div'); wm.id='watermark'; document.body.appendChild(wm); }
    wm.style.cssText="position:fixed;inset:0;z-index:9998;pointer-events:none;"
      +"background-repeat:repeat;background-image:url(\"data:image/svg+xml,"+encodeURIComponent(svg)+"\")";
  }).catch(()=>{});
}
initWatermark();   // 앱 초기화 시 호출
```

---

## 핵심 4가지 (이것만 지키면 됨)
1. **신원 = 프록시 헤더**(`x-auth-user`) — 절대 자체 로그인 만들지 말 것 (중복·크리덴셜 리스크)
2. **`position:fixed; inset:0`** — 스크롤·탭 무관 항상 전 화면 (캡처에 항상 포함)
3. **`pointer-events:none`** — 워터마크가 클릭·조작을 방해하지 않게 (필수)
4. **`rgba(...,0.20)` 반투명 + `background-repeat:repeat`** — 은은한 대각선 타일 (0.15~0.22 사이 취향껏)

---

## 반드시 함께 안내할 주의점
- **완전 방지(방탐)가 아님** — 클라이언트 오버레이라 개발자도구로 제거 가능 →
  목적은 **"유출 억제·추적"**(정상 사용자의 캡처·촬영물에 사번이 남아 유출 시 추적).
  서버가 이미지 자체에 굽는 방식이 아니므로, 이 한계를 먼저 공유할 것.
- **헤더 위조 여부** — 프록시가 인바운드 클라이언트가 보낸 가짜 `x-auth-user`를 **strip 후 자기 값으로 덮어쓰는지** 확인 권장(보통 그렇게 함). 안 그러면 사번 위조 여지.
- **개인정보** — `/api/me`는 요청자 본인 신원만 반환. 타인 신원 조회/집계 금지. 로그에 사번·이름 과도하게 남기지 말 것.

---

## 진하기/표시 커스터마이즈
- 진하기: `fill='rgba(130,130,150,0.20)'`의 마지막 값(0.20). ↑진하게 / ↓은은하게.
- 표시 내용: `label`에 부서·시각 추가 가능 (예: `${me.dept} ${시각}`). 단, 너무 길면 타일이 커짐(`tw`/`th` 조정).
- 다크/라이트 무관하게 보이려면 중간톤 회색(`130,130,150`) 유지 권장.

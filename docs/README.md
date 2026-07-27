# 📚 mno-device-sales 문서 인덱스

MNO 단말 판매 대시보드 관련 지식 문서 모음. 팀 공유·온보딩용.

## 가이드
| 문서 | 내용 |
|---|---|
| **[디자인 가이드](design-guide.md)** | MNO SYNAPSE 디자인 시스템 — 컬러 토큰(라이트/다크)·타이포·컴포넌트·레이아웃·DO/DON'T. **UI 만들 때 먼저 읽기.** |
| **[데이터 적재 + Gateway 로딩 가이드](data-pipeline-and-gateway-guide.md)** | Iceberg 마트 배치(백필/증분/OPTIMIZE)와 Polaris Gateway 메모리캐시 로딩. 실제 겪은 에러·함정·최적화 + 처음부터 끝까지 복붙 워크스루. |
| **[워터마크 적용법](watermark-howto.md)** | 뷰어 사번 기반 화면 워터마크(유출 억제). SSO 헤더 → /api/me → SVG 타일. |
| **[Q&A 에이전트 프롬프트](qa-agent-prompt.md)** | 대시보드 Q&A 어시스턴트 시스템 프롬프트(Polaris Studio 위젯용). 마트 스키마·본부/단말군·SQL 템플릿. |

## 설계 스펙
- [단말별 분석 탭 설계](superpowers/specs/2026-07-22-device-analysis-tab-design.md)

## 앱 개요
- 스택·배포·데이터·탭 구성 등 전반은 리포지토리 루트의 **`CLAUDE.md`** 참고.

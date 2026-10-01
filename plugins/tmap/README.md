# TMap Skill for Claude Code

SK 티맵(TMap) API를 Claude Code에서 사용할 수 있게 해주는 스킬입니다. 한국 지역의 경로 탐색, 장소 검색, 지오코딩, 대중교통, 실시간 교통정보 등 티맵이 제공하는 거의 모든 기능(~38개 엔드포인트)을 얇은 CLI 래퍼로 노출합니다.

## 왜 이 스킬이 필요한가

Claude는 웹 검색만으로는 한국 지역의 정확한 경로, 소요시간, 주소, 주변 장소 정보를 안정적으로 제공하지 못합니다. 이 스킬은 티맵 API를 호출하는 Python 스크립트 모음과 SKILL.md 디스패처로 구성되어, Claude가 경로·위치 관련 질문에 정확한 답을 할 수 있게 해줍니다.

## 설계 원칙

모든 스크립트는 티맵 REST API의 **얇고 충실한 래퍼**입니다.

- 서브커맨드 = 티맵 엔드포인트 1:1 매핑
- 티맵 API의 모든 공식 파라미터를 CLI 플래그로 노출
- `--json` 옵션으로 raw 요청 바디 통과 가능 (파라미터 변경 대응)
- 기본 출력은 API의 전체 응답(compact JSON). 요약은 `--summarize` 옵션
- 복합 워크플로우(약속 시간 역산, 여러 출발지 비교 등)는 SKILL.md의 조합 패턴으로 Claude가 orchestrate
- Skill 밖에서도 일반 CLI 도구로 사용 가능

## 기능 범위

| 카테고리 | 스크립트 | 서브커맨드 | 엔드포인트 수 |
|---|---|---|---|
| 경로안내 | `route.py` | car, predict(타임머신), pedestrian, distance | 4 |
| 지오코딩 | `geocode.py` | forward, full, reverse, convert, address, near-road, postal, reverse-label | 8 |
| POI 검색 | `poi.py` | search, detail, nearby-category, around-route, admin-area, region-code | 6 |
| 대중교통 | `transit.py` | route, summary | 2 |
| 경유지 | `waypoints.py` | multi-30/100/200, optimize-10/20/30/100 | 7 |
| 교통정보 | `traffic.py` | live | 1 |
| 유가정보 | `fuel.py` | nearby, detail | 2 |
| 경로 매트릭스 | `matrix.py` | od | 1 |
| 도로 매칭 | `mapmatch.py` | match, match-500, match-1000 | 3 |
| 정적지도 | `staticmap.py` | render | 1 |
| 지오펜싱 | `geofence.py` | spatial-search, area | 2 |

**합계**: ~38개 엔드포인트

## API 키 발급 방법

1. https://openapi.sk.com/ 접속 후 회원가입
2. 로그인 후 "My 페이지" → "내 애플리케이션" → "앱 생성"
3. 앱 생성 시 **TMap API** 상품을 선택하여 등록
4. 생성된 앱의 `AppKey`를 확인 (이 값이 API 키)

### ⚠️ 상품 구조: TMap API는 2개 상품으로 나뉩니다

SK Open API에서 TMap은 **두 개의 독립 상품**으로 판매됩니다. 같은 AppKey에 각각 등록해야 해당 기능을 사용할 수 있습니다:

| 상품명 | 포함 스크립트 |
|---|---|
| **TMap API** (기본) | `route.py`, `poi.py`, `geocode.py`, `waypoints.py`, `fuel.py`, `matrix.py`, `mapmatch.py`, `staticmap.py`, `geofence.py`, **`traffic.py`** |
| **TMap 대중교통** | `transit.py` (버스/지하철/기차 경로) |

실시간 교통정보(`traffic.py`)는 **TMap API 기본 상품에 포함**됩니다 (별도 상품 아님).

**상품 신청 방법:**
1. https://openapi.sk.com/ → "My 페이지" → 앱 → 상품 추가
2. 필요한 상품(TMap API / TMap 대중교통) 선택 후 등록
3. 승인 후 동일한 AppKey로 해당 엔드포인트 사용 가능

신청하지 않으면 `403 INVALID_API_KEY` 오류가 발생합니다. 이건 스크립트 버그가 아니라 권한 문제입니다.

### 온보딩 자동 감지

이 스킬은 **첫 질의 시 자동으로 상품 활성화 상태를 감지**해 `tmap.local.md` 에 저장합니다. 비활성화된 상품의 reference는 아예 로드되지 않아 컨텍스트가 효율적입니다. 나중에 상품을 추가 신청했다면 Claude에게 "**방금 대중교통 신청했어**" 같이 말씀하시면 자동으로 재확인합니다.

### ⚠️ Free 요금제 일부 엔드포인트의 일일 한도

SK Open API Free 요금제에서 다음 엔드포인트는 **일일 1회**로 매우 제한적입니다:

| 엔드포인트 | Free 일일 한도 |
|---|---|
| `waypoints.py optimize-30` | 1회 |
| `waypoints.py optimize-100` | 1회 |

많이 사용하려면 유료 요금제 업그레이드 또는 `optimize-10`/`optimize-20` 사용 권장.

### 지오비전 퍼즐은 비포함

지오비전 퍼즐(GeoVision Puzzle)은 **완전히 다른 상품군**이며 이 플러그인에는 포함되지 않았습니다. 장소 혼잡도, 유동인구, 주거생활 분석 등 공간 빅데이터를 원하시면 https://puzzle.geovision.co.kr/ 참고하세요. 별도 스킬로 구현하는 게 적절합니다.

**요금**: 후불제. 사용량에 따라 월별 청구. 상세는 https://openapi.sk.com/products/calc?svcSeq=4 참조.

## 설치 방법

### 방법 1: Claude Code 플러그인 마켓플레이스 (권장)

Claude Code 내에서:

```
/plugin marketplace add samton-inc/samton-plugins
/plugin install tmap@samton-plugins
```

또는 `~/.claude/settings.json`의 `enabledPlugins`에 추가:

```json
"enabledPlugins": {
  "tmap@samton-plugins": true
}
```

### 방법 2: skills.sh로 스킬만 설치 (Codex, Cursor 등)

Claude Code가 아닌 다른 에이전트 환경에서 스킬만 쓰고 싶다면:

```bash
npx skills add https://github.com/samton-inc/samton-plugins/tree/main/plugins/tmap/skills/tmap
```

이 방법은 `skills/tmap/` 디렉토리만 설치합니다 (Claude Code 플러그인 매니페스트·hooks는 제외).

## API 키 설정

두 환경 모두 **파일 기반** 또는 **환경변수**로 키를 관리할 수 있습니다.

### 방법 A: setup_key.py 사용 (권장)

플러그인 설치 후:

```bash
python3 <스킬 scripts 경로>/setup_key.py "YOUR_APP_KEY"
```

자동으로 다음 우선순위로 키 파일을 저장·로드합니다:

1. **XDG 표준 위치** (기본): `$XDG_CONFIG_HOME/tmap/tmap.local.md` — 보통 `~/.config/tmap/tmap.local.md`
2. **플러그인 내부 경로** (Claude Code 하위호환): `<플러그인 루트>/.claude/tmap.local.md`

- Claude Code 환경에서 **기존에** 플러그인 내부 경로에 키가 있으면 계속 그 위치를 유지합니다 (자동 마이그레이션 없음).
- skills.sh 환경에서는 자동으로 `~/.config/tmap/tmap.local.md`에 저장됩니다.
- 첫 설정은 항상 XDG 표준 위치를 사용합니다.

### 방법 B: 환경변수

```bash
export TMAP_APP_KEY="YOUR_APP_KEY"
```

파일이 없으면 환경변수를 fallback으로 사용합니다. 임시 테스트나 CI 환경에 적합합니다.

### 키 파일 예시

```markdown
---
tmap_app_key: 발급받은_AppKey_여기
product_base: enabled
product_transit: enabled
last_checked: 2026-04-11T00:10:00Z
---
```

`product_*`와 `last_checked` 필드는 온보딩(`onboarding.py check`)이 자동으로 채웁니다.

## 설치 확인

Claude Code 환경에서:

```
/plugin
```

목록에서 `tmap@samton-plugins`가 활성화되어 있는지 확인하세요.

## 사용 예시

Claude에게 자연어로 물어보면 됩니다:

- "강남역에서 홍대입구역까지 자동차로 얼마나 걸려?"
- "서울시청 근처 카페 찾아줘"
- "내일 오전 9시까지 광화문에 도착하려면 판교에서 언제 출발해야 해?"
- "판교에서 출발해서 강남역이랑 홍대 들러 광화문까지 최적 경로"
- "강남역에서 인천공항까지 대중교통"
- "서울역 근처 주유소 가격"

## 직접 CLI 사용

각 스크립트는 독립적으로도 사용 가능합니다. 경로는 설치 방법에 따라 다릅니다:

```bash
# Claude Code 플러그인 환경
cd ~/.claude/plugins/cache/samton-plugins/tmap/<version>/skills/tmap/scripts

# skills.sh 환경
cd ~/.agents/skills/tmap/scripts

# 예: 자동차 경로
python3 route.py car --start-x 127.0276 --start-y 37.4979 \
                     --end-x 126.9236 --end-y 37.5663 \
                     --summarize standard

# 예: 타임머신 — 내일 11시 도착하려면 언제 출발? (응답에 departureTime/arrivalTime)
python3 route.py predict --start-x 126.72429747 --start-y 37.44957437 \
                         --end-x 126.718741 --end-y 37.366246 \
                         --arrive-by "2026-10-02T11:00:00+0900" \
                         --summarize minimal

# 예: 지오코딩
python3 geocode.py full --full-addr "서울특별시 중구 세종대로 110"

# 예: POI 검색
python3 poi.py search --keyword "스타벅스" \
                      --center-lat 37.5663 --center-lon 126.9236 \
                      --radius 1
```

**좌표 순서 주의**: X=경도(longitude), Y=위도(latitude). 일반적인 "위도, 경도" 표기와 반대입니다.

## 라이선스

MIT License. 티맵 API 사용 약관(https://openapi.sk.com/)을 준수해야 합니다.

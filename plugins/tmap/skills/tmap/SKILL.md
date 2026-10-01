---
name: tmap
description: This skill should be used when the user asks about Korean location, routes, or navigation — when the user says things like "경로", "길찾기", "여기서 거기까지", "○○까지 얼마나 걸려", "근처 ○○", "○○ 주소", "○○ 좌표", "주유소 가격", "대중교통으로 가는 법", "몇 시에 출발해야", "몇 시에 도착해", "약속 시간에 맞춰", "경유지", "지도", or asks for directions, distances, POI search, geocoding, traffic, or fuel prices in Korea. Wraps SK TMap API endpoints (~38 endpoints) as thin Python CLI scripts covering routes (car/time-machine prediction/pedestrian/transit), POI search, geocoding, waypoints, traffic, fuel, matrix, map matching, static maps, and geofencing.
---

# TMap API Skill

Provide accurate Korean-region location and navigation information by invoking thin Python wrapper scripts over SK TMap API. Do not rely on web search for Korean routes, addresses, or places — use these scripts instead.

## Core principle: thin wrappers, composition at skill level

Every script in `scripts/` is a faithful 1:1 mapping to a TMap REST endpoint:
- Subcommands correspond to endpoints
- CLI flags mirror TMap API parameters
- Default output is the **full raw API response** (compact JSON)
- Summaries are **opt-in** via `--summarize [minimal|standard|full]`
- Raw JSON passthrough via `--json '{...}'` for any endpoint
- `--path` override on every subcommand if TMap endpoint path changes

Compose multi-step workflows (geocoding → routing, POI → route, arrive-by via the time machine) by chaining scripts at this skill level, never by editing scripts.

## Setup: API key

Before calling any endpoint, verify the API key is available. Keys are read in this order:
1. `~/.claude/plugins/tmap/.claude/tmap.local.md` — the double `.claude` is intentional: the outer `.claude` is the user's Claude Code directory, the inner `.claude` is this plugin's local-settings directory (`.gitignore`d) with `tmap_app_key` in YAML frontmatter
2. Environment variable `TMAP_APP_KEY`

If both are missing, the first script call raises `MissingKeyError` with instructions. On that error, follow this interactive flow:

1. Explain: "TMap API 키가 설정되어 있지 않습니다. SK오픈API에서 발급받아야 합니다."
2. Guide to https://openapi.sk.com/ — register, create an app, register the TMap product, copy the AppKey.
3. Ask the user to paste the AppKey.
4. Run `python3 scripts/setup_key.py "<pasted_key>"` to save it.
5. Retry the original request.

Never write the key to logs, conversation, or any file outside `.claude/tmap.local.md`. Never commit it.

## Onboarding: 상품 활성화 확인

SK Open API에서 TMap은 **두 개의 독립 상품**으로 나뉩니다. 같은 AppKey에 각각 별도 등록:

| product key | 한글 상품명 | 포함 스크립트 |
|---|---|---|
| `base` | **TMap API** | route, poi, geocode, waypoints, fuel, matrix, mapmatch, staticmap, geofence, **traffic** |
| `transit` | **TMap 대중교통** | transit |

두 상품은 **완전히 독립적**입니다 — 하나만 등록해도 그 부분만 사용 가능합니다.

### 첫 TMap 관련 질의 시 반드시 수행

다른 작업 전에:

1. `python3 "${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/onboarding.py" status` 실행
2. 결과 JSON을 파싱
3. `last_checked`가 비어 있거나 모든 상품이 `unknown`이면 `python3 "${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/onboarding.py" check` 자동 실행 (첨 질의 시에만, 이후 세션에선 status 결과 재사용)
4. 반환된 capabilities를 기반으로 **reference 로드 여부** 결정

### Capability → Reference 로드 매핑 (독립 판정)

| 상태 | 동작 |
|---|---|
| `base=enabled` | route, geocode, poi, waypoints, fuel, matrix, mapmatch, staticmap, geofence, **traffic** 관련 참조·스크립트 사용 가능 |
| `base=disabled` | 위 참조들 **절대 로드 금지**. base 관련 질의(경로/POI/지오코딩/교통정보 등)가 오면 **"현재 AppKey에 TMap API 기본 상품이 등록되어 있지 않습니다. https://openapi.sk.com/ → My 페이지 → 앱 → 상품 추가 → TMap API를 선택 등록해주세요. 등록 후 '신청했어'라고 알려주시면 재확인합니다."** 안내 후 중단 |
| `transit=enabled` | `transit.md` 로드 및 `transit.py` 사용 가능 |
| `transit=disabled` | `transit.md` **절대 로드 금지**. 대중교통 질의가 오면 **"현재 AppKey에 TMap 대중교통 상품이 등록되어 있지 않습니다. https://openapi.sk.com/ 에서 TMap 대중교통 상품을 추가 등록해주세요. 등록 후 '신청했어'라고 알려주시면 재확인합니다."** 안내 후 중단 |
| 둘 다 disabled | 어떤 reference도 로드 금지. 두 상품 모두 신청 필요 안내 |

**질의 분류**:
- 대중교통("대중교통", "지하철", "버스") → `transit`만 확인
- 그 외 (경로, POI, 지오코딩, 교통정보 등) → `base`만 확인
- 복합("자동차 vs 대중교통 비교") → 두 상품 모두 확인, 가능한 방법만 제시

**한글 상품명 필수**: 사용자 안내에서는 반드시 **"TMap API"**, **"TMap 대중교통"** 한글 상품명을 사용합니다. 영어 `base`/`transit`은 내부 코드에만 씁니다.

### "신청했어" 트리거

사용자가 "신청했어" / "활성화했어" / "상품 추가했어" / "등록했어" 등의 언급을 하면:

1. 즉시 `python3 "${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/onboarding.py" refresh` 실행
2. 결과를 한글 상품명으로 보고 (예: "TMap 대중교통이 활성화되었습니다")
3. 이후 갱신된 capabilities 기준으로 동작
4. 이전에 차단됐던 원래 질의가 있으면 이어서 처리

## Scripts directory

All scripts live at `${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/` and share `tmap_client.py` (shared HTTP, auth, error handling, optional summarizers).

| Script | Category | Subcommands (endpoint 1:1) |
|---|---|---|
| `route.py` | 경로안내 | `car`, `predict` (타임머신), `pedestrian`, `distance` |
| `geocode.py` | 지오코딩 | `forward`, `full`, `reverse`, `convert`, `address`, `near-road`, `postal`, `reverse-label` |
| `poi.py` | POI 검색 | `search`, `detail`, `nearby-category`, `around-route`, `admin-area`, `region-code` |
| `transit.py` | 대중교통 | `route`, `summary` |
| `waypoints.py` | 경유지 | `multi-30`, `multi-100`, `multi-200`, `optimize-10`, `optimize-20`, `optimize-30`, `optimize-100` |
| `traffic.py` | 교통정보 | `live` |
| `fuel.py` | 유가정보 | `nearby`, `detail` |
| `matrix.py` | 경로 매트릭스 | `od` |
| `mapmatch.py` | 도로 매칭 | `match`, `match-500`, `match-1000` |
| `staticmap.py` | 정적 지도 | `render` |
| `geofence.py` | 지오펜싱 | `spatial-search`, `area` |
| `setup_key.py` | 키 설정 | (no subcommands — positional key arg) |

Always invoke with absolute paths or from the scripts directory. Example:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/route.py" car --help
```

## Common query flows

Most user queries reduce to one of these composition patterns. Resolve place names to coordinates via `geocode.py` first, then call the routing/search endpoint, then summarize only the parts the user asked about.

### 1. Simple route (origin → destination)

"강남역에서 홍대입구역까지 자동차로"

```bash
# Step 1: geocode origin
python3 scripts/geocode.py full --full-addr "강남역" --pretty

# Step 2: geocode destination
python3 scripts/geocode.py full --full-addr "홍대입구역" --pretty

# Step 3: route with summary for user-facing answer
python3 scripts/route.py car \
  --start-x 127.0276 --start-y 37.4979 \
  --end-x 126.9236 --end-y 37.5663 \
  --summarize standard
```

Read `references/route.md` for parameter details and searchOption values. Read `references/geocode.md` for which geocoding subcommand fits best (use `full` for free-text, `forward` for structured).

### 2. Pedestrian route

Same shape as car, but `route.py pedestrian`. `startName` and `endName` are **required** for pedestrian. See `references/route.md`.

### 3. POI search then route

"서울시청 근처 카페" or "홍대 떡볶이집 찾고 강남역에서 거기까지 운전"

```bash
# Find places
python3 scripts/poi.py search --keyword "카페" \
  --center-lat 37.5663 --center-lon 126.9780 --radius 1 \
  --summarize standard --limit 5
```

Then pipe the chosen coordinates into `route.py`. See `references/poi.md`.

### 4. Transit (bus / subway / train)

"강남역에서 인천공항까지 대중교통"

```bash
python3 scripts/transit.py route \
  --start-x 127.0276 --start-y 37.4979 \
  --end-x 126.4407 --end-y 37.4601 \
  --summarize standard --options 3
```

See `references/transit.md`.

### 5. Arrive-by / depart-at at a future time (타임머신)

"서울대 시흥캠퍼스에 내일 오전 11시까지 도착하려면 구월동에서 언제 출발해야 해?" / "내일 8시에 출발하면 몇 시에 도착해?"

Use `route.py predict` (`POST /tmap/routes/prediction`). It predicts traffic at the given future time and returns both `departureTime` and `arrivalTime`. **Never use `route.py car` for this** — `/tmap/routes` ignores prediction fields and answers with current traffic (the script now refuses those flags).

```bash
# 도착 시각 고정 → 출발 시각 계산
python3 scripts/route.py predict \
  --start-name "구월4동행정복지센터" --start-x 126.72429747 --start-y 37.44957437 \
  --end-name "서울대 시흥캠퍼스" --end-x 126.718741 --end-y 37.366246 \
  --arrive-by "2026-10-02T11:00:00+0900" \
  --summarize minimal
# → {"totalDistance_m":14546,"totalTime_s":1719,...,
#    "departureTime":"2026-10-02T10:31:21+0900","arrivalTime":"2026-10-02T11:00:00+0900"}

# 출발 시각 고정 → 도착 시각 계산
python3 scripts/route.py predict ... --depart-at "2026-10-02T08:00:00+0900" --summarize minimal
```

- **Use `--arrive-by` / `--depart-at`.** The raw API field is inverted relative to its name: `predictionType=departure` means "predictionTime is the *arrival* time — compute departureTime"; `predictionType=arrival` means "predictionTime is the *departure* time — compute arrivalTime" (official docs + live-verified 2026-10-01). `--arrive-by` sends `departure`, `--depart-at` sends `arrival`. If you ever use the raw `--prediction-type`, apply that inversion.
- **Time format**: the API accepts only `YYYY-MM-DDTHH:MM:SS+0900` (no `+09:00`, no missing seconds/offset → 400). The script normalizes `2026-10-02T11:00:00+0900`, `2026-10-02 11:00`, `202610021100`, `...+09:00`, `...Z` (no offset = KST). Derive the date from today (`date +%Y-%m-%d`) plus the user's phrase ("내일 오전 11시"); never hardcode it.
- Read `departureTime` / `arrivalTime` straight from the response; don't recompute from `totalTime_s`. Traffic is predicted for that time slot, so one call is enough — no convergence loop.
- Default `--total-value 2` returns the summary only (no turn-by-turn). Add `--total-value 1` when the user also wants directions.
- Reply with "○시 ○분에 출발하세요" plus the expected travel time. Suggest a 10–15 min buffer and say explicitly that it is a buffer you added.

### 6. Arrive-by with waypoints

"11시까지 시흥캠퍼스 도착, 구월동 출발, 중간에 두 군데 들러야"

**Up to 5 stops in a known order — one call.** Repeat `--via 경도,위도` in visiting order (or `--pass-list "X1,Y1_X2,Y2"`):

```bash
python3 scripts/route.py predict \
  --start-name "<출발지>" --start-x <경도> --start-y <위도> \
  --end-name "<도착지>" --end-x <경도> --end-y <위도> \
  --via <경유1 경도>,<경유1 위도> --via <경유2 경도>,<경유2 위도> \
  --arrive-by "2026-10-02T11:00:00+0900" \
  --summarize minimal
```

This assumes no time spent at the stops. Six or more `--via` points are rejected by the API (400), and the script stops before calling it.

**Visiting order not decided** — get the order first from `waypoints.py optimize-10` (the summary's `waypointOrder`), then pass the stops to `route.py predict --via ...` in that order for the time-accurate departure.

**More than 5 stops, or the user stays at a stop (dwell time)** — chain legs backward:

1. Split the trip into legs at the stops where time is spent (each leg may still carry up to 5 `--via` points).
2. Last leg: `predict --arrive-by <deadline>` → its `departureTime` is when to leave the last stop.
3. Subtract the dwell time at that stop → that is the `--arrive-by` for the previous leg.
4. Repeat back to the origin. The first leg's `departureTime` is the answer.

Each call is exact for its own time slot, so no iteration is needed. Tell the user briefly "구간별로 역산 중..." when making several calls. Keep this chaining at the skill level — do not bake it into the script.

### 7. Compare multiple starting points

"집, 회사, 역 중에서 3시까지 코엑스에 도착하려면 어디서 출발해야 빨리 도착?"

Use `matrix.py od`:

```bash
python3 scripts/matrix.py od \
  --origins-json '[{"lat":37.X,"lon":127.X},...]' \
  --destinations-json '[{"lat":37.5115,"lon":127.0595}]' \
  --summarize standard
```

The matrix reflects current traffic. For a future deadline, follow up with `route.py predict --arrive-by` for the best one or two origins.

### 8. Address ↔ coordinate

```bash
# Address → coordinate
python3 scripts/geocode.py full --full-addr "서울특별시 중구 세종대로 110"

# Coordinate → address
python3 scripts/geocode.py reverse --lat 37.5665 --lon 126.9780 --pretty
```

### 9. Fuel prices

"서울역 근처 주유소 가격"

```bash
python3 scripts/fuel.py nearby \
  --center-lat 37.5547 --center-lon 126.9707 --radius 2 \
  --sort price --count 10 --pretty
```

### 10. Live traffic

```bash
python3 scripts/traffic.py live --center-lat 37.5 --center-lon 127.03 --radius 3
```

## Response handling

- **For user-facing answers**, add `--summarize standard` to get distance/time/fare/turn-by-turn. This saves context dramatically.
- **For debugging or one-off inspection**, use `--pretty` (with or without `--summarize`) to get indented JSON on stdout.
- **For programmatic analysis** or when the user asks for precise details, omit `--summarize` and parse the full response.
- `--summarize` and `--pretty` are orthogonal: use `--summarize --pretty` for a readable summary, or `--pretty` alone for the full response pretty-printed.
- **Large responses**: use `--output-full /tmp/tmap_response.json` to write the raw response to a file, and summarize stdout. Load the file only if needed for follow-up.
- **Binary responses** (Static Map): must use `--output <path>`. The stdout is a small JSON with `savedTo`.

## Error handling

Scripts exit with distinct codes:
- `0` success
- `1` generic error
- `2` CLI argument parsing error
- `3` `MissingKeyError` — trigger the setup flow
- `4` `TmapAPIError` — inspect the JSON on stderr; common causes: invalid coordinates, out-of-range radius, appKey quota exceeded, wrong coordinate system

When a TMap API error occurs, read the error JSON on stderr and explain the cause in Korean. Typical fixes:
- `Invalid appKey` → rerun setup_key.py
- Parameter errors → check the relevant `references/*.md` for the correct parameter name/format
- Empty results → broaden radius or change keyword
- **Undocumented / new parameters** → every subcommand accepts `--json '{"key":"value"}'` to merge raw fields into the request. Use this escape hatch when TMap adds fields not yet exposed as CLI flags

## Coordinate system

TMap defaults to WGS84GEO (standard lat/lon). All scripts default to `WGS84GEO` for both request and response. If the user provides KATEC or EPSG3857 coordinates, use `geocode.py convert` to transform first, or set `--req-coord-type` accordingly.

## References

Detailed per-category documentation is in `references/`. **Each reference is tagged with the product that must be enabled to use it.** Do not load a reference if its product is not `enabled` — use the onboarding flow to verify first.

TMap API 기본 (`base`) 상품에 속하는 참조 (base=enabled일 때만 로드 가능):
- **`references/route.md`** — car/pedestrian routes, searchOption values, time machine (`predict`: arrive-by/depart-at)
- **`references/geocode.md`** — all 8 geocoding endpoints and when to use each
- **`references/poi.md`** — POI search, category codes, around-route usage
- **`references/waypoints.md`** — multi-waypoint and optimization endpoints, viaPoints schema
- **`references/traffic.md`** — live traffic parameters (base 상품에 포함, 별도 상품 아님)
- **`references/fuel.md`** — fuel station search, filter options
- **`references/matrix.md`** — matrix endpoint request/response shape
- **`references/mapmatch.md`** — road matching coordinate format
- **`references/staticmap.md`** — static map parameters, markers
- **`references/geofence.md`** — geofencing area schema

TMap 대중교통 (`transit`) 상품에 속하는 참조 (transit=enabled일 때만 로드 가능):
- **`references/transit.md`** — public transit route schema and itinerary structure

Load only the reference relevant to the current query **and** whose product is enabled — do not preload all of them.

## Path conventions

Scripts are at `${CLAUDE_PLUGIN_ROOT}/skills/tmap/scripts/`. When running from Claude Code, always use the absolute path or resolve via `${CLAUDE_PLUGIN_ROOT}`. Never hardcode `/Users/...` paths.

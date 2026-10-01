---
name: tmap
description: This skill should be used when the user asks about Korean location, routes, or navigation — when the user says things like "경로", "길찾기", "여기서 거기까지", "○○까지 얼마나 걸려", "근처 ○○", "○○ 주소", "○○ 좌표", "주유소 가격", "대중교통으로 가는 법", "몇 시에 출발해야", "약속 시간에 맞춰", "경유지", "지도", or asks for directions, distances, POI search, geocoding, traffic, or fuel prices in Korea. Wraps SK TMap API endpoints (~37 endpoints) as thin Python CLI scripts covering routes (car/pedestrian/transit), POI search, geocoding, waypoints, traffic, fuel, matrix, map matching, static maps, and geofencing.
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
- `--path` override if a TMap endpoint path changes — available on every endpoint subcommand **except `route.py`**, whose paths are fixed in code (use `--json` there for new fields; a moved route endpoint needs a script change)

Compose multi-step workflows (geocoding → routing, POI → route, arrive-by with convergence) by chaining scripts at this skill level, never by editing scripts.

## Setup: API key

Before calling any endpoint, verify the API key is available. The key lives in the `tmap_app_key` field of a YAML-frontmatter file (the same file also stores onboarding status: `product_*`, `last_checked`). Lookup order (`tmap_client.py`):
1. `$XDG_CONFIG_HOME/tmap/tmap.local.md` — default `~/.config/tmap/tmap.local.md` (standard location)
2. `${CLAUDE_PLUGIN_ROOT}/.claude/tmap.local.md` — legacy in-plugin location, kept for backward compatibility (`.gitignore`d)
3. Environment variable `TMAP_APP_KEY`

Only the **first of the two files that exists** is read. If that file has no `tmap_app_key`, the lookup falls through to `TMAP_APP_KEY` — the other file is not consulted.

`setup_key.py` writes the key into whichever of those two files already exists (same precedence); if neither exists, it creates `$XDG_CONFIG_HOME/tmap/tmap.local.md`. It never migrates a legacy file.

If no key is found, the first script call raises `MissingKeyError` with instructions. On that error, follow this interactive flow:

1. Explain: "TMap API 키가 설정되어 있지 않습니다. SK오픈API에서 발급받아야 합니다."
2. Guide to https://openapi.sk.com/ — register, create an app, register the TMap product, copy the AppKey.
3. Ask the user to paste the AppKey.
4. Run `python3 scripts/setup_key.py "<pasted_key>"` to save it.
5. Retry the original request.

Never write the key to logs, the conversation, or any file other than the key file `setup_key.py` chose (path above). Never commit it.

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
| `route.py` | 경로안내 | `car`, `pedestrian`, `distance` |
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

### 5. Arrive-by queries (time reversal)

"광화문에 내일 오전 9시까지 도착하려면 판교에서 언제 출발해야 해?"

TMap's timemachine supports **native arrival-time prediction**. Use `--prediction-type arrival --prediction-time` on `route.py car`:

```bash
python3 scripts/route.py car \
  --start-x <판교 경도> --start-y <판교 위도> \
  --end-x <광화문 경도> --end-y <광화문 위도> \
  --prediction-type arrival \
  --prediction-time "<YYYYMMDDHHMM>" \
  --summarize standard

# 예: 2026-04-11 오전 9시 도착이면 --prediction-time "202604110900"
```

Compute the target `prediction-time` from the current date (use `date +%Y%m%d` or parse the user's phrase like "내일 오전 9시"). Never hardcode a date.

The response includes the departure time. Parse it and reply with "○시 ○분에 출발하세요" plus the expected travel time. Add a reasonable buffer (10-15 min) when presenting to the user, and mention it explicitly.

### 6. Arrive-by with waypoints

"9시까지 광화문 도착, 판교 출발, 강남역과 홍대 들러야"

**First attempt — pass predictionType via `--json`**:
```bash
python3 scripts/waypoints.py optimize-10 \
  --start-x <판교 경도> --start-y <판교 위도> \
  --end-x <광화문 경도> --end-y <광화문 위도> \
  --stops-json '[
    {"viaPointId":"1","viaPointName":"강남역","viaX":"127.0276","viaY":"37.4979"},
    {"viaPointId":"2","viaPointName":"홍대입구역","viaX":"126.9236","viaY":"37.5563"}
  ]' \
  --json '{"predictionType":"arrival","predictionTime":"<YYYYMMDDHHMM>"}' \
  --summarize standard
```

If the endpoint rejects that JSON (response error mentions unknown field), **fall back to iterative convergence**:

```bash
# Step 1: initial estimate with a guess departure time
python3 scripts/waypoints.py optimize-10 \
  --start-x ... --start-y ... --end-x ... --end-y ... \
  --stops-json '[...]' \
  --start-time "<초기 추정 YYYYMMDDHHmm>" \
  --summarize minimal

# Parse totalTime_s from the response, then:
# departAt = arriveBy - totalTime_s (subtract seconds)
# Re-run with the new --start-time
# Repeat up to 3 iterations until totalTime_s converges (delta < 60s)
```

Keep this convergence logic at the skill level — do not bake it into the script. When running this, briefly tell the user "경유지 반복 계산 중..." so they know why there are multiple API calls.

### 7. Compare multiple starting points

"집, 회사, 역 중에서 3시까지 코엑스에 도착하려면 어디서 출발해야 빨리 도착?"

Use `matrix.py od`:

```bash
python3 scripts/matrix.py od \
  --origins-json '[{"lat":37.X,"lon":127.X},...]' \
  --destinations-json '[{"lat":37.5115,"lon":127.0595}]' \
  --summarize standard
```

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
- **`references/route.md`** — car/pedestrian routes, searchOption values, timemachine parameters
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

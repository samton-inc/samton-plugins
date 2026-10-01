# 경로안내 (route.py)

**Product: `base`** (TMap API 기본 상품)
**Status: Verified 2026-04-11** — car, pedestrian, distance 실제 API 호출 성공
**Status: Verified 2026-10-01** — predict(타임머신, `/tmap/routes/prediction`) 실제 API 호출 성공. 이전 문서의 "car + predictionType" 방식은 `/tmap/routes`가 해당 필드를 무시해 현재 교통 기준 결과만 돌려주던 오류였음

티맵 자동차/타임머신/보행자/직선거리 경로 API. 공식 문서: https://skopenapi.readme.io/reference (TMAP 섹션의 경로안내).

## 서브커맨드와 엔드포인트

| 서브커맨드 | 메서드 | 경로 |
|---|---|---|
| `car` | POST | `/tmap/routes?version=1` |
| `predict` | POST | `/tmap/routes/prediction?version=1&reqCoordType=WGS84GEO&resCoordType=WGS84GEO&sort=index&totalValue=2` |
| `pedestrian` | POST | `/tmap/routes/pedestrian?version=1` |
| `distance` | GET | `/tmap/routes/distance?version=1` |

## 자동차 경로 (`car`) 주요 파라미터

필수:
- `--start-x` / `--startX` — 출발 경도 (WGS84GEO 기준)
- `--start-y` / `--startY` — 출발 위도
- `--end-x` / `--endX` — 도착 경도
- `--end-y` / `--endY` — 도착 위도

선택 (자주 쓰는 것):
- `--start-name` / `--end-name` — 출발/도착지 이름 (URL 인코딩 자동)
- `--search-option` / `--searchOption` — 경로 탐색 옵션 (공식 문서 기준):
  - `0` 교통최적+추천 (기본)
  - `1` 교통최적+무료우선
  - `2` 교통최적+최소시간
  - `3` 교통최적+초보
  - `4` 교통최적+고속도로우선
  - `10` 최단거리+유/무료
  - `12` 이륜차도로우선
  - `19` 교통최적+어린이보호구역 회피
- `--traffic-info Y` / `--trafficInfo` — 실시간 교통정보 반영
- `--car-type` / `--carType` — 차종 1(승용)~6(대형)
- `--pass-list "127.0,37.5_127.1,37.6"` — 경유지 (2~3개 간단한 경유지일 때)
- `--angle` — 출발 각도 0~360 (이전 진행 방향 정보)

`car`는 미래 시각 예측을 하지 않습니다. `/tmap/routes`는 `predictionType`/`predictionTime`을 받아도 무시하고 현재 교통 기준 `totalTime`을 돌려주며 응답에 출발/도착 시각도 없습니다. 그래서 스크립트는 `car`에서 이 플래그(또는 `--json`으로 넣은 같은 필드)를 받으면 `predict`로 안내하고 exit 2로 끝냅니다.

좌표계:
- `--req-coord-type` / `--res-coord-type` — 기본 `WGS84GEO`. 다른 값: `KATEC`, `EPSG3857`, `WGS84GEORAD` 등

## 타임머신 (`predict`)

미래 시각의 예측 교통으로 경로를 계산하고 출발·도착 시각을 함께 돌려줍니다. 도착 시각을 정해 두고 출발 시각을 구하는 "약속 시간 역산"은 이 서브커맨드를 씁니다.

시각 지정 (셋 중 하나, 필수):
- `--arrive-by TIME` — 이 시각에 **도착** → `departureTime` 계산 (API `predictionType=departure`)
- `--depart-at TIME` — 이 시각에 **출발** → `arrivalTime` 계산 (API `predictionType=arrival`)
- `--prediction-type {departure,arrival}` + `--prediction-time TIME` — API 값을 그대로 지정

⚠️ **API의 `predictionType` 이름은 의미와 반대입니다** (공식 문서 정의, 2026-10-01 실측 일치):

| predictionType | predictionTime의 의미 | 응답에서 계산되는 값 |
|---|---|---|
| `departure` | **도착** 시각 | `departureTime` |
| `arrival` | **출발** 시각 | `arrivalTime` |

헷갈리지 않도록 `--arrive-by` / `--depart-at`을 쓰세요. 도착 시각 역산에 `--prediction-type arrival`을 쓰면 그 시각에 **출발**하는 경로가 나옵니다.

TIME 형식:
- API는 `YYYY-MM-DDTHH:MM:SS+0900` **하나만** 받습니다. `+09:00`(콜론 오프셋), 초 생략, 오프셋 생략, `YYYYMMDDHHmm`은 모두 400 (code 1100)
- 스크립트가 `2026-10-02T11:00:00+0900`, `2026-10-02 11:00`, `2026-10-02T11:00+09:00`, `2026-10-02T02:00:00Z`, `202610021100`을 받아 위 형식(KST)으로 바꿔 보냅니다. 오프셋이 없으면 KST로 봅니다
- 과거 시각도 받습니다

출발/도착:
- `--start-x`, `--start-y`, `--end-x`, `--end-y` — 필수
- `--start-name`, `--end-name` — 기본값 `출발지`/`도착지`. 빈 문자열이면 400 (code 9401 "필수 파라메터가 없습니다"). URL 인코딩하지 않고 그대로 보냄

경유지 (입력 순서대로 방문, **최대 5개**, 6개부터 API가 400):
- `--via 경도,위도` — 반복 지정
- `--pass-list "X1,Y1_X2,Y2"` — `car`와 같은 형식. `--via` 뒤에 이어 붙음
- 경유지 체류 시간은 반영되지 않음. 체류가 있거나 5개를 넘으면 SKILL.md 6번 흐름처럼 구간을 나눠 뒤에서부터 `--arrive-by`로 이어 계산

옵션 (`routesInfo` 안으로 들어감):
- `--search-option` — **두 자리** 코드. 기본 `00`. 한 자리를 주면 앞에 0을 채움 (`"0"` 그대로는 400)
  - `00` 교통최적+추천, `01` 교통최적+무료우선, `02` 교통최적+최소시간, `03` 교통최적+초보, `04` 교통최적+고속도로우선, `10` 최단거리+유/무료, `19` 교통최적+어린이보호구역 회피
  - `12`(이륜차도로우선)는 타임머신에서 400
- `--traffic-info Y|N` — 기본 `N`
- `--tollgate-car-type` — 기본 `car`. 그 외 `mediumvan`, `largevan`, `largetruck`, `specialtruck`, `smallcar`(경차), `twowheel`
- `--json '{...}'` — `routesInfo`에 병합 (예: `{"searchOption":"10"}`)

쿼리 파라미터:
- `--total-value` — 기본 `2` = 요약만 (feature 1개, geometry 없음). `1` = 전체 경로 (턴바이턴 Point/LineString 포함)
- `--sort` — 기본 `index`. `custom`이면 LineString 먼저, Point 나중
- `--req-coord-type`, `--res-coord-type` — 기본 `WGS84GEO`

요청 바디 (스크립트가 만드는 형태):
```json
{
  "routesInfo": {
    "departure": {"name": "구월4동행정복지센터", "lon": "126.72429747", "lat": "37.44957437"},
    "destination": {"name": "서울대 시흥캠퍼스", "lon": "126.718741", "lat": "37.366246"},
    "predictionType": "departure",
    "predictionTime": "2026-10-02T11:00:00+0900",
    "searchOption": "00",
    "tollgateCarType": "car",
    "trafficInfo": "N",
    "wayPoints": {"wayPoint": [{"lon": "126.7052", "lat": "37.4563"}]}
  }
}
```

응답 (`totalValue=2`):
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "totalDistance": 14546, "totalTime": 1719, "totalFare": 0, "taxiFare": 17230,
        "departureTime": "2026-10-02T10:31:21+0900",
        "arrivalTime": "2026-10-02T11:00:00+0900"
      }
    }
  ]
}
```

`totalValue=1`이면 `features[0]`(출발 Point)의 properties에 같은 값이 들어 있고, 그 뒤로 `car`와 같은 턴바이턴 Point/LineString이 이어집니다. `--summarize`는 어느 쪽이든 `departureTime`/`arrivalTime`을 포함합니다.

## 보행자 경로 (`pedestrian`) 주의사항

- `startName` 과 `endName` 이 **필수**입니다 (자동차와 다름)
- `searchOption` 값이 자동차와 다름:
  - `0` 추천
  - `4` 대로우선
  - `10` 최단
  - `30` 계단 제외
- `passList` 는 간단한 경유지만 (자세한 건 waypoints.py 사용)

## 직선거리 (`distance`)

두 좌표 사이의 직선 거리만 미터 단위로 반환. GET 방식이라 쿼리 파라미터:
- `--start-x`, `--start-y`, `--end-x`, `--end-y`
- `--req-coord-type`, `--res-coord-type`

실제 경로 거리가 아니라 평면 직선 거리임에 주의.

## 응답 구조 (car/pedestrian)

GeoJSON FeatureCollection 형식:
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {"type": "Point", "coordinates": [127.02, 37.49]},
      "properties": {
        "totalDistance": 12345,
        "totalTime": 1800,
        "totalFare": 0,
        "taxiFare": 15000,
        "pointIndex": "0",
        "description": "출발지"
      }
    },
    {
      "type": "Feature",
      "geometry": {"type": "LineString", "coordinates": [[...], [...]]},
      "properties": {...}
    },
    // ... 많은 Point (턴바이턴) + LineString (구간)
  ]
}
```

- `totalDistance` — 미터
- `totalTime` — 초
- `totalFare` — 통행료 원
- `taxiFare` — 예상 택시 요금 원 (자동차만)
- Point feature의 `description` — 턴바이턴 안내 문구

응답이 수백 KB ~ MB 단위로 클 수 있습니다. 사용자 응답용이면 반드시 `--summarize` 사용.

## 요약 레벨

- `--summarize minimal` — totalDistance, totalTime, totalFare, taxiFare만 (`predict`는 departureTime, arrivalTime 추가)
- `--summarize standard` — + 시작/끝 좌표 + 턴바이턴 10개 (기본)
- `--summarize full` — + 전체 턴바이턴
- `--turns N` — 턴바이턴 개수 수동 지정

## 예시

```bash
# 기본 자동차 경로 (요약 포함)
python3 route.py car \
  --start-x 127.0276 --start-y 37.4979 \
  --end-x 126.9236 --end-y 37.5663 \
  --summarize standard

# 실시간 교통 반영
python3 route.py car \
  --start-x 127.0276 --start-y 37.4979 \
  --end-x 126.9236 --end-y 37.5663 \
  --search-option 10 --traffic-info Y \
  --summarize standard

# 타임머신: 2026-10-02 11:00 도착 → 출발 시각 역산
python3 route.py predict \
  --start-name "구월4동행정복지센터" --start-x 126.72429747 --start-y 37.44957437 \
  --end-name "서울대 시흥캠퍼스" --end-x 126.718741 --end-y 37.366246 \
  --arrive-by "2026-10-02T11:00:00+0900" \
  --summarize minimal
# → "departureTime":"2026-10-02T10:31:21+0900", "arrivalTime":"2026-10-02T11:00:00+0900"

# 타임머신: 10:30 출발 → 도착 시각, 경유지 1곳, 턴바이턴 포함
python3 route.py predict \
  --start-x 126.72429747 --start-y 37.44957437 \
  --end-x 126.718741 --end-y 37.366246 \
  --depart-at "2026-10-02 10:30" \
  --via 126.7052,37.4563 \
  --total-value 1 --summarize standard

# 보행자
python3 route.py pedestrian \
  --start-name "서울시청" --end-name "덕수궁" \
  --start-x 126.9779 --start-y 37.5666 \
  --end-x 126.9751 --end-y 37.5659 \
  --search-option 0 \
  --summarize standard

# 파라미터가 부족하거나 새 파라미터가 필요하면 --json 사용
python3 route.py car \
  --start-x 127.0 --start-y 37.5 --end-x 127.1 --end-y 37.6 \
  --json '{"carType":"3","tollgateCarType":"1","reservedField":"custom"}' \
  --summarize standard
```

## 주의사항

- 좌표 순서는 **경도(X) 먼저, 위도(Y) 나중**. 일반적인 "위도, 경도" 순서와 반대
- 한국 내 좌표만 지원
- appKey 할당량 초과 시 `TmapAPIError 429` 발생
- 출발지와 도착지가 같거나 너무 가까우면 오류

# 경유지 (waypoints.py)

**Product: `base`** (TMap API 기본 상품)
**Status: Verified 2026-04-11**
- `multi-30`, `multi-100`, `multi-200`: 실제 호출 성공
- `optimize-10`, `optimize-20`: 실제 호출 성공
- `optimize-30`, `optimize-100`: 구조 검증 완료 (동일 스키마, **일일 quota 1회** 소진으로 당일 재확인 불가)

다중 경유지 경로 및 경유지 방문 순서 최적화.

## ⚠️ Free 요금제 일일 한도 (중요)

SK Open API **Free 요금제**에서 일부 경유지 엔드포인트는 **일일 호출 한도가 극히 낮습니다**:

| 엔드포인트 | Free 요금제 일일 한도 |
|---|---|
| 경유지 최적화 30 (`optimize-30`) | **1회/일** |
| 경유지 최적화 100 (`optimize-100`) | **1회/일** |

테스트 중 한 번 호출하면 당일 재호출 불가. 실무에서 사용할 때는 반드시 고려해야 하며, 많이 쓰려면 유료 요금제 업그레이드 또는 더 작은 variant(`optimize-10`, `optimize-20`)로 충분한지 확인하세요.

quota 초과 시 응답:
```
429 {"error":{"id":"429","category":"gw","code":"QUOTA_EXCEEDED","message":"Limit Exceeded"}}
```

이 오류가 나면 스크립트 버그가 아니라 일일 할당량 소진입니다.

## 서브커맨드와 엔드포인트

### 다중 경유지 경로 (순서 지정)
사용자가 지정한 순서대로 경유지를 방문하는 경로를 계산.

| 서브커맨드 | 최대 경유지 | 경로 |
|---|---|---|
| `multi-30` | 30개 | `POST /tmap/routes/routeSequential30` |
| `multi-100` | 100개 | `POST /tmap/routes/routeSequential100` |
| `multi-200` | 200개 | `POST /tmap/routes/routeSequential200` |

### 경유지 최적화 (순서 자동 결정)
여러 경유지를 방문할 때 **최적의 순서**를 자동으로 결정. TSP(외판원 문제) 근사.

| 서브커맨드 | 최대 경유지 | 경로 | Free 한도 |
|---|---|---|---|
| `optimize-10` | 10개 | `POST /tmap/routes/routeOptimization10` | 일반적 |
| `optimize-20` | 20개 | `POST /tmap/routes/routeOptimization20` | 일반적 |
| `optimize-30` | 30개 | `POST /tmap/routes/routeOptimization30` | **1회/일** |
| `optimize-100` | 100개 | `POST /tmap/routes/routeOptimization100` | **1회/일** |

**선택 규칙**: 경유지 개수에 맞는 가장 작은 variant를 선택. 예: 5개 최적화는 `optimize-10`. Free 요금제라면 30/100 전에 20을 먼저 고려.

## 필수 파라미터

- `--start-name`, `--start-x`, `--start-y` — 출발지
- `--start-time "YYYYMMDDHHmm"` — 출발 시각
- `--end-name`, `--end-x`, `--end-y` — 도착지
- `--stops-json '[...]'` 또는 `--stops-file path.json` — 경유지 배열

## viaPoints 스키마

각 경유지 객체:
```json
[
  {
    "viaPointId": "1",
    "viaPointName": "강남역",
    "viaX": "127.027926",
    "viaY": "37.497952"
  },
  {
    "viaPointId": "2",
    "viaPointName": "홍대입구역",
    "viaX": "126.924191",
    "viaY": "37.556853"
  }
]
```

- `viaPointId` — 경유지 고유 ID (문자열)
- `viaPointName` — 이름
- `viaX`, `viaY` — 경도/위도 (문자열)

## 선택 파라미터

- `--search-option` — 경로 옵션 (route.py car와 동일)
- `--car-type` — 차종 1~6
- `--req-coord-type`, `--res-coord-type`

## 응답 구조

### 다중 경유지 (multi-*)
`route.py car`와 유사한 GeoJSON FeatureCollection. `properties.totalDistance`, `totalTime`이 **최상위에** 위치.

### 경유지 최적화 (optimize-*)
GeoJSON FeatureCollection. 경유지가 **재배열된 순서**로 Point feature에 나타남. 각 Point의 properties:
- `viaPointId`, `viaPointName`
- `index` — 방문 순서 (0=출발)
- `pointType` — `S`(출발) / `B1`, `B2`, ...(경유 순서) / `E`(도착)
- `arriveTime`, `completeTime` — 도착 시각 (YYYYMMDDHHmmss)
- `distance` — 이전 지점에서의 거리 (m)

요약 함수(`--summarize standard`)가 이 정보를 `waypointOrder` 배열로 추출합니다.

## 예시

```bash
# 5개 경유지 순서 최적화
cat > /tmp/stops.json << 'EOF'
[
  {"viaPointId":"1","viaPointName":"강남역","viaX":"127.027926","viaY":"37.497952"},
  {"viaPointId":"2","viaPointName":"홍대","viaX":"126.924191","viaY":"37.556853"},
  {"viaPointId":"3","viaPointName":"서울역","viaX":"126.970682","viaY":"37.554722"},
  {"viaPointId":"4","viaPointName":"잠실역","viaX":"127.100232","viaY":"37.513294"},
  {"viaPointId":"5","viaPointName":"건대입구역","viaX":"127.069847","viaY":"37.540218"}
]
EOF

python3 waypoints.py optimize-10 \
  --start-name "시청" --start-x 126.977816 --start-y 37.566323 \
  --start-time "202604110900" \
  --end-name "시청" --end-x 126.977816 --end-y 37.566323 \
  --stops-file /tmp/stops.json \
  --summarize standard

# 순서 고정 다중 경유지
python3 waypoints.py multi-30 \
  --start-name "시청" --start-x 126.977816 --start-y 37.566323 \
  --start-time "202604110900" \
  --end-name "광화문" --end-x 126.9770 --end-y 37.5760 \
  --stops-json '[{"viaPointId":"1","viaPointName":"시청역","viaX":"126.977","viaY":"37.564"}]' \
  --summarize standard
```

## 주의사항

- 최적화는 근사 알고리즘 — 경유지가 많을수록 최적해 보장 어려움
- `viaPoints`의 순서는 `multi-*`에서 그대로 유지, `optimize-*`에서 재배열됨
- **Free 요금제 주의**: optimize-30/100은 일일 1회. 불필요하게 반복 호출하지 말 것
- 응답이 매우 큼 (경유지 수에 비례). 사용자 응답에는 `--summarize` 필수
- 차종과 검색 옵션은 모든 구간에 동일 적용
- `startTime`은 과거 시각도 허용되지만 미래 시각이 일반적
- 출발·도착 시각 예측(타임머신)은 waypoints 엔드포인트가 아니라 `route.py predict`(`/tmap/routes/prediction`)로 한다. 경유지 최대 5개, 순서 고정. 최적 순서가 필요하면 `optimize-*`로 순서만 구한 뒤 그 순서로 `predict --via ...` 호출 (SKILL.md 6번 흐름)

#!/usr/bin/env python3
"""TMap 경로안내 (자동차/보행자/직선거리/타임머신).

티맵 API의 얇은 래퍼. 서브커맨드 = 티맵 엔드포인트 1:1.

서브커맨드:
  car         자동차 경로       POST /tmap/routes?version=1
  predict     타임머신 자동차   POST /tmap/routes/prediction?version=1
  pedestrian  보행자 경로       POST /tmap/routes/pedestrian?version=1
  distance    직선거리          GET  /tmap/routes/distance?version=1

모든 서브커맨드는 --json, --summarize, --output-full, --pretty 공통 지원.
타임머신(미래 교통 예측)은 predict 서브커맨드 전용. /tmap/routes(car)는
predictionType/predictionTime을 무시하고 현재 교통 기준 결과를 돌려주므로 car에서는 막는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tmap_client import (
    TmapClient,
    apply_summarize,
    die,
    handle_error_and_exit,
    merge_body,
    output_json,
    parse_json_body,
)

KST = timezone(timedelta(hours=9))
PREDICT_MAX_WAYPOINTS = 5  # 6개부터 400 (code 1100), 2026-10-01 실측


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", dest="raw_json", help="raw JSON 요청 바디를 직접 전달 (다른 플래그와 병합, raw가 우선)")
    p.add_argument(
        "--summarize",
        nargs="?",
        const="standard",
        choices=["minimal", "standard", "full"],
        help="응답을 요약하여 출력. 값 없이 쓰면 standard.",
    )
    p.add_argument("--turns", type=int, default=None, help="요약 시 턴바이턴 최대 개수 (기본 standard=10, full=무제한)")
    p.add_argument("--output-full", metavar="PATH", help="전체 원본 응답을 파일에 저장. stdout에는 요약 또는 원본 출력.")
    p.add_argument("--pretty", action="store_true", help="stdout JSON을 들여쓰기해서 출력")
    p.add_argument("--version", default="1", help="API version 쿼리 파라미터 (기본 1)")


def add_route_params(p: argparse.ArgumentParser, *, mode: str) -> None:
    # 출발/도착 좌표 (모든 경로 서브커맨드 공통)
    p.add_argument("--start-x", "--startX", dest="startX", help="출발 경도 (startX)")
    p.add_argument("--start-y", "--startY", dest="startY", help="출발 위도 (startY)")
    p.add_argument("--end-x", "--endX", dest="endX", help="도착 경도 (endX)")
    p.add_argument("--end-y", "--endY", dest="endY", help="도착 위도 (endY)")
    p.add_argument("--start-name", "--startName", dest="startName", help="출발지 이름 (보행자 경로는 필수)")
    p.add_argument("--end-name", "--endName", dest="endName", help="도착지 이름 (보행자 경로는 필수)")
    p.add_argument(
        "--req-coord-type",
        "--reqCoordType",
        dest="reqCoordType",
        default="WGS84GEO",
        help="요청 좌표계 (기본 WGS84GEO)",
    )
    p.add_argument(
        "--res-coord-type",
        "--resCoordType",
        dest="resCoordType",
        default="WGS84GEO",
        help="응답 좌표계 (기본 WGS84GEO)",
    )
    p.add_argument("--angle", type=int, help="출발 각도 (0~360)")
    p.add_argument(
        "--search-option",
        "--searchOption",
        dest="searchOption",
        help="경로탐색 옵션 (자동차: 0 교통최적+추천(기본), 1 무료우선, 2 최소시간, 3 초보, 4 고속도로우선, 10 최단거리, 12 이륜차도로우선, 19 어린이보호구역 회피 / 보행자: 0 추천, 4 대로우선, 10 최단, 30 계단제외)",
    )
    if mode == "car":
        p.add_argument("--traffic-info", "--trafficInfo", dest="trafficInfo", choices=["Y", "N"], help="실시간 교통정보 반영")
        p.add_argument("--car-type", "--carType", dest="carType", help="차종 (1~6)")
        p.add_argument("--tollgate-car-type", "--tollgateCarType", dest="tollgateCarType", help="통행료 차종")
        p.add_argument("--total-value", "--totalValue", dest="totalValue", help="전기/수소차 관련 값")
        p.add_argument("--pass-list", "--passList", dest="passList", help="경유지 리스트 'X1,Y1_X2,Y2' 형식")
        p.add_argument("--pass-search-flag", "--passSearchFlag", dest="passSearchFlag", help="경유지 검색 플래그")
        p.add_argument("--direction-option", "--directionOption", dest="directionOption", help="경로 방향 옵션")
        p.add_argument("--route-type", "--routeType", dest="routeType", help="경로 유형")
        p.add_argument("--sort", help="경로 정렬 기준")
        p.add_argument("--detail-pos-flag", "--detailPosFlag", dest="detailPosFlag", help="상세 좌표 플래그")
        # 타임머신은 /tmap/routes에서 무시된다 — 받기만 하고 run_car에서 predict로 안내
        p.add_argument("--prediction-type", "--predictionType", dest="predictionType", help=argparse.SUPPRESS)
        p.add_argument("--prediction-time", "--predictionTime", dest="predictionTime", help=argparse.SUPPRESS)
    if mode == "pedestrian":
        p.add_argument("--pass-list", "--passList", dest="passList", help="경유지 리스트 'X1,Y1_X2,Y2' 형식")
        p.add_argument("--sort", help="경로 정렬 기준")


def add_predict_params(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start-x", "--startX", dest="startX", required=True, help="출발 경도")
    p.add_argument("--start-y", "--startY", dest="startY", required=True, help="출발 위도")
    p.add_argument("--end-x", "--endX", dest="endX", required=True, help="도착 경도")
    p.add_argument("--end-y", "--endY", dest="endY", required=True, help="도착 위도")
    # name이 비어 있으면 9401(필수 파라메터 없음)로 거부된다
    p.add_argument("--start-name", "--startName", dest="startName", default="출발지", help="출발지 이름 (기본 '출발지', 빈 값 불가)")
    p.add_argument("--end-name", "--endName", dest="endName", default="도착지", help="도착지 이름 (기본 '도착지', 빈 값 불가)")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument(
        "--arrive-by",
        metavar="TIME",
        help="이 시각에 도착 → 출발 시각(departureTime) 계산. API의 predictionType=departure. "
        "TIME: 2026-10-02T09:00:00+0900, 2026-10-02 09:00, 202610020900 등 (오프셋 없으면 KST)",
    )
    when.add_argument(
        "--depart-at",
        metavar="TIME",
        help="이 시각에 출발 → 도착 시각(arrivalTime) 계산. API의 predictionType=arrival. TIME 형식은 --arrive-by와 같음",
    )
    when.add_argument(
        "--prediction-type",
        "--predictionType",
        dest="predictionType",
        choices=["departure", "arrival"],
        help="API 값을 그대로 지정 (이름과 의미가 반대: departure=predictionTime이 도착 시각, arrival=predictionTime이 출발 시각). "
        "가능하면 --arrive-by/--depart-at을 쓸 것",
    )
    p.add_argument("--prediction-time", "--predictionTime", dest="predictionTime", metavar="TIME", help="--prediction-type과 함께 쓰는 시각")
    p.add_argument(
        "--via",
        action="append",
        metavar="LON,LAT",
        help=f"경유지 '경도,위도' (반복 지정, 입력 순서대로 방문, 최대 {PREDICT_MAX_WAYPOINTS}개)",
    )
    p.add_argument("--pass-list", "--passList", dest="passList", help="경유지 'X1,Y1_X2,Y2' 형식 (--via와 합쳐짐, --via 뒤에 붙음)")
    p.add_argument(
        "--search-option",
        "--searchOption",
        dest="searchOption",
        default="00",
        help="경로탐색 옵션, 두 자리 (00 교통최적+추천(기본), 01 무료우선, 02 최소시간, 03 초보, 04 고속도로우선, 10 최단거리, 19 어린이보호구역 회피). 한 자리는 0을 앞에 채움. 12(이륜차)는 400",
    )
    p.add_argument("--traffic-info", "--trafficInfo", dest="trafficInfo", choices=["Y", "N"], default="N", help="교통정보 포함 여부 (기본 N)")
    p.add_argument("--tollgate-car-type", "--tollgateCarType", dest="tollgateCarType", default="car", help="통행료 차종: car(기본), mediumvan, largevan, largetruck, specialtruck, smallcar(경차), twowheel")
    p.add_argument("--req-coord-type", "--reqCoordType", dest="reqCoordType", default="WGS84GEO", help="요청 좌표계 (기본 WGS84GEO)")
    p.add_argument("--res-coord-type", "--resCoordType", dest="resCoordType", default="WGS84GEO", help="응답 좌표계 (기본 WGS84GEO)")
    p.add_argument("--sort", default="index", help="응답 feature 정렬 (기본 index)")
    p.add_argument(
        "--total-value",
        "--totalValue",
        dest="totalValue",
        default="2",
        help="2=요약만 (feature 1개, 총거리/시간/요금/출발·도착 시각, 기본) / 1=전체 경로 (턴바이턴·geometry 포함)",
    )


def build_body(args: argparse.Namespace, *, mode: str) -> dict:
    """CLI args를 티맵 API 요청 바디로 변환."""
    body: dict = {}
    field_names = [
        "startX",
        "startY",
        "endX",
        "endY",
        "startName",
        "endName",
        "reqCoordType",
        "resCoordType",
        "angle",
        "searchOption",
        "trafficInfo",
        "carType",
        "tollgateCarType",
        "totalValue",
        "passList",
        "passSearchFlag",
        "directionOption",
        "routeType",
        "sort",
        "detailPosFlag",
        "predictionType",
        "predictionTime",
    ]
    for name in field_names:
        val = getattr(args, name, None)
        if val is not None:
            body[name] = val
    # 출발/도착지 이름 URL 인코딩 (티맵 API가 요구)
    for k in ("startName", "endName"):
        if k in body and isinstance(body[k], str):
            from urllib.parse import quote
            if all(ord(c) < 128 for c in body[k]):
                pass  # 이미 ASCII면 그대로
            else:
                body[k] = quote(body[k], safe="")
    return body


def run_car(args: argparse.Namespace) -> None:
    body = build_body(args, mode="car")
    override = parse_json_body(args.raw_json)
    body = merge_body(body, override)
    if "predictionType" in body or "predictionTime" in body:
        die(
            "car(/tmap/routes)는 predictionType/predictionTime을 무시하고 현재 교통 기준 결과를 반환합니다.\n"
            "타임머신은 `route.py predict --arrive-by <시각>` (도착 시각 기준 → 출발 시각 계산) 또는\n"
            "`route.py predict --depart-at <시각>` (출발 시각 기준 → 도착 시각 계산)을 사용하세요.",
            code=2,
        )
    client = TmapClient()
    resp = client.post("/tmap/routes", body=body, query={"version": args.version})
    handle_output(resp, args, kind="route")


def normalize_prediction_time(value: str) -> str:
    """predictionTime을 API가 받는 유일한 형식 `YYYY-MM-DDTHH:MM:SS+0900`으로 맞춘다.

    API는 콜론 들어간 오프셋(+09:00), 초 생략, 오프셋 생략을 모두 400으로 거부한다.
    입력은 `YYYYMMDDHHMM`(KST) 또는 `YYYY-MM-DD[T ]HH:MM[:SS][Z|±HH:MM|±HHMM]`
    (오프셋 없으면 KST)를 받아 KST로 변환한다.
    """
    s = value.strip()
    bad = f"예측 시각 형식을 해석할 수 없습니다: {value!r} (예: 2026-10-02T09:00:00+0900, 202610020900)"
    try:
        if re.fullmatch(r"\d{12}", s):
            return datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=KST).strftime("%Y-%m-%dT%H:%M:%S%z")
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?\s*(Z|[+-]\d{2}:?\d{2})?", s)
        if not m:
            die(bad, code=2)
        date_part, hm, sec, offset = m.groups()
        dt = datetime.strptime(f"{date_part}T{hm}{sec or ':00'}", "%Y-%m-%dT%H:%M:%S")
    except ValueError:  # 25시, 13월 같은 범위 밖 값
        die(bad, code=2)
    if offset in (None, ""):
        dt = dt.replace(tzinfo=KST)
    elif offset == "Z":
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        sign = 1 if offset[0] == "+" else -1
        digits = offset[1:].replace(":", "")
        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))))
    return dt.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_waypoints(args: argparse.Namespace) -> list[dict]:
    """--via (반복) 와 --pass-list 'X1,Y1_X2,Y2'를 wayPoint 배열로 변환. 순서 유지."""
    raw: list[str] = list(args.via or [])
    if args.passList:
        raw.extend(part for part in args.passList.split("_") if part.strip())
    points = []
    for item in raw:
        parts = [x.strip() for x in item.split(",")]
        if len(parts) != 2:
            die(f"경유지 형식은 '경도,위도' 입니다: {item!r}", code=2)
        try:
            float(parts[0]), float(parts[1])
        except ValueError:
            die(f"경유지 좌표가 숫자가 아닙니다: {item!r}", code=2)
        points.append({"lon": parts[0], "lat": parts[1]})
    if len(points) > PREDICT_MAX_WAYPOINTS:
        die(
            f"타임머신 경유지는 최대 {PREDICT_MAX_WAYPOINTS}개입니다 (받은 개수: {len(points)}). "
            "구간을 나눠 뒤에서부터 --arrive-by로 이어 계산하세요 (SKILL.md 6번 흐름).",
            code=2,
        )
    return points


def run_predict(args: argparse.Namespace) -> None:
    # 주의: API의 predictionType 이름은 직관과 반대다 (공식 문서 정의, 2026-10-01 실측 일치).
    #   departure → predictionTime을 '도착' 시각으로 보고 departureTime을 계산
    #   arrival   → predictionTime을 '출발' 시각으로 보고 arrivalTime을 계산
    if args.arrive_by:
        ptype, ptime = "departure", args.arrive_by
    elif args.depart_at:
        ptype, ptime = "arrival", args.depart_at
    else:
        if not args.predictionTime:
            die("--prediction-type에는 --prediction-time이 필요합니다.", code=2)
        ptype, ptime = args.predictionType, args.predictionTime
    if args.predictionTime and (args.arrive_by or args.depart_at):
        die("--prediction-time은 --prediction-type과만 함께 씁니다 (--arrive-by/--depart-at에는 시각이 이미 들어 있음).", code=2)

    search_option = args.searchOption
    if search_option.isdigit() and len(search_option) == 1:
        search_option = "0" + search_option  # API는 두 자리만 받음 ("0" → 400)

    routes_info: dict = {
        "departure": {"name": args.startName, "lon": args.startX, "lat": args.startY},
        "destination": {"name": args.endName, "lon": args.endX, "lat": args.endY},
        "predictionType": ptype,
        "predictionTime": normalize_prediction_time(ptime),
        "searchOption": search_option,
        "tollgateCarType": args.tollgateCarType,
        "trafficInfo": args.trafficInfo,
    }
    waypoints = parse_waypoints(args)
    if waypoints:
        routes_info["wayPoints"] = {"wayPoint": waypoints}
    override = parse_json_body(args.raw_json)
    routes_info = merge_body(routes_info, override)

    query = {
        "version": args.version,
        "reqCoordType": args.reqCoordType,
        "resCoordType": args.resCoordType,
        "sort": args.sort,
        "totalValue": args.totalValue,
    }
    client = TmapClient()
    resp = client.post("/tmap/routes/prediction", body={"routesInfo": routes_info}, query=query)
    handle_output(resp, args, kind="route")


def run_pedestrian(args: argparse.Namespace) -> None:
    client = TmapClient()
    body = build_body(args, mode="pedestrian")
    override = parse_json_body(args.raw_json)
    body = merge_body(body, override)
    resp = client.post("/tmap/routes/pedestrian", body=body, query={"version": args.version})
    handle_output(resp, args, kind="route")


def run_distance(args: argparse.Namespace) -> None:
    client = TmapClient()
    query = {
        "version": args.version,
        "startX": args.startX,
        "startY": args.startY,
        "endX": args.endX,
        "endY": args.endY,
        "reqCoordType": args.reqCoordType,
        "resCoordType": args.resCoordType,
    }
    override = parse_json_body(args.raw_json)
    if override:
        query.update(override)
    resp = client.get("/tmap/routes/distance", query=query)
    handle_output(resp, args, kind=None)


def handle_output(resp, args: argparse.Namespace, *, kind: str | None) -> None:
    if args.output_full:
        Path(args.output_full).write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    data = resp
    if args.summarize and kind:
        data = apply_summarize(resp, kind=kind, level=args.summarize, extra={"turns": args.turns})
    output_json(data, pretty=args.pretty)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="route.py",
        description="TMap 경로안내 얇은 래퍼 (자동차/타임머신/보행자/직선거리)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    car = sub.add_parser("car", help="자동차 경로 (POST /tmap/routes)")
    add_common_args(car)
    add_route_params(car, mode="car")
    car.set_defaults(func=run_car)

    pred = sub.add_parser(
        "predict",
        help="타임머신 자동차 경로 (POST /tmap/routes/prediction) — 미래 시각 교통 예측, 출발/도착 시각 계산",
        description=(
            "타임머신 자동차 경로. 응답 features[0].properties에 departureTime/arrivalTime이 들어온다. "
            "도착 시각을 정해 두고 출발 시각을 구하려면 --arrive-by, 출발 시각을 정해 두고 도착 시각을 구하려면 --depart-at."
        ),
    )
    add_common_args(pred)
    add_predict_params(pred)
    pred.set_defaults(func=run_predict)

    ped = sub.add_parser("pedestrian", help="보행자 경로 (POST /tmap/routes/pedestrian)")
    add_common_args(ped)
    add_route_params(ped, mode="pedestrian")
    ped.set_defaults(func=run_pedestrian)

    dist = sub.add_parser("distance", help="직선거리 (GET /tmap/routes/distance)")
    add_common_args(dist)
    add_route_params(dist, mode="distance")
    dist.set_defaults(func=run_distance)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        handle_error_and_exit(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())

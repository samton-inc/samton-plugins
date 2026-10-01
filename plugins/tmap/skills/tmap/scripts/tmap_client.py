#!/usr/bin/env python3
"""TMap API 공유 클라이언트 및 유틸리티.

설계 원칙: 티맵 API의 얇고 충실한 래퍼. 파라미터/응답 가공은 최소화.
요약 함수는 선택적으로만 사용 (각 스크립트의 --summarize 플래그가 호출).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import urllib.error
import urllib.parse
import urllib.request


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
ENV_VAR = "TMAP_APP_KEY"

# 키 파일 검색 경로 (우선순위 순)
# 1. XDG 표준 위치 — 환경 불문 기본 (~/.config/tmap/tmap.local.md)
# 2. 플러그인 내부 .claude/ — 기존 Claude Code 플러그인 환경 하위호환
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
STANDARD_KEY_FILE = _XDG_CONFIG_HOME / "tmap" / "tmap.local.md"
LEGACY_PLUGIN_KEY_FILE = PLUGIN_ROOT / ".claude" / "tmap.local.md"

TMAP_BASE_URL = "https://apis.openapi.sk.com"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 2


def _candidate_key_files() -> list[Path]:
    """키 파일 검색 우선순위. 앞이 우선."""
    return [STANDARD_KEY_FILE, LEGACY_PLUGIN_KEY_FILE]


def _find_existing_key_file() -> Path | None:
    """실제 존재하는 키 파일 중 가장 우선순위 높은 것. 없으면 None."""
    for p in _candidate_key_files():
        if p.exists():
            return p
    return None


def _primary_write_target() -> Path:
    """신규 쓰기 시 사용할 기본 경로. 기존 파일이 있으면 그 위치를 유지."""
    existing = _find_existing_key_file()
    return existing or STANDARD_KEY_FILE


class MissingKeyError(RuntimeError):
    """API 키가 설정되지 않은 경우."""


class TmapAPIError(RuntimeError):
    """티맵 API가 오류 응답을 반환한 경우."""

    def __init__(self, status: int, body: Any, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"TMap API {status} at {url}: {body}")


PRODUCT_KEYS = ("base", "transit")


def load_frontmatter(path: Path | None = None) -> dict[str, str]:
    """지정한(또는 검색된 기존) `.local.md` 파일의 YAML frontmatter를 dict로 파싱.

    path가 None이면 `_find_existing_key_file()` 결과 사용.
    파일 없으면 빈 dict.
    """
    target = path if path is not None else _find_existing_key_file()
    if target is None or not target.exists():
        return {}
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def save_frontmatter(fields: dict[str, str], path: Path | None = None) -> Path:
    """기존 frontmatter를 읽어 fields를 병합 후 다시 쓴다. 기존 body는 유지.

    path가 None이면 `_primary_write_target()` 결과 사용 (기존 파일이 있으면 그 위치,
    없으면 XDG 표준 위치).
    """
    target = path if path is not None else _primary_write_target()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    body_after_frontmatter = ""
    if target.exists():
        text = target.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=False)
        if lines and lines[0].strip() == "---":
            end_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_idx = i
                    break
            if end_idx is not None:
                for i in range(1, end_idx):
                    line = lines[i]
                    if ":" in line:
                        k, _, v = line.partition(":")
                        existing[k.strip()] = v.strip().strip('"').strip("'")
                body_after_frontmatter = "\n".join(lines[end_idx + 1 :])

    # 기존 필드를 유지하며 새 값 병합
    merged = dict(existing)
    for k, v in fields.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = str(v)

    # frontmatter 재구성 (키 순서: tmap_app_key → product_* → last_checked → 나머지)
    ordered_keys: list[str] = []
    for preferred in ("tmap_app_key",):
        if preferred in merged:
            ordered_keys.append(preferred)
    for k in sorted(merged.keys()):
        if k.startswith("product_"):
            ordered_keys.append(k)
    if "last_checked" in merged:
        ordered_keys.append("last_checked")
    for k in sorted(merged.keys()):
        if k not in ordered_keys:
            ordered_keys.append(k)

    frontmatter_lines = ["---"]
    for k in ordered_keys:
        frontmatter_lines.append(f"{k}: {merged[k]}")
    frontmatter_lines.append("---")

    if not body_after_frontmatter.strip():
        body_after_frontmatter = (
            "\n# TMap API Local Settings\n\n"
            "이 파일은 민감한 정보(API 키)를 담고 있으므로 외부에 노출되지 않도록 관리하세요.\n"
            "플러그인 내부 경로라면 `.gitignore`로 이미 제외되어 있고, "
            "XDG config 경로(`~/.config/tmap/`)라면 홈 디렉토리 밖으로 유출되지 않습니다.\n"
        )

    content = "\n".join(frontmatter_lines) + "\n" + body_after_frontmatter
    if not content.endswith("\n"):
        content += "\n"
    target.write_text(content, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def load_app_key() -> str:
    """API 키를 파일 → 환경변수 순서로 로드."""
    fm = load_frontmatter()
    key = fm.get("tmap_app_key", "").strip()
    if key:
        return key
    env_key = os.environ.get(ENV_VAR, "").strip()
    if env_key:
        return env_key
    raise MissingKeyError(
        "TMap API 키가 설정되지 않았습니다.\n"
        "다음 중 하나로 설정하세요:\n"
        f"  1. setup_key.py <키> 명령으로 파일 저장\n"
        f"     (기본 위치: {STANDARD_KEY_FILE})\n"
        f"  2. 환경변수 {ENV_VAR} 설정\n"
        "API 키 발급: https://openapi.sk.com/"
    )


def save_app_key(key: str) -> Path:
    """API 키를 frontmatter에 저장. 경로는 _primary_write_target() 결정."""
    key = key.strip()
    if not key:
        raise ValueError("빈 API 키는 저장할 수 없습니다.")
    return save_frontmatter({"tmap_app_key": key})


def load_product_status() -> dict[str, str]:
    """상품별 활성화 상태를 로드. 없으면 모두 'unknown'."""
    fm = load_frontmatter()
    status: dict[str, str] = {}
    for key in PRODUCT_KEYS:
        status[key] = fm.get(f"product_{key}", "unknown")
    status["last_checked"] = fm.get("last_checked", "")
    return status


def save_product_status(status: dict[str, str]) -> Path:
    """상품별 상태와 last_checked를 frontmatter에 저장. 기존 다른 필드는 보존."""
    fields: dict[str, str] = {}
    for key in PRODUCT_KEYS:
        if key in status:
            fields[f"product_{key}"] = status[key]
    if "last_checked" in status:
        fields["last_checked"] = status["last_checked"]
    return save_frontmatter(fields)


def _maybe_gunzip(data: bytes) -> bytes:
    """Content-Encoding 헤더 없이 gzip 본문이 오는 경우가 있어 매직 바이트로 판별해 푼다."""
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


class TmapClient:
    """티맵 API HTTP 클라이언트. 얇은 래퍼 — 재시도, 헤더, 타임아웃만 담당."""

    def __init__(
        self,
        app_key: str | None = None,
        base_url: str = TMAP_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.app_key = app_key or load_app_key()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        return url

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        accept: str = "application/json",
    ) -> Any:
        """HTTP 요청 실행. 응답은 JSON이면 파싱, 바이너리면 bytes 반환."""
        url = self._url(path, query)
        headers = {
            "appKey": self.app_key,
            "Accept": accept,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    content = _maybe_gunzip(resp.read())
                    ctype = resp.headers.get("Content-Type", "")
                    if "json" in ctype or accept == "application/json":
                        if not content:
                            return None
                        return json.loads(content.decode("utf-8"))
                    return content
            except urllib.error.HTTPError as e:
                body_text: Any
                raw = b""
                try:
                    raw = _maybe_gunzip(e.read())
                    body_text = json.loads(raw.decode("utf-8"))
                except Exception:
                    body_text = raw.decode("utf-8", errors="replace") if raw else ""
                if 500 <= e.code < 600 and attempt < self.retries:
                    last_exc = e
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise TmapAPIError(e.code, body_text, url) from e
            except urllib.error.URLError as e:
                if attempt < self.retries:
                    last_exc = e
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable")

    def get(self, path: str, query: dict[str, Any] | None = None, **kwargs) -> Any:
        return self.request("GET", path, query=query, **kwargs)

    def post(self, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None, **kwargs) -> Any:
        return self.request("POST", path, query=query, body=body, **kwargs)


# ---------------------------------------------------------------------------
# 요약 함수 (옵션). 각 스크립트의 --summarize 플래그가 호출.
# 요약은 기본 동작이 아님. 호출자가 명시적으로 선택해야 사용됨.
# ---------------------------------------------------------------------------


def summarize_route(resp: dict, level: str = "standard", turns: int | None = None) -> dict:
    """티맵 경로 응답 요약. 자동차/보행자/경유지 최적화 세 가지 응답 구조를 모두 처리.

    구조 차이:
    - route.py car/pedestrian/predict: totalDistance/totalTime이 features[0].properties에 존재
      (predict는 departureTime/arrivalTime도 함께 — 요약에 그대로 포함)
    - waypoints.py optimize-*: totalDistance/totalTime이 top-level properties에 존재, features의 properties는 경유지 정보

    level: minimal | standard | full
    - minimal: 거리, 시간, 요금만
    - standard: + 출발/도착 좌표, 턴바이턴 요약, 경유지 순서 (있는 경우)
    - full: standard에 모든 턴바이턴
    """
    if not isinstance(resp, dict):
        return {"error": "not a dict response"}
    features = resp.get("features") or []

    # 총 거리/시간/요금: 먼저 top-level properties에서 찾고 (waypoints optimize), 없으면 features에서 (일반 경로)
    top_props = resp.get("properties") or {}
    props: dict = {}
    if "totalDistance" in top_props or "totalTime" in top_props:
        props = top_props
    else:
        for f in features:
            p = f.get("properties") or {}
            if "totalDistance" in p or "totalTime" in p:
                props = p
                break

    out: dict[str, Any] = {
        "totalDistance_m": _maybe_int(props.get("totalDistance")),
        "totalTime_s": _maybe_int(props.get("totalTime")),
        "totalFare_krw": _maybe_int(props.get("totalFare")),
        "taxiFare_krw": _maybe_int(props.get("taxiFare")),
    }
    # 타임머신(route.py predict) 응답에만 있음
    for key in ("departureTime", "arrivalTime"):
        if props.get(key):
            out[key] = props[key]

    if level == "minimal":
        return out

    points = [f for f in features if (f.get("geometry") or {}).get("type") == "Point"]
    if points:
        # 경유지가 있으면 도착(E) Point 뒤에 경유지 마커 Point가 덧붙어 오므로 pointType을 우선한다
        by_type = {}
        for f in points:
            by_type.setdefault((f.get("properties") or {}).get("pointType"), f)
        out["startPoint"] = (by_type.get("S") or points[0]).get("geometry", {}).get("coordinates")
        out["endPoint"] = (by_type.get("E") or points[-1]).get("geometry", {}).get("coordinates")

    # 경유지 순서 (waypoints optimize 응답만): Point feature에 viaPointName이 있으면 수집
    # 일반 경로(route.py) 응답에는 viaPointName이 없으므로 이 블록이 실행되지 않음
    via_order = []
    for f in points:
        p = f.get("properties") or {}
        if p.get("viaPointName"):
            via_order.append(
                {
                    "index": _maybe_int(p.get("index")),
                    "name": p.get("viaPointName"),
                    "viaPointId": p.get("viaPointId"),
                    "pointType": p.get("pointType"),  # S=출발, E=도착, B1/B2/...=경유
                    "arriveTime": p.get("arriveTime"),
                    "completeTime": p.get("completeTime"),
                    "distance_m": _maybe_int(p.get("distance")),
                }
            )
    if via_order:
        out["waypointOrder"] = via_order

    # 턴바이턴: Point feature의 description (일반 route 응답)
    turn_list = []
    for f in points:
        p = f.get("properties") or {}
        desc = p.get("description")
        if desc:
            turn_list.append(
                {
                    "step": p.get("pointIndex") or p.get("index"),
                    "description": desc,
                    "distance_m": _maybe_int(p.get("distance")),
                    "time_s": _maybe_int(p.get("time")),
                }
            )
    if level == "standard" and turns is None:
        turns = 10
    if turns is not None and turns >= 0 and turns < len(turn_list):
        out["turnByTurn"] = turn_list[:turns]
    else:
        out["turnByTurn"] = turn_list
    out["turnByTurnTotalCount"] = len([f for f in points if (f.get("properties") or {}).get("description")])
    return out


def _maybe_int(v: Any) -> Any:
    """문자열 숫자를 int로 변환. 변환 실패 시 원본 그대로 반환."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v


def summarize_poi(resp: dict, limit: int | None = None) -> dict:
    """POI 통합검색 응답을 요약."""
    search = (resp or {}).get("searchPoiInfo") or {}
    pois = (search.get("pois") or {}).get("poi") or []
    if limit is not None:
        pois = pois[:limit]
    simplified = []
    for p in pois:
        simplified.append(
            {
                "name": p.get("name"),
                "address": _join_address(p),
                "tel": p.get("telNo"),
                "lat": _to_float(p.get("frontLat") or p.get("noorLat")),
                "lon": _to_float(p.get("frontLon") or p.get("noorLon")),
                "distance_m": _to_float(p.get("radius")) and _to_float(p.get("radius")) * 1000 if p.get("radius") else None,
                "category": p.get("bizName") or p.get("middleBizName"),
            }
        )
    return {
        "totalCount": search.get("totalCount"),
        "count": search.get("count"),
        "page": search.get("page"),
        "results": simplified,
    }


def summarize_transit(resp: dict, options: int | None = None) -> dict:
    """대중교통 경로 응답을 요약."""
    metadata = (resp or {}).get("metaData") or {}
    plans = (metadata.get("plan") or {}).get("itineraries") or []
    if options is not None:
        plans = plans[:options]
    simplified = []
    for it in plans:
        legs = it.get("legs") or []
        leg_summary = []
        for leg in legs:
            leg_summary.append(
                {
                    "mode": leg.get("mode"),
                    "route": leg.get("route") or leg.get("routeColor"),
                    "start": (leg.get("start") or {}).get("name"),
                    "end": (leg.get("end") or {}).get("name"),
                    "time_s": leg.get("sectionTime"),
                }
            )
        simplified.append(
            {
                "totalTime_s": it.get("totalTime"),
                "totalDistance_m": it.get("totalDistance"),
                "totalFare_krw": (it.get("fare") or {}).get("regular", {}).get("totalFare"),
                "transferCount": it.get("transferCount"),
                "legs": leg_summary,
            }
        )
    return {
        "requestParameters": metadata.get("requestParameters"),
        "itineraries": simplified,
    }


def summarize_matrix(resp: dict) -> dict:
    """경로 매트릭스 응답을 표 형태로 요약."""
    rows = (resp or {}).get("matrixRoutes") or []
    table = []
    for r in rows:
        table.append(
            {
                "originIndex": r.get("originIndex"),
                "destinationIndex": r.get("destinationIndex"),
                "distance_m": r.get("distance"),
                "duration_s": r.get("duration"),
            }
        )
    return {"matrix": table}


def _join_address(p: dict) -> str:
    parts = [
        p.get("upperAddrName"),
        p.get("middleAddrName"),
        p.get("lowerAddrName"),
        p.get("detailAddrName"),
        p.get("mlClass") == "2" and p.get("firstBuildNo") or None,
    ]
    return " ".join([x for x in parts if x])


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 공통 CLI 헬퍼
# ---------------------------------------------------------------------------


def output_json(data: Any, *, pretty: bool = False) -> None:
    """JSON을 stdout에 출력. 기본 compact, --pretty 시 들여쓰기."""
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    print(text)


def apply_summarize(data: Any, kind: str, level: str, extra: dict | None = None) -> Any:
    """공통 요약 디스패처."""
    extra = extra or {}
    if kind == "route":
        return summarize_route(data, level=level, turns=extra.get("turns"))
    if kind == "poi":
        return summarize_poi(data, limit=extra.get("limit"))
    if kind == "transit":
        return summarize_transit(data, options=extra.get("options"))
    if kind == "matrix":
        return summarize_matrix(data)
    return data


def parse_json_body(raw: str | None) -> dict | None:
    """--json 플래그로 전달된 raw JSON 바디 파싱."""
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"--json 파싱 실패: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(obj, dict):
        print("--json은 JSON 객체여야 합니다.", file=sys.stderr)
        sys.exit(2)
    return obj


def merge_body(base: dict, override: dict | None) -> dict:
    """base 바디에 --json으로 받은 override를 병합."""
    if not override:
        return base
    merged = dict(base)
    merged.update(override)
    return merged


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def handle_error_and_exit(e: Exception) -> None:
    """표준 에러 출력."""
    if isinstance(e, MissingKeyError):
        print(str(e), file=sys.stderr)
        sys.exit(3)
    if isinstance(e, TmapAPIError):
        print(f"TMap API 오류 {e.status} ({e.url})", file=sys.stderr)
        if isinstance(e.body, (dict, list)):
            print(json.dumps(e.body, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(str(e.body), file=sys.stderr)
        sys.exit(4)
    print(f"알 수 없는 오류: {e}", file=sys.stderr)
    sys.exit(1)

"""KIS 시세 조회 래퍼 (자료팀).

인증·호출 간격·실계좌 도메인 차단은 프로그램팀 `stockking.broker.kis.KisPaperClient`를 그대로 쓴다.
이 모듈은 **시세 조회 TR만** 호출한다(주문 TR 없음).

⚠ TR ID·파라미터·응답 필드명은 KIS Developers 공식 문서로 재확인 필요 (SOURCES.md §6 확인 로그).
"""

from __future__ import annotations

import time
from datetime import date

# 국내주식 시세 TR (실전·모의 공통 시세)
TR_DAILY_CHART = "FHKST03010100"       # 기간별 시세(일봉), 1회 최대 100건
TR_MINUTE_TODAY = "FHKST03010200"      # 당일 분봉, 1회 30건
TR_MINUTE_BY_DATE = "FHKST03010230"    # 일자별 분봉(과거일자), 1회 120건, 보관 약 1년 ⚠
TR_PRICE = "FHKST01010100"             # 현재가 (종목 상태 코드 포함)
TR_INDEX_DAILY = "FHKUP03500100"       # 업종(지수) 기간별 시세

P_DAILY_CHART = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
P_MINUTE_TODAY = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
P_MINUTE_BY_DATE = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
P_PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
P_INDEX_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"

RATE_LIMIT_CODES = {"EGW00201"}   # 초당 거래건수 초과
MAX_RETRY = 5


class KisQuoteError(RuntimeError):
    pass


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _int(v) -> int:
    if v in (None, ""):
        return 0
    return int(float(v))


class KisQuotes:
    def __init__(self, client) -> None:
        self._c = client  # KisPaperClient (또는 테스트용 가짜)
        self.calls = 0

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        for attempt in range(MAX_RETRY):
            self.calls += 1
            res = self._c._call("GET", path, self._c._headers(tr_id), params)
            code = res.get("msg_cd", "")
            if res.get("rt_cd", "0") == "0":
                return res
            if code in RATE_LIMIT_CODES:
                time.sleep(1.0 + attempt)
                continue
            raise KisQuoteError(f"{tr_id} 실패: {code} {res.get('msg1', '')}".strip())
        raise KisQuoteError(f"{tr_id} 호출 한도 초과가 계속됨 ({MAX_RETRY}회)")

    # --- 일봉 ---
    def daily_bars(self, symbol: str, start: date, end: date, adjusted: bool = True) -> list[dict]:
        """[start, end] 일봉. 100건 단위로 끝 날짜를 당기며 페이지 조회. 오래된 날짜 → 최신 순으로 반환."""
        out: dict[str, dict] = {}
        cur_end = end
        while cur_end >= start:
            res = self._get(P_DAILY_CHART, TR_DAILY_CHART, {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": _ymd(start), "FID_INPUT_DATE_2": _ymd(cur_end),
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
            })
            rows = [r for r in res.get("output2") or [] if r.get("stck_bsop_date")]
            if not rows:
                break
            for r in rows:
                d = r["stck_bsop_date"]
                out[d] = {
                    "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "symbol": symbol,
                    "open": _int(r.get("stck_oprc")), "high": _int(r.get("stck_hgpr")),
                    "low": _int(r.get("stck_lwpr")), "close": _int(r.get("stck_clpr")),
                    "volume": _int(r.get("acml_vol")), "value": _int(r.get("acml_tr_pbmn")),
                }
            oldest = min(r["stck_bsop_date"] for r in rows)
            nxt = date(int(oldest[:4]), int(oldest[4:6]), int(oldest[6:])).toordinal() - 1
            if len(rows) < 100:
                break
            cur_end = date.fromordinal(nxt)
        return [out[k] for k in sorted(out) if start <= date.fromisoformat(out[k]["date"]) <= end]

    # --- 지수 일봉 ---
    def index_bars(self, index_code: str, start: date, end: date) -> tuple[str, list[dict]]:
        """(지수명, 일봉 목록). 지수명으로 코드가 맞는지 호출 측에서 검증한다."""
        out: dict[str, dict] = {}
        name = ""
        cur_end = end
        while cur_end >= start:
            res = self._get(P_INDEX_DAILY, TR_INDEX_DAILY, {
                "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": index_code,
                "FID_INPUT_DATE_1": _ymd(start), "FID_INPUT_DATE_2": _ymd(cur_end),
                "FID_PERIOD_DIV_CODE": "D",
            })
            name = name or (res.get("output1") or {}).get("hts_kor_isnm", "")
            rows = [r for r in res.get("output2") or [] if r.get("stck_bsop_date")]
            if not rows:
                break
            for r in rows:
                d = r["stck_bsop_date"]
                out[d] = {
                    "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    "open": float(r.get("bstp_nmix_oprc") or 0), "high": float(r.get("bstp_nmix_hgpr") or 0),
                    "low": float(r.get("bstp_nmix_lwpr") or 0), "close": float(r.get("bstp_nmix_prpr") or 0),
                }
            oldest = min(r["stck_bsop_date"] for r in rows)
            if len(rows) < 50:
                break
            cur_end = date.fromordinal(date(int(oldest[:4]), int(oldest[4:6]), int(oldest[6:])).toordinal() - 1)
        return name, [out[k] for k in sorted(out) if start <= date.fromisoformat(out[k]["date"]) <= end]

    # --- 현재가/상태 ---
    def price_status(self, symbol: str) -> dict:
        o = self._get(P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}).get("output") or {}
        return {
            "symbol": symbol,
            "price": _int(o.get("stck_prpr")),
            "status_code": o.get("iscd_stat_cls_code", ""),   # 51 관리 52 위험 53 경고 54 주의 58 정지 59 단기과열 ⚠
            "halted": o.get("temp_stop_yn", "N") == "Y",
            "liquidation": o.get("sltr_yn", "N") == "Y",      # 정리매매
            "short_overheat": o.get("short_over_yn", "N") == "Y",
            "warn_code": o.get("mrkt_warn_cls_code", "00"),   # 00 없음 01 주의 02 경고 03 위험 ⚠
            "market_name": o.get("rprs_mrkt_kor_name", ""),
        }

    # --- 분봉 ---
    def minute_bars(self, symbol: str, day: date) -> tuple[list[dict], list[dict]]:
        """해당 일자 1분봉 전체 (raw 응답 목록, 정리된 행). 09:00~15:30, 시간 오름차순."""
        target = _ymd(day)
        raws: list[dict] = []
        rows: dict[str, dict] = {}
        hour = "160000"
        for _ in range(10):  # 120건 × 4회면 하루치. 안전 상한 10회
            res = self._get(P_MINUTE_BY_DATE, TR_MINUTE_BY_DATE, {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": hour, "FID_INPUT_DATE_1": target,
                "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "",
            })
            raws.append({"params": {"symbol": symbol, "date": target, "hour": hour}, "response": res})
            got = [r for r in res.get("output2") or [] if r.get("stck_bsop_date") == target]  # 휴장일엔 전일 데이터가 올 수 있음
            if not got:
                break
            for r in got:
                t = r["stck_cntg_hour"]
                rows[t] = {
                    "date": day.isoformat(), "symbol": symbol, "time": f"{t[:2]}:{t[2:4]}:{t[4:]}",
                    "open": _int(r.get("stck_oprc")), "high": _int(r.get("stck_hgpr")),
                    "low": _int(r.get("stck_lwpr")), "close": _int(r.get("stck_prpr")),
                    "volume": _int(r.get("cntg_vol")), "cum_value": _int(r.get("acml_tr_pbmn")),  # 누적 거래대금
                }
            earliest = min(r["stck_cntg_hour"] for r in got)
            if earliest <= "090000":
                break
            h, m = int(earliest[:2]), int(earliest[2:4]) - 1
            if m < 0:
                h, m = h - 1, 59
            hour = f"{h:02d}{m:02d}00"
        return raws, [rows[k] for k in sorted(rows)]

"""자료팀 수집 도구 단위 테스트 — 가짜 KIS 응답으로 네트워크 없이 검증."""

import csv
import gzip
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE)]

from stockking_data import minute_job, s02_inputs  # noqa: E402
from stockking_data.constituents import read_constituents  # noqa: E402
from stockking_data.kis_quotes import KisQuotes  # noqa: E402
from stockking_data import store  # noqa: E402

T = date(2026, 11, 25)
HOLIDAYS = {date(2026, 11, 20)}


def trading_days(end: date, start: date):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


class FakeClient:
    """KIS 응답 흉내: 일봉 100건, 지수 100건, 분봉 120건 페이지."""

    def __init__(self, listing=None, halted=(), cheap=(), rate_limit_once=False):
        self.listing = listing or {}
        self.halted, self.cheap = set(halted), set(cheap)
        self.rate_limit_once = rate_limit_once

    def _headers(self, tr):
        return {"tr_id": tr}

    def _call(self, method, path, headers, p):
        tr = headers["tr_id"]
        if self.rate_limit_once:
            self.rate_limit_once = False
            return {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}
        ymd = lambda s: date(int(s[:4]), int(s[4:6]), int(s[6:]))  # noqa: E731
        if tr == "FHKST03010100":
            sym = p["FID_INPUT_ISCD"]
            start = max(ymd(p["FID_INPUT_DATE_1"]), self.listing.get(sym, date(2000, 1, 1)))
            days = trading_days(ymd(p["FID_INPUT_DATE_2"]), start)[-100:][::-1]
            px = 1500 if sym in self.cheap else 50000
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": d.strftime("%Y%m%d"), "stck_oprc": px, "stck_hgpr": px + 500,
                                               "stck_lwpr": px - 500, "stck_clpr": px + 100, "acml_vol": 200000,
                                               "acml_tr_pbmn": 10_000_000_000} for d in days]}
        if tr == "FHKUP03500100":
            code = p["FID_INPUT_ISCD"]
            names = {"2001": "코스피200", "2203": "코스닥150"}
            if code not in names:
                return {"rt_cd": "1", "msg_cd": "X", "msg1": "없는 업종"}
            days = trading_days(ymd(p["FID_INPUT_DATE_2"]), ymd(p["FID_INPUT_DATE_1"]))[-100:][::-1]
            return {"rt_cd": "0", "output1": {"hts_kor_isnm": names[code]},
                    "output2": [{"stck_bsop_date": d.strftime("%Y%m%d"), "bstp_nmix_oprc": "400.1", "bstp_nmix_hgpr": "401",
                                 "bstp_nmix_lwpr": "399", "bstp_nmix_prpr": "400.5"} for d in days]}
        if tr == "FHKST01010100":
            sym = p["FID_INPUT_ISCD"]
            return {"rt_cd": "0", "output": {"stck_prpr": "1600" if sym in self.cheap else "50100",
                                             "iscd_stat_cls_code": "58" if sym in self.halted else "55",
                                             "temp_stop_yn": "Y" if sym in self.halted else "N",
                                             "sltr_yn": "N", "short_over_yn": "N", "mrkt_warn_cls_code": "00"}}
        if tr == "FHKST03010230":
            day = ymd(p["FID_INPUT_DATE_1"])
            if day in HOLIDAYS or day.weekday() >= 5:  # 휴장일: KIS가 직전 거래일 데이터를 돌려주는 경우 흉내
                day = day - timedelta(days=1)
            hh, mm = int(p["FID_INPUT_HOUR_1"][:2]), int(p["FID_INPUT_HOUR_1"][2:4])
            end = min(hh * 60 + mm, 15 * 60 + 30)
            mins = [m for m in range(end, 9 * 60 - 1, -1)][:120]
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": day.strftime("%Y%m%d"), "stck_cntg_hour": f"{m//60:02d}{m%60:02d}00",
                                               "stck_oprc": 100, "stck_hgpr": 101, "stck_lwpr": 99, "stck_prpr": 100,
                                               "cntg_vol": 10, "acml_tr_pbmn": 1000} for m in mins]}
        raise AssertionError(tr)


def test_daily_bars_paging_and_rate_limit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    q = KisQuotes(FakeClient(rate_limit_once=True))
    bars = q.daily_bars("005930", T - timedelta(days=520), T)
    dates = [b["date"] for b in bars]
    assert dates == sorted(set(dates)) and dates[-1] == T.isoformat()
    assert len(bars) == len(trading_days(T, T - timedelta(days=520)))
    assert bars[0]["value"] == 10_000_000_000


def test_minute_bars_full_day():
    q = KisQuotes(FakeClient())
    _, rows = q.minute_bars("005930", T)
    assert rows[0]["time"] == "09:00:00" and rows[-1]["time"] == "15:30:00"
    assert len(rows) == 391 and len({r["time"] for r in rows}) == 391


def test_minute_holiday_returns_nothing():
    _, rows = KisQuotes(FakeClient()).minute_bars("005930", date(2026, 11, 20))
    assert rows == []


def test_collect_day_resume_and_holiday(tmp_path):
    q = KisQuotes(FakeClient())
    syms = ["005930", "000660", "035420"]
    r = minute_job.collect_day(q, tmp_path, T, syms)
    assert r["saved"] == 3
    calls = q.calls
    r2 = minute_job.collect_day(q, tmp_path, T, syms)
    assert q.calls == calls and r2["skipped_done"] == 3        # 재실행 시 건너뜀
    with gzip.open(tmp_path / "clean/minute_kr/date=2026-11-25/005930.csv.gz", "rt") as fh:
        assert len(list(csv.DictReader(fh))) == 391
    h = minute_job.collect_day(q, tmp_path, date(2026, 11, 20), syms)
    assert h["status"] == "holiday"


def test_backfill_oldest_first(tmp_path):
    q = KisQuotes(FakeClient())
    res = minute_job.backfill(q, tmp_path, ["005930"], T, days=5, max_days=2, log=lambda *_: None)
    assert [r["date"] for r in res] == ["2026-11-19", "2026-11-20"]


def test_s02_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(s02_inputs, "date", type("D", (date,), {"today": staticmethod(lambda: T)}))
    fc = FakeClient(listing={"111110": T - timedelta(days=100)}, halted={"222220"}, cheap={"333330"})
    q = KisQuotes(fc)
    m = s02_inputs.build(q, T, ["005930", "111110", "005935", "444440"], ["222220", "333330"], tmp_path,
                         log=lambda *_: None, names={"444440": "하나26호스팩"})
    uni = (tmp_path / "universe.txt").read_text().split()
    assert uni == ["005930"]
    rep = {r["symbol"]: r["reasons"] for r in csv.DictReader((tmp_path / "universe_report.csv").open())}
    assert "U3" in rep["111110"] and "U7" in rep["222220"] and "U4" in rep["333330"] and "U2" in rep["005935"]
    idx = list(csv.DictReader((tmp_path / "index.csv").open()))
    assert {r["symbol"] for r in idx} == {"KOSPI200", "KOSDAQ150"}
    assert sum(r["symbol"] == "KOSPI200" for r in idx) >= 200
    bars = list(csv.DictReader((tmp_path / "bars.csv").open()))
    assert sum(b["symbol"] == "005930" for b in bars) >= 300
    assert list(bars[0]) == ["date", "symbol", "open", "high", "low", "close", "volume", "value"]
    mk = {r["symbol"]: r["market"] for r in csv.DictReader((tmp_path / "markets.csv").open())}
    assert mk["005930"] == "KOSPI" and mk["222220"] == "KOSDAQ"
    assert m["index_rows"]["kosdaq150_code"] == "2203"
    assert "U2" in rep["444440"]
    ex = m["excluded_by_rule"]
    assert ex["U2"] == 2 and ex["U3"] == 1 and ex["U4"] == 1 and ex["U7"] >= 1


def test_bars_load_with_program_team_loader(tmp_path, monkeypatch):
    """프로그램팀 로더(src/stockking/data/bars.py)로 그대로 읽히는지."""
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from stockking.data.bars import load_bars_csv
    monkeypatch.setattr(s02_inputs, "date", type("D", (date,), {"today": staticmethod(lambda: T)}))
    s02_inputs.build(KisQuotes(FakeClient()), T, ["005930"], ["091990"], tmp_path, log=lambda *_: None)
    assert len(load_bars_csv(tmp_path / "bars.csv")) > 600


def test_constituents_cp949(tmp_path):
    p = tmp_path / "k200.csv"
    p.write_bytes('"종목코드","종목명","종가"\n"005930","삼성전자","50000"\n"660","SK하이닉스","1"\n'.encode("cp949"))
    assert read_constituents(p) == ["000660", "005930"]
    from stockking_data.constituents import read_constituent_names
    assert read_constituent_names(p)["005930"] == "삼성전자"


def test_data_root_refuses_git_repo(monkeypatch):
    monkeypatch.setenv("STOCKKING_DATA_DIR", str(HERE / "var-data"))
    with pytest.raises(RuntimeError):
        store.data_root()


def test_tech_candidates_price_band():
    V = s02_inputs.Verdict
    a = V("100000", "KOSPI", price=30_000, adv20=9_000_000_000)
    b = V("200000", "KOSPI", price=31_000, adv20=20_000_000_000)
    c = V("300000", "KOSDAQ", price=30_500, adv20=50_000_000_000); c.fail("U7 거래정지")
    d = V("400000", "KOSDAQ", price=35_000, adv20=4_000_000_000); d.fail("U5 20일 평균 거래대금 부족")
    e = V("500000", "KOSPI", price=40_000, adv20=99_000_000_000)
    rows = s02_inputs.pick_tech_candidates([a, b, c, d, e])
    t1 = [r["symbol"] for r in rows if r["role"] == "T1"]
    t5 = [r["symbol"] for r in rows if r["role"] == "T5"]
    assert t1 == ["200000", "100000"] and t5 == ["400000"]


def test_live_quote_client_is_quote_only(monkeypatch):
    """D-041: 실전 시세 클라이언트는 허용된 시세 조회 GET과 토큰 발급 외에는 모두 거부."""
    from stockking_data.live_quotes import KisLiveQuoteClient, QuoteOnlyViolation
    sent = []

    def transport(method, url, headers, body):
        sent.append((method, url))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "tok", "expires_in": 86400}
        return {"rt_cd": "0", "output": {"stck_prpr": "50000", "iscd_stat_cls_code": "55"}}

    c = KisLiveQuoteClient("k", "s", transport=transport)
    c.min_interval_sec = 0
    st = KisQuotes(c).price_status("005930")
    assert st["price"] == 50000
    assert all(u.startswith("https://openapi.koreainvestment.com:9443") for _, u in sent)
    for bad in [("POST", "/uapi/domestic-stock/v1/trading/order-cash", "TTTC0012U"),
                ("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", "TTTC8434R"),
                ("POST", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100")]:
        with pytest.raises(QuoteOnlyViolation):
            c._call(bad[0], bad[1], {"tr_id": bad[2]}, {})
    with pytest.raises(QuoteOnlyViolation):
        c._headers("TTTC0012U")
    assert not [n for n in dir(c) if "order" in n.lower() or "cancel" in n.lower()]
    n = len(sent)
    with pytest.raises(QuoteOnlyViolation):
        c._send("POST", "/uapi/domestic-stock/v1/trading/order-cash", {}, {})
    assert len(sent) == n  # 거부된 호출은 네트워크로 나가지 않음


def test_live_quote_keys_separate_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from stockking_data import live_quotes
    import stockking.config as cfg
    monkeypatch.setenv("KIS_LIVE_QUOTE_APP_KEY", "from-env-should-be-ignored")
    f = tmp_path / "live.env"
    f.write_text("KIS_LIVE_QUOTE_APP_KEY=a\nKIS_LIVE_QUOTE_APP_SECRET=b\n")
    f.chmod(0o600)
    assert live_quotes.load_live_quote_keys(f) == ("a", "b")
    monkeypatch.setattr(cfg, "DEFAULT_SECRETS_FILE", f)
    with pytest.raises(PermissionError):
        live_quotes.load_live_quote_keys(f)
    empty = tmp_path / "none.env"
    with pytest.raises(RuntimeError):
        live_quotes.load_live_quote_keys(empty)


def test_tech_ranges_scale_with_capital():
    assert s02_inputs.tech_ranges(150_000) == {"T1": (27_000, 33_000), "T5": (33_000, 37_500)}
    assert s02_inputs.tech_ranges(1_000_000) == {"T1": (180_000, 220_000), "T5": (220_000, 250_000)}
    V = s02_inputs.Verdict
    rows = s02_inputs.pick_tech_candidates([V("100000", "KOSPI", price=200_000, adv20=9)], s02_inputs.tech_ranges(1_000_000))
    assert [r["symbol"] for r in rows if r["role"] == "T1"] == ["100000"]

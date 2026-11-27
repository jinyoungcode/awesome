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


def test_s02_inputs_status_now_and_kospi_only(tmp_path):
    """자정 이후 직전 거래일 파일: status_now=True면 상태 조회, KOSDAQ150 구성종목 없이도 생성."""
    m = s02_inputs.build(KisQuotes(FakeClient()), T, ["005930"], [], tmp_path, log=lambda *_: None, status_now=True)
    assert m["status_checked"] is True and m["universe_passed"] == 1
    assert {r["symbol"] for r in csv.DictReader((tmp_path / "index.csv").open())} == {"KOSPI200", "KOSDAQ150"}
    m2 = s02_inputs.build(KisQuotes(FakeClient()), T, ["005930"], [], tmp_path / "b", log=lambda *_: None, status_now=False)
    assert m2["universe_passed"] == 0


def test_resolve_capital_number_auto_and_default(tmp_path, monkeypatch):
    """D-046: STOCKKING_OPERATING_CAPITAL=auto 여도 오류 없이 고정 파일 값을 읽는다."""
    import json
    from stockking_data.capital import resolve_capital
    for k in ("STOCKKING_AUTO_CAPITAL_FILE", "STOCKKING_DATA_DIR", "STOCKKING_OPERATING_CAPITAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    warns = []
    assert resolve_capital("10,000,000")[0] == 10_000_000
    assert resolve_capital("auto", warn=warns.append) == (150_000, "default") and warns
    assert resolve_capital("", warn=warns.append)[0] == 150_000
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "operating_capital.json").write_text(json.dumps({"capital": 9_876_543}))
    assert resolve_capital("auto")[0] == 9_876_543
    assert resolve_capital(" AUTO ")[0] == 9_876_543
    data = tmp_path / "d"; data.mkdir()
    (data / "operating_capital.json").write_text(json.dumps({"capital": 2_000_000}))
    monkeypatch.setenv("STOCKKING_AUTO_CAPITAL_FILE", str(data / "operating_capital.json"))
    assert resolve_capital("auto")[0] == 2_000_000          # 명시 파일 우선
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "auto")
    assert resolve_capital()[0] == 2_000_000                 # 환경변수 auto
    with pytest.raises(ValueError):
        resolve_capital("abc")
    with pytest.raises(ValueError):
        resolve_capital("-5")


def test_cli_s02_inputs_accepts_auto_capital(tmp_path, monkeypatch):
    """collect.py 인자 해석 단계에서 auto 때문에 ValueError가 나지 않아야 한다 (총괄 버그 신고 11/26)."""
    import importlib.util
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "auto")
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("collect_cli", HERE / "collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = {}
    monkeypatch.setattr(mod, "_client", lambda live=False: KisQuotes(FakeClient()))
    monkeypatch.setattr(mod.s02_inputs, "build", lambda *a, **k: got.update(k) or {"ok": True})
    k2 = tmp_path / "k2.csv"; k2.write_text("종목코드,종목명\n005930,삼성전자\n", encoding="utf-8")
    assert mod.main(["s02-inputs", "--kospi200", str(k2), "--out", str(tmp_path / "out")]) == 0
    assert got["capital"] == 150_000


class FlakyClient(FakeClient):
    """간헐 HTTP 500 / EGW00201 / 장기 구간 500 흉내."""

    def __init__(self, fail_every=0, **kw):
        super().__init__(**kw)
        self.n = 0
        self.fail_every = fail_every
        self.max_span = 0

    def _call(self, method, path, headers, p):
        import io
        import urllib.error
        self.n += 1
        if "FID_INPUT_DATE_2" in p and "FID_INPUT_DATE_1" in p and len(p["FID_INPUT_DATE_1"]) == 8:
            y = lambda s: date(int(s[:4]), int(s[4:6]), int(s[6:]))  # noqa: E731
            span = (y(p["FID_INPUT_DATE_2"]) - y(p["FID_INPUT_DATE_1"])).days + 1
            self.max_span = max(self.max_span, span)
            if span > 150:
                raise urllib.error.HTTPError("u", 500, "long", {}, io.BytesIO(b"{}"))
        if self.fail_every and self.n % self.fail_every == 0:
            if self.n % (2 * self.fail_every) == 0:
                return {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}
            raise urllib.error.HTTPError("u", 500, "Internal", {}, io.BytesIO(b'{"msg_cd":"X","msg1":"err"}'))
        return super()._call(method, path, headers, p)


def test_retry_on_http_500_and_short_windows(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fc = FlakyClient(fail_every=3)
    q = KisQuotes(fc, log=lambda *_: None)
    m = s02_inputs.build(q, T, ["005930", "000660"], ["091990"], tmp_path, log=lambda *_: None, status_now=True)
    assert m["universe_passed"] == 3 and q.retries > 0
    assert fc.max_span <= 150                       # 장기 구간 조회 없음
    assert set(sleeps) <= {1, 2, 4, 8}


def test_gives_up_after_5_attempts(monkeypatch):
    import io
    import urllib.error
    monkeypatch.setattr("time.sleep", lambda s: None)

    class Dead(FakeClient):
        def _call(self, *a):
            raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b""))
    q = KisQuotes(Dead(), log=lambda *_: None)
    from stockking_data.kis_quotes import KisQuoteError
    with pytest.raises(KisQuoteError):
        q.price_status("005930")
    assert q.calls == 5


def test_non_retryable_http_error_raises_immediately(monkeypatch):
    import io
    import urllib.error

    class Forbidden(FakeClient):
        def _call(self, *a):
            raise urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b""))
    q = KisQuotes(Forbidden(), log=lambda *_: None)
    with pytest.raises(urllib.error.HTTPError):
        q.price_status("005930")
    assert q.calls == 1


def test_s02_inputs_resume_skips_cached_symbols(tmp_path):
    q = KisQuotes(FakeClient(), log=lambda *_: None)
    s02_inputs.build(q, T, ["005930", "000660"], ["091990"], tmp_path, log=lambda *_: None, status_now=True)
    first = q.calls
    q2 = KisQuotes(FakeClient(), log=lambda *_: None)
    m = s02_inputs.build(q2, T, ["005930", "000660"], ["091990"], tmp_path, log=lambda *_: None, status_now=True)
    assert q2.calls == first - 3 * 5 and m["universe_passed"] == 3   # 2차 실행은 지수만 조회 (종목 3개 × 일봉 4구간+상태 1 생략)


class Last50IndexClient(FakeClient):
    """실측 재현(11/27): KIS 지수 일봉은 요청 구간이 길어도 최근 약 50행만 돌려준다."""

    def _call(self, method, path, headers, p):
        res = super()._call(method, path, headers, p)
        if headers["tr_id"] == "FHKUP03500100" and res.get("output2"):
            res = {**res, "output2": res["output2"][:50]}   # 최신순 앞 50행
        return res


def test_index_bars_pages_back_when_only_50_rows_returned():
    q = KisQuotes(Last50IndexClient(), log=lambda *_: None)
    start = T - timedelta(days=s02_inputs.INDEX_LOOKBACK_DAYS)
    _, rows = q.index_bars("2001", start, T)
    expect = trading_days(T, start)
    assert [r["date"] for r in rows] == [d.isoformat() for d in expect]   # 빈 구간 없음
    assert len(rows) >= 280


def test_s02_inputs_refuses_short_index(tmp_path, monkeypatch):
    monkeypatch.setattr(s02_inputs, "INDEX_LOOKBACK_DAYS", 200)
    with pytest.raises(RuntimeError, match="200행"):
        s02_inputs.build(KisQuotes(FakeClient(), log=lambda *_: None), T, ["005930"], [], tmp_path,
                         log=lambda *_: None, status_now=True)
    import json
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert m["status"] == "FAILED" and "200" in m["warning"]
    assert not (tmp_path / "bars.csv").exists() and not (tmp_path / "index.csv").exists()


def test_verify_index_flags_jumps_and_compares(tmp_path):
    from stockking_data import index_check
    idx = tmp_path / "index.csv"
    rows = ["date,symbol,open,high,low,close"]
    closes = [500, 505, 560, 562, 563, 520, 521]          # +10.9%, -7.6%
    days = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]
    rows += [f"{d},KOSPI200,{c},{c},{c},{c}" for d, c in zip(days, closes)]
    idx.write_text("\n".join(rows) + "\n", encoding="utf-8")
    krx = tmp_path / "krx.csv"
    krx.write_bytes(("일자,종가,대비\n" + "\n".join(f"{d.replace('-', '/')},\"{c:,}\",0" for d, c in zip(days, closes))).encode("cp949"))
    rep = index_check.check(idx, None, None, {"KOSPI200": krx})
    k = rep["KOSPI200"]
    assert [j["date"] for j in k["jumps"]] == ["2026-11-19"] and k["krx_max_abs_diff_pct"] == 0.0


def test_verify_index_kis_single_day_refetch(tmp_path):
    from stockking_data import index_check
    q = KisQuotes(FakeClient(), log=lambda *_: None)
    _, k2 = q.index_bars("2001", T - timedelta(days=30), T)
    idx = tmp_path / "index.csv"
    idx.write_text("date,symbol,open,high,low,close\n" + "".join(f"{r['date']},KOSPI200,1,1,1,{r['close']}\n" for r in k2))
    rep = index_check.check(idx, q, "2203")
    assert rep["KOSPI200"]["kis_refetch"] and all(r["match"] for r in rep["KOSPI200"]["kis_refetch"])


def test_index_from_krx(tmp_path):
    from stockking_data.index_check import index_csv_from_krx
    hdr = "일자,종가,대비,등락률,시가,고가,저가,거래량\n"
    body = "2026/11/26,\"500.10\",1,0.2,\"499.00\",\"501.00\",\"498.00\",1\n2026/11/25,\"499.10\",1,0.2,\"498.00\",\"500.00\",\"497.00\",1\n"
    a = tmp_path / "a.csv"; a.write_bytes((hdr + body).encode("cp949"))
    b = tmp_path / "b.csv"; b.write_bytes((hdr + body).encode("cp949"))
    r = index_csv_from_krx(a, b, tmp_path / "index.csv")
    rows = list(csv.DictReader((tmp_path / "index.csv").open()))
    assert r["KOSPI200"] == 2 and rows[0]["date"] == "2026-11-25" and rows[1]["close"] == "500.1"


def test_regime_status_and_outlook():
    from stockking_data import regime
    base = date(2025, 1, 1)
    closes = [1000.0] * 150 + [700.0] * 120 + [650.0] * 9 + [700.0]     # 마지막 700, 최근 200일 중 9일은 650
    series = [((base + timedelta(days=i)).isoformat(), c) for i, c in enumerate(closes)]
    st = regime.regime_status(closes)
    assert st["on"] is False and st["sma200"] == (70 * 1000 + 121 * 700 + 9 * 650) / 200
    o = regime.outlook(series)
    flat = o["scenarios"][0]
    # 횡보 k일 후 평균 = ((70-k)*1000 + (121+k)*700 + 9*650)/200 < 700  ⇔  k > 68.5 → 69일째 ON
    assert flat["trading_days"] == 69 and flat["index_at_cross"] == 700.0
    up = o["scenarios"][3]
    assert up["trading_days"] < flat["trading_days"]
    pure_flat = regime.outlook([((base + timedelta(days=i)).isoformat(), c) for i, c in enumerate([1000.0] * 150 + [700.0] * 130)])
    assert pure_flat["scenarios"][0]["trading_days"] is None and pure_flat["scenarios"][0]["within_1pct_trading_days"] is not None
    st_on = regime.regime_status([100.0] * 199 + [90.0, 120.0])
    assert st_on["on"] and st_on["changed"]


def test_etf_tr_series_ex_date_before_record_date():
    from stockking_data import etf
    bars = [{"date": d, "symbol": "069500", "close": c} for d, c in
            [("2026-04-27", 100.0), ("2026-04-28", 100.0), ("2026-04-29", 99.0), ("2026-04-30", 99.0)]]
    tr = etf.tr_series(bars, {"2026-04-30": 1.0})   # 지급기준일 4/30 → 분배락일 4/29
    assert [r["tr_factor"] for r in tr] == [1.0, 1.0, 1.01, 1.01]
    assert tr[2]["tr_close"] == round(99.0 * 1.01, 4)


def test_etf_distributions_csv_and_proxy(tmp_path):
    from stockking_data import etf
    f = tmp_path / "d.csv"
    f.write_bytes("종목코드,종목명,지급기준일,주당분배금\n069500,KODEX 200,2026/04/30,\"1,000\"\n114260,국고채,20260131,50\n".encode("cp949"))
    d = etf.read_distributions(f)
    assert d["069500"]["2026-04-30"] == 1000.0 and d["114260"]["2026-01-31"] == 50.0
    idx = [{"date": "2015-10-05", "close": 1000.0}, {"date": "2015-10-06", "close": 1010.0}, {"date": "2015-10-07", "close": 1020.0}]
    first = {"date": "2015-10-07", "symbol": "229200", "close": 10000.0, "tr_close": 10000.0}
    p = etf.proxy_from_index(idx, first)
    assert [r["date"] for r in p] == ["2015-10-05", "2015-10-06"] and all(r["proxy"] for r in p)
    assert abs(p[1]["close"] - 10000 / (1020 / 1010) / (1 - etf.PROXY_FEE) ** (1 / 252)) < 1e-3


def test_s02_manifest_has_regime(tmp_path):
    m = s02_inputs.build(KisQuotes(FakeClient(), log=lambda *_: None), T, ["005930"], [], tmp_path,
                         log=lambda *_: None, status_now=True)
    assert set(m["regime"]) == {"KOSPI200", "KOSDAQ150"} and "on" in m["regime"]["KOSPI200"]


def test_t1_uses_integer_qty_amount_band():
    V = s02_inputs.Verdict
    r = s02_inputs.tech_ranges(10_000_000)          # 목표 200만: T1 금액 180만~220만, T5 1주 220만 초과~250만
    vs = [V("100000", "KOSPI", price=1_709_000, adv20=99_000_000_000),   # 1주 171만, 2주 342만 → 범위 밖
          V("200000", "KOSPI", price=55_000, adv20=50_000_000_000),      # 36주 = 198만 → 범위 안
          V("300000", "KOSPI", price=30_000, adv20=60_000_000_000),      # 67주 = 201만
          V("400000", "KOSDAQ", price=2_400_000, adv20=5_000_000_000),   # T5 1주 240만
          V("500000", "KOSPI", price=60_000, adv20=70_000_000_000)]
    vs[4].fail("U7 거래정지")
    rows = s02_inputs.pick_tech_candidates(vs, r)
    t1 = [x for x in rows if x["role"] == "T1"]
    assert [(x["symbol"], x["q"], x["order_amount"]) for x in t1] == [("300000", 67, 2_010_000), ("200000", 36, 1_980_000)]
    assert all(x["in_band"] for x in t1)
    t5 = [x for x in rows if x["role"] == "T5"]
    assert [(x["symbol"], x["q"], x["order_amount"]) for x in t5] == [("400000", 1, 2_400_000)]
    assert s02_inputs.tech_ranges(150_000)["T1"] == (27_000, 33_000)
    assert s02_inputs.int_qty_in_band(1_709_000, 1_800_000, 2_200_000) is None


def test_rebuild_tech_from_cache_no_kis_calls(tmp_path):
    q = KisQuotes(FakeClient(), log=lambda *_: None)
    s02_inputs.build(q, T, ["005930", "000660"], ["091990"], tmp_path, log=lambda *_: None, status_now=True)
    r = s02_inputs.rebuild_tech_from_cache(tmp_path, 10_000_000, log=lambda *_: None)
    assert r["T"] == T.isoformat()
    rows = list(csv.DictReader((tmp_path / "tech_candidates.csv").open()))
    assert rows and rows[0]["q"] and int(rows[0]["order_amount"]) == int(rows[0]["q"]) * int(rows[0]["price"])
    assert (tmp_path / "tech_candidates_prev.csv").exists()
    import json
    assert json.loads((tmp_path / "manifest.json").read_text())["tech_ranges"]["rebuilt_from_cache"] is True


def test_seibro_distribution_isin_and_utf8sig(tmp_path):
    from stockking_data import etf
    from stockking_data.index_check import read_krx_index_ohlc
    a = tmp_path / "a.csv"
    a.write_text("종목명,isin,운용사,유형,지급기준일,실지급일,배당구분,주당분배금\n"
                 "KODEX 200,KR7069500007,삼성,주식,2026/04/30,2026/05/06,현금,\"1,000\"\n"
                 "다른ETF,KR7999990001,X,주식,2026/04/30,2026/05/06,현금,5\n", encoding="utf-8-sig")
    b = tmp_path / "b.csv"
    b.write_text("종목명,isin,지급기준일,주당분배금\nKODEX 200,KR7069500007,2025/04/30,900\nKODEX 200,KR7069500007,2026/04/30,1000\n",
                 encoding="utf-8-sig")
    d = etf.read_distributions_many([a, b])
    assert d["069500"] == {"2026-04-30": 1000.0, "2025-04-30": 900.0}
    tr = tmp_path / "tr.csv"
    tr.write_text("일자,종가,대비,등락률,시가,고가,저가\n2026/11/27,1018.57,1,0.1,1010,1020,1005\n", encoding="utf-8-sig")
    assert read_krx_index_ohlc(tr)[0] == {"date": "2026-11-27", "open": 1010.0, "high": 1020.0, "low": 1005.0, "close": 1018.57}

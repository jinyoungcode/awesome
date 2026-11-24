import pytest

from stockking.broker.kis import KIS_PAPER_BASE_URL, KisPaperClient, KisPaperCredentials
from stockking.config import Secret
from stockking.domain import OrderRequest, OrderType, Side


def creds():
    return KisPaperCredentials(Secret("k"), Secret("s"), "12345678", "01")


def test_live_domain_refused():
    with pytest.raises(PermissionError):
        KisPaperClient(creds(), base_url="https://openapi.koreainvestment.com:9443")


def test_order_uses_paper_tr_id_and_domain():
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/oauth2/tokenP"):
            return {"access_token": "TOKEN", "expires_in": 86400}
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "0001"}}

    c = KisPaperClient(creds(), transport=transport)
    c.min_interval_sec = 0
    r = c.place_order(OrderRequest("005930", Side.BUY, 3, OrderType.LIMIT, 70_000))
    assert r.accepted and r.broker_order_id == "0001"
    method, url, headers, body = calls[-1]
    assert url.startswith(KIS_PAPER_BASE_URL)
    assert headers["tr_id"].startswith("V")  # 모의투자 TR 은 V 로 시작
    assert body == {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930",
                    "ORD_DVSN": "00", "ORD_QTY": "3", "ORD_UNPR": "70000"}
    # 토큰은 캐시되어 1회만 발급
    assert sum(1 for c_ in calls if c_[1].endswith("tokenP")) == 1


def test_from_env_requires_keys(monkeypatch):
    monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
    with pytest.raises(RuntimeError):
        KisPaperCredentials.from_env()

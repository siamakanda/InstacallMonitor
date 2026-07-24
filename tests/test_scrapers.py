from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings, WatchTarget
from scrapers import (
    SummaryRow,
    _do_fetch_balance,
    _do_fetch_summary,
    parse_summary_page,
    summaries_to_rows,
)

BALANCE_HTML = """<html><body>
<input name="name" value="TestCo"/>
<input name="balance" value="-150.5000"/>
<input name="credit_limit" value="600"/>
</body></html>"""

BALANCE_HTML_NO_CREDIT = """<html><body>
<input name="name" value="MinimalCo"/>
<input name="balance" value="-50.0000"/>
</body></html>"""

SUMMARY_HTML = """<html><body>
<div id="panel-cust">
<table>
<thead><tr>
<th></th><th>Customer</th><th>Calls</th><th>Connected</th><th>ASR</th>
<th>ALOC</th><th></th><th>Billed Min</th><th>Profit</th>
<th></th><th></th><th></th><th>Margin</th>
</tr></thead>
<tbody>
<tr>
<td><button class="sr-expand-btn" onclick="someJs('ct18')"></button></td>
<td><span class="sr-vol-name">TestCo</span></td>
<td><span class="rpt-num">1,500</span></td>
<td><span class="rpt-num">1,200</span></td>
<td><span class="rpt-asr-pill">80.0%</span></td>
<td><span class="rpt-num">245</span></td>
<td></td>
<td><span class="rpt-num">1,234.5</span></td>
<td><span class="rpt-num">425.30</span></td>
<td></td><td></td><td></td>
<td><span class="rpt-asr-pill">52.9%</span></td>
</tr>
<tr>
<td><button class="sr-expand-btn" onclick="foo('ct99')"></button></td>
<td><span class="sr-vol-name">OtherCo</span></td>
<td><span class="rpt-num">500</span></td>
<td><span class="rpt-num">400</span></td>
<td><span class="rpt-asr-pill">80.0%</span></td>
<td><span class="rpt-num">120</span></td>
<td></td>
<td><span class="rpt-num">500</span></td>
<td><span class="rpt-num">100.00</span></td>
<td></td><td></td><td></td>
<td><span class="rpt-asr-pill">28.3%</span></td>
</tr>
</tbody></table>
</div>
</body></html>"""

SUMMARY_HTML_NO_THEAD = """<html><body>
<div id="panel-cust">
<table><tbody>
<tr>
<td><button class="sr-expand-btn" onclick="someJs('ct42')"></button></td>
<td><span class="sr-vol-name">NoHeadCo</span></td>
<td></td><td></td><td></td><td></td><td></td>
<td><span class="rpt-num">800</span></td>
<td></td><td></td><td></td><td></td>
<td><span class="rpt-asr-pill">45.0%</span></td>
</tr>
</tbody></table>
</div>
</body></html>"""


def _make_session(mock_resp):
    session = MagicMock()
    session.get.return_value = mock_resp
    return session


class TestScrapers:
    def test_fetch_balance_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/customers?edit=18"
        mock_resp.status_code = 200
        mock_resp.text = BALANCE_HTML

        session = _make_session(mock_resp)
        name, balance, credit, error = _do_fetch_balance(session, "18", 10)
        assert name == "TestCo"
        assert balance == -150.5
        assert credit == 600.0
        assert error is None

    def test_fetch_balance_no_credit(self) -> None:
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/customers?edit=18"
        mock_resp.status_code = 200
        mock_resp.text = BALANCE_HTML_NO_CREDIT

        session = _make_session(mock_resp)
        name, balance, credit, error = _do_fetch_balance(session, "18", 10)
        assert name == "MinimalCo"
        assert balance == -50.0
        assert credit is None
        assert error is None

    def test_fetch_balance_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/customers?edit=18"
        mock_resp.status_code = 404

        session = _make_session(mock_resp)
        name, balance, credit, error = _do_fetch_balance(session, "18", 10)
        assert balance is None
        assert "HTTP 404" in (error or "")

    def test_fetch_summary_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/summary_report"
        mock_resp.status_code = 200
        mock_resp.text = SUMMARY_HTML

        session = _make_session(mock_resp)
        s = Settings(watch=[WatchTarget(customer="18", balance_below=-365.0)])
        results, error = _do_fetch_summary(session, s)

        assert error is None
        assert "18" in results
        assert results["18"]["name"] == "TestCo"
        assert results["18"]["margin"] == 52.9
        assert results["18"]["billed_min"] == 1234.5
        assert results["18"]["calls"] == 1500.0
        assert results["18"]["connected"] == 1200.0
        assert results["18"]["asr"] == 80.0
        assert results["18"]["aloc"] == 245.0
        assert results["18"]["profit"] == 425.30
        assert "99" in results
        assert results["99"]["margin"] == 28.3
        assert results["99"]["billed_min"] == 500.0
        assert results["99"]["calls"] == 500.0
        assert results["99"]["profit"] == 100.0

    def test_fetch_summary_no_panel(self) -> None:
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/summary_report"
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><div id='wrong'></div></body></html>"

        session = _make_session(mock_resp)
        s = Settings(watch=[WatchTarget(customer="18", balance_below=-365.0)])
        results, error = _do_fetch_summary(session, s)

        assert error is not None

    def test_parse_summary_with_thead_detection(self) -> None:
        results = parse_summary_page(SUMMARY_HTML)
        assert results is not None
        assert "18" in results
        assert results["18"]["calls"] == 1500.0
        assert results["18"]["connected"] == 1200.0
        assert results["18"]["asr"] == 80.0
        assert results["18"]["aloc"] == 245.0
        assert results["18"]["billed_min"] == 1234.5
        assert results["18"]["profit"] == 425.30
        assert results["18"]["margin"] == 52.9
        assert results["18"]["name"] == "TestCo"

    def test_parse_summary_no_thead_fallback(self) -> None:
        results = parse_summary_page(SUMMARY_HTML_NO_THEAD)
        assert results is not None
        assert "42" in results
        assert results["42"]["name"] == "NoHeadCo"
        assert results["42"]["billed_min"] == 800.0
        assert results["42"]["margin"] == 45.0

    def test_summaries_to_rows_conversion(self) -> None:
        results = parse_summary_page(SUMMARY_HTML)
        assert results is not None
        rows = summaries_to_rows(results)
        assert len(rows) == 2
        row18 = [r for r in rows if r.customer_id == "18"][0]
        assert row18.calls == 1500.0
        assert row18.connected == 1200.0
        assert row18.asr == 80.0
        assert row18.aloc == 245.0
        assert row18.billed_min == 1234.5
        assert row18.profit == 425.30
        assert row18.margin == 52.9
        assert row18.balance is None

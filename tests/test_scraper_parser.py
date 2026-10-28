from __future__ import annotations

from scraper_parser import parse_customer_page, parse_summary_page


class TestParseCustomerPage:
    def test_full_page(self) -> None:
        html = (
            "<html><body>"
            "<input name='name' value='Acme'/>"
            "<input name='balance' value='-150.5'/>"
            "<input name='credit_limit' value='600'/>"
            "</body></html>"
        )
        name, balance, credit, error = parse_customer_page(html)
        assert name == "Acme"
        assert balance == -150.5
        assert credit == 600.0
        assert error is None

    def test_missing_balance_field(self) -> None:
        name, balance, credit, error = parse_customer_page(
            "<html><body><input name='name' value='Acme'/></body></html>"
        )
        assert balance is None
        assert "balance field not found" in (error or "")

    def test_non_numeric_balance(self) -> None:
        name, balance, credit, error = parse_customer_page(
            "<html><body><input name='balance' value='abc'/></body></html>"
        )
        assert balance is None
        assert "non-numeric balance" in (error or "")

    def test_credit_limit_optional(self) -> None:
        html = "<html><body><input name='name' value='Acme'/><input name='balance' value='-1.5'/></body></html>"
        name, balance, credit, error = parse_customer_page(html)
        assert balance == -1.5
        assert credit is None
        assert error is None


class TestParseSummaryPage:
    def test_no_panel_returns_none(self) -> None:
        assert parse_summary_page("<html><body></body></html>") is None

    def test_no_tbody_returns_none(self) -> None:
        assert parse_summary_page("<html><body><div id='panel-cust'></div></body></html>") is None


def _summary_row(name: str, billed: str, margin: str, cid: str = "18") -> str:
    return (
        "<tr>"
        f"<td><button class='sr-expand-btn' onclick=\"x('ct{cid}')\"></button></td>"
        f"<td><span class='sr-vol-name'>{name}</span></td>"
        "<td></td><td></td><td></td><td></td><td></td>"
        f"<td><span class='rpt-num'>{billed}</span></td>"
        "<td></td><td></td><td></td><td></td>"
        f"<td><span class='rpt-asr-pill'>{margin}</span></td>"
        "</tr>"
    )


def _summary_html(*rows: str) -> str:
    return "<html><body><div id='panel-cust'><table><tbody>" + "".join(rows) + "</tbody></table></div></body></html>"


class TestParserHardening:
    def test_number_forms_with_symbols(self) -> None:
        html = (
            "<html><body>"
            "<input name='name' value='Acme'/>"
            "<input name='balance' value='$1,234.50'/>"
            "<input name='credit_limit' value='600'/>"
            "</body></html>"
        )
        _, balance, credit, error = parse_customer_page(html)
        assert balance == 1234.5
        assert credit == 600.0
        assert error is None

    def test_negative_credit_ignored(self) -> None:
        html = (
            "<html><body>"
            "<input name='name' value='Acme'/>"
            "<input name='balance' value='-1'/>"
            "<input name='credit_limit' value='-5'/>"
            "</body></html>"
        )
        _, _, credit, error = parse_customer_page(html)
        assert credit is None
        assert error is None

    def test_summary_margin_out_of_range_dropped(self) -> None:
        results = parse_summary_page(_summary_html(_summary_row("Acme", "100", "150%")))
        assert results is not None
        assert results["18"]["margin"] is None
        assert results["18"]["billed_min"] == 100.0

    def test_summary_duplicate_customer_keeps_first(self) -> None:
        results = parse_summary_page(
            _summary_html(
                _summary_row("First", "100", "50%"),
                _summary_row("Second", "200", "60%"),
            )
        )
        assert results is not None
        assert results["18"]["name"] == "First"
        assert results["18"]["billed_min"] == 100.0

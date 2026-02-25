from bot.handlers.expenses import _fmt, _parse_amount


class TestParseAmount:
    def test_integer(self):
        assert _parse_amount("100") == 100.0

    def test_comma_decimal_separator(self):
        assert _parse_amount("1,5") == 1.5

    def test_strips_whitespace(self):
        assert _parse_amount("  42  ") == 42.0

    def test_non_numeric_returns_none(self):
        assert _parse_amount("abc") is None

    def test_empty_string_returns_none(self):
        assert _parse_amount("") is None

    def test_negative(self):
        # negative values parse successfully; validation is the handler's job
        assert _parse_amount("-5") == -5.0


class TestFmt:
    def test_thousands_separator(self):
        assert _fmt(1000.0) == "1 000"

    def test_decimal(self):
        # Python :,.2f uses comma as thousands sep and dot as decimal sep,
        # then .replace(",", " ") converts thousands comma to space
        assert _fmt(1234.56) == "1 234.56"

    def test_zero(self):
        assert _fmt(0.0) == "0"

    def test_small_decimal(self):
        assert _fmt(9.99) == "9.99"

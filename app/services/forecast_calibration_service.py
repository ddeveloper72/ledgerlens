from decimal import Decimal

from app.services.money import parse_money

MATCH_STATUSES = (
    "matched", "under_forecast", "over_forecast", "partially_observed",
    "not_observed", "not_yet_due", "excluded", "insufficient_data",
)


def compare_forecast_actual(forecast_amount, actual_amount, forecast_date, actual_date=None,
                            coverage="complete", excluded=False, as_of=None):
    """Compare one forecast with observed evidence using Decimal throughout."""
    forecast = parse_money(str(forecast_amount), allow_negative=True)
    if excluded:
        return _result(forecast, None, forecast_date, actual_date, "excluded")
    if as_of and forecast_date > as_of:
        return _result(forecast, None, forecast_date, actual_date, "not_yet_due")
    if actual_amount is None:
        status = "partially_observed" if coverage == "partial" else "insufficient_data" if coverage == "unknown" else "not_observed"
        return _result(forecast, None, forecast_date, actual_date, status)
    actual = parse_money(str(actual_amount), allow_negative=True)
    variance = (actual - forecast).quantize(Decimal("0.01"))
    if coverage == "partial":
        status = "partially_observed"
    elif variance == 0:
        status = "matched"
    elif variance > 0:
        status = "over_forecast"
    else:
        status = "under_forecast"
    return _result(forecast, actual, forecast_date, actual_date, status)


def _result(forecast, actual, forecast_date, actual_date, status):
    variance = None if actual is None else (actual - forecast).quantize(Decimal("0.01"))
    percentage = None
    if variance is not None and forecast != 0:
        percentage = (variance * Decimal("100") / abs(forecast)).quantize(Decimal("0.01"))
    return {
        "forecast_amount": forecast, "actual_amount": actual,
        "variance_amount": variance, "variance_percentage": percentage,
        "forecast_date": forecast_date, "actual_date": actual_date,
        "date_variance": (actual_date - forecast_date).days if actual_date else None,
        "match_status": status,
    }


def accuracy_summary(comparisons):
    observed = [row for row in comparisons if row["actual_amount"] is not None and row["match_status"] not in {"excluded", "partially_observed"}]
    forecast_total = sum((row["forecast_amount"] for row in comparisons if row["match_status"] != "excluded"), Decimal("0.00"))
    actual_total = sum((row["actual_amount"] for row in observed), Decimal("0.00"))
    return {"forecast_total": forecast_total, "actual_total": actual_total,
            "overall_variance": (actual_total - forecast_total).quantize(Decimal("0.01")),
            "observed_count": len(observed), "comparison_count": len(comparisons)}

from datetime import date
from decimal import Decimal

from app.models import Account, ContributionReconciliation, HouseholdSpendingSummary, Transaction


def calculate_forecast_confidence(session, *, account_ids=None, selected_date=None,
                                  estimated_event_count=0, allocation_reasons=()):
    """Return an explainable coverage indicator, not a probability."""
    selected_date = selected_date or date.today()
    accounts = session.query(Account).filter(Account.id.in_(account_ids)).all() if account_ids else session.query(Account).all()
    reasons, actions, components = [], [], {}
    if not accounts:
        return {"level": "insufficient", "coverage_percentage": Decimal("0.00"), "components": {},
                "reasons": ["No household accounts are configured."], "recommended_actions": ["Add a household account or import a statement."]}
    latest_dates = []
    for account in accounts:
        row = session.query(Transaction.posted_date).filter_by(account_id=account.id, excluded_from_analysis=False).order_by(Transaction.posted_date.desc()).first()
        latest_dates.append(row[0] if row else account.balance_as_of)
    freshness = Decimal("100") if latest_dates and all(value and (selected_date - value).days <= 7 for value in latest_dates) else Decimal("60") if any(latest_dates) else Decimal("0")
    components["account_balance_freshness"] = freshness
    if freshness < 100:
        reasons.append("One or more household account sources are stale or missing."); actions.append("Import or confirm recent household account information.")
    query = session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False))
    if account_ids: query = query.filter(Transaction.account_id.in_(account_ids))
    rows = query.all()
    components["transaction_review"] = Decimal(sum(row.review_state == "reviewed" for row in rows) * 100) / Decimal(len(rows)) if rows else Decimal("0")
    components["transaction_categorisation"] = Decimal(sum(row.category_id is not None for row in rows) * 100) / Decimal(len(rows)) if rows else Decimal("0")
    reconciliations = session.query(ContributionReconciliation).all()
    components["contribution_reconciliation"] = Decimal(sum(row.status in {"matched", "cancelled", "skipped"} for row in reconciliations) * 100) / Decimal(len(reconciliations)) if reconciliations else Decimal("50")
    summaries = session.query(HouseholdSpendingSummary).all()
    components["household_spending_coverage"] = Decimal("100") if summaries else Decimal("50") if estimated_event_count else Decimal("70")
    components["forecast_observation"] = max(Decimal("20"), Decimal("100") - Decimal(estimated_event_count * 10))
    if estimated_event_count:
        reasons.append("Some household spending is estimated rather than observed."); actions.append("Add a household category summary for non-visible spending.")
    reasons.extend(allocation_reasons)
    weights = {"account_balance_freshness": Decimal("0.30"), "transaction_review": Decimal("0.20"),
               "transaction_categorisation": Decimal("0.15"), "contribution_reconciliation": Decimal("0.15"),
               "household_spending_coverage": Decimal("0.10"), "forecast_observation": Decimal("0.10")}
    coverage = sum((components[key] * weight for key, weight in weights.items()), Decimal("0")).quantize(Decimal("0.01"))
    level = "high" if coverage >= 85 else "moderate" if coverage >= 65 else "low" if coverage >= 40 else "insufficient"
    if components["transaction_review"] < 80: reasons.append("Some forecast-relevant transactions still need review."); actions.append("Review pending household transactions.")
    component_scores = {key: value.quantize(Decimal("0.01")) for key, value in components.items()}
    actions = list(dict.fromkeys(actions))
    return {"level": level, "coverage_percentage": coverage, "estimated_coverage": coverage,
            "components": component_scores, "component_scores": component_scores,
            "reasons": list(dict.fromkeys(reasons)), "recommended_actions": actions, "recommended_data_actions": actions,
            "reviewed_percent": components["transaction_review"].quantize(Decimal("0.01")),
            "categorised_percent": components["transaction_categorisation"].quantize(Decimal("0.01"))}

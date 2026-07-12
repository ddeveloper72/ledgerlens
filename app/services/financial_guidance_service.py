from datetime import timedelta
from decimal import Decimal


def generate_financial_guidance(snapshot):
    """Return deterministic planning guidance linked to forecast evidence."""
    items = []
    minimum = Decimal(snapshot["minimum_projected_balance"])
    buffer_value = Decimal(snapshot["safety_buffer"])
    if minimum < 0:
        items.append({"severity": "urgent", "title": "Protect the projected cash position", "explanation": "The forecast falls below zero before the next expected income.", "evidence": f"Minimum projected balance {minimum:.2f} on {snapshot['minimum_balance_date']}.", "start_date": snapshot["selected_date"], "end_date": snapshot["next_income_date"], "action": "Review optional spending and reserve funds for essential commitments.", "kind": "urgent"})
    elif minimum < buffer_value:
        items.append({"severity": "warning", "title": "Preserve the household safety buffer", "explanation": "Available cash is forecast to fall below the configured planning threshold.", "evidence": f"Minimum {minimum:.2f}; configured buffer {buffer_value:.2f}.", "start_date": snapshot["selected_date"], "end_date": snapshot["next_income_date"], "action": "Reduce discretionary spending until the next income date.", "kind": "informational"})
    if snapshot["overdue_commitments"]:
        items.append({"severity": "warning", "title": "Review unmatched expected payments", "explanation": "One or more expected payments are overdue without a reviewed match.", "evidence": f"{len(snapshot['overdue_commitments'])} overdue occurrence(s).", "start_date": snapshot["selected_date"], "end_date": snapshot["selected_date"] + timedelta(days=5), "action": "Review proposed transaction matches or mark the occurrence skipped.", "kind": "informational"})
    if snapshot["data_confidence"]["level"] in {"low", "insufficient"}:
        items.append({"severity": "warning", "title": "Update incomplete financial data", "explanation": "The forecast has limited supporting data and should not be treated as definitive.", "evidence": "; ".join(snapshot["data_confidence"]["reasons"]), "start_date": snapshot["selected_date"], "end_date": snapshot["selected_date"], "action": "Import recent statements and review pending transactions.", "kind": "informational"})
    return items

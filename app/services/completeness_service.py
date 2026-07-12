from datetime import date, timedelta

from app.models import Account, Category, Transaction
from app.services.period_service import apply_transaction_period


def data_completeness_report(session, period, today=None, recent_days=45):
    """Describe analysis coverage and expose visible reasons results may be incomplete."""
    today = today or date.today()
    account_rows = []
    stale_cutoff = today - timedelta(days=recent_days)
    missing_ranges = []
    for account in session.query(Account).order_by(Account.name).all():
        dates = [row[0] for row in session.query(Transaction.posted_date).filter_by(account_id=account.id, excluded_from_analysis=False).order_by(Transaction.posted_date).all()]
        earliest = dates[0] if dates else None
        latest = dates[-1] if dates else None
        gaps = []
        for left, right in zip(dates, dates[1:]):
            if (right - left).days > recent_days:
                gap = {"account": account, "start_date": left + timedelta(days=1), "end_date": right - timedelta(days=1)}
                gaps.append(gap)
                missing_ranges.append(gap)
        account_rows.append({"account": account, "earliest_date": earliest, "latest_date": latest, "stale": latest is None or latest < stale_cutoff, "missing_ranges": gaps})
    eligible = apply_transaction_period(session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(False), Transaction.internal_transfer.is_(False)), period, Transaction)
    unreviewed = eligible.filter(Transaction.review_state == "pending").count()
    uncategorized = eligible.outerjoin(Category).filter((Transaction.category_id.is_(None)) | (Category.name == "Uncategorized")).count()
    excluded = apply_transaction_period(session.query(Transaction).filter(Transaction.excluded_from_analysis.is_(True)), period, Transaction).count()
    first_row = session.query(Transaction.posted_date).filter(Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date.asc()).first()
    last_row = session.query(Transaction.posted_date).filter(Transaction.excluded_from_analysis.is_(False)).order_by(Transaction.posted_date.desc()).first()
    represented_start = first_row[0] if first_row else None
    represented_end = last_row[0] if last_row else None
    warnings = []
    if unreviewed: warnings.append(f"{unreviewed} transaction(s) still require review.")
    if uncategorized: warnings.append(f"{uncategorized} transaction(s) are uncategorized.")
    if any(row["stale"] for row in account_rows): warnings.append("One or more accounts have no recent import.")
    if missing_ranges: warnings.append("One or more accounts contain a possible gap between imported dates.")
    if represented_start is None or represented_start > period.start_date or represented_end < period.end_date: warnings.append("The selected reporting period is not fully represented by imported data.")
    return {"accounts": account_rows, "unreviewed": unreviewed, "uncategorized": uncategorized, "excluded": excluded, "missing_ranges": missing_ranges, "represented_start": represented_start, "represented_end": represented_end, "warnings": warnings, "complete": not warnings, "household_reports_partial": bool(warnings)}

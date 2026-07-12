from decimal import Decimal, ROUND_CEILING

from app.models import SavingsGoal, SavingsRecoveryEvent

EVENT_TYPES = ("withdrawal", "repayment", "adjustment")
REASON_CATEGORIES = ("vehicle", "household repair", "annual charge", "medical", "income timing", "other")


def add_recovery_event(session, goal, *, event_date, amount, event_type, reason, note=None):
    """Append an immutable recovery event after validating its type."""
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported savings event type.")
    reason = reason.strip().lower()
    if reason not in REASON_CATEGORIES:
        raise ValueError("Select a supported savings reason category.")
    event = SavingsRecoveryEvent(
        savings_goal_id=goal.id, event_date=event_date, amount=amount,
        event_type=event_type, reason=reason.strip(), note=(note or "").strip() or None,
    )
    session.add(event)
    session.flush()
    return event


def savings_recovery_summary(session):
    """Calculate current recovery position from the goal baseline plus event history."""
    goal = session.query(SavingsGoal).filter(SavingsGoal.name.ilike("%emergency%")).order_by(SavingsGoal.id).first()
    if not goal:
        return None
    withdrawals = repayments = adjustments = Decimal("0")
    events = session.query(SavingsRecoveryEvent).filter_by(savings_goal_id=goal.id).order_by(SavingsRecoveryEvent.event_date.desc(), SavingsRecoveryEvent.id.desc()).all()
    for event in events:
        amount = Decimal(event.amount)
        if event.event_type == "withdrawal": withdrawals += amount
        elif event.event_type == "repayment": repayments += amount
        else: adjustments += amount
    original_target = Decimal(goal.target_amount)
    current = Decimal(goal.current_amount) - withdrawals + repayments + adjustments
    gap = max(original_target - current, Decimal("0"))
    progress = ((current / original_target) * 100).quantize(Decimal("0.01")) if original_target > 0 else Decimal("0")
    per_payday = Decimal(goal.repayment_per_payday) if goal.repayment_per_payday else None
    paydays = int((gap / per_payday).to_integral_value(rounding=ROUND_CEILING)) if per_payday and per_payday > 0 else None
    return {
        "goal": goal, "goal_name": goal.name, "original_target": original_target,
        "target_amount": original_target, "current_amount": current, "total_withdrawals": withdrawals,
        "total_repaid": repayments, "gap": gap, "progress_percent": progress,
        "target_date": goal.target_date, "repayment_per_payday": per_payday,
        "estimated_paydays": paydays, "events": events,
    }

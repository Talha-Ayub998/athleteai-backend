# users/credit_service.py
from dataclasses import dataclass
from django.db import transaction
from users.models import Subscription, ReportPurchase
from users.subscription_limits import remaining_subscription_credits


class CreditCommitError(Exception):
    """Raised when credit cannot be safely consumed at commit time."""


@dataclass
class CreditTicket:
    source: str               # "one_time" | "subscription"
    purchase_id: int | None   # for one_time
    user_id: int
    units: int                # number of matches to consume (subscription), or 1 for one_time

def reserve_credit(user, units: int):
    """
    Reserve credits for `units` matches.
    Rule: use any unconsumed one-time purchase first (covers the whole report),
          otherwise require >= units subscription credits.
    Returns (ok: bool, ticket: CreditTicket|None, message: str).
    """
    if units <= 0:
        return False, None, "Invalid match count."

    # 1) one-time purchase first
    purchase = ReportPurchase.objects.filter(user=user, consumed=False).order_by("id").first()
    if purchase:
        return True, CreditTicket(source="one_time", purchase_id=purchase.id, user_id=user.id, units=1), \
               "using one-time credit"

    # 2) subscription allowance
    sub, _ = Subscription.objects.get_or_create(user=user)
    remaining = remaining_subscription_credits(sub)
    if remaining >= units:
        return True, CreditTicket(source="subscription", purchase_id=None, user_id=user.id, units=units), \
               f"using subscription credits ({units})"

    return False, None, (
        f"Your upload requires {units} match credits, but you only have {remaining} left. "
        f"Please upgrade your plan or purchase a one-time report."
    )

@transaction.atomic
def commit_credit(ticket: CreditTicket):
    """Consume the reserved credit after successful report creation."""
    if ticket.source == "one_time":
        rp = ReportPurchase.objects.select_for_update().get(id=ticket.purchase_id)
        if rp.consumed:
            raise CreditCommitError("One-time credit is no longer available.")

        from django.utils import timezone
        rp.consumed = True
        rp.consumed_at = timezone.now()
        rp.save(update_fields=["consumed", "consumed_at"])
        return

    # Subscription usage: enforce availability at commit time under row lock.
    sub = Subscription.objects.select_for_update().get(user_id=ticket.user_id)
    units = int(ticket.units)
    if units <= 0:
        raise CreditCommitError("Invalid credit usage.")

    remaining = remaining_subscription_credits(sub)
    if remaining < units:
        raise CreditCommitError("Not enough subscription credits remaining.")

    sub.period_usage = (sub.period_usage or 0) + units
    sub.save(update_fields=["period_usage"])

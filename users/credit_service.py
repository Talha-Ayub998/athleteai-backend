# users/credit_service.py
from dataclasses import dataclass
from django.db import transaction
from users.models import Subscription, ReportPurchase
from users.subscription_limits import remaining_subscription_credits

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

    return False, None, f"Not enough subscription credits. Need {units}, have {remaining}. " \
                        f"Buy a one-time PDF or upgrade your plan."

@transaction.atomic
def commit_credit(ticket: CreditTicket):
    """Consume the reserved credit after successful report creation."""
    if ticket.source == "one_time":
        rp = ReportPurchase.objects.select_for_update().get(id=ticket.purchase_id)
        if not rp.consumed:
            from django.utils import timezone
            rp.consumed = True
            rp.consumed_at = timezone.now()
            rp.save(update_fields=["consumed", "consumed_at"])
        return

    # subscription usage (consume `units`)
    from users.models import Subscription
    sub = Subscription.objects.select_for_update().get(user_id=ticket.user_id)
    sub.period_usage = (sub.period_usage or 0) + int(ticket.units)
    sub.save(update_fields=["period_usage"])

# users/subscription_limits.py
from datetime import timedelta
from django.utils import timezone

# --------- Policy ----------
FREE_TRIAL_DAYS = 14
LIMITS = {
    "free":       {"monthly": 0,  "trial_once": 1},  # 1 total match during trial
    "essentials": {"monthly": 6},                    # matches per rolling month
    "precision":  {"monthly": 12},                   # matches per rolling month
}


# --------- Window helpers (ROLLING month, not calendar) ----------
def billing_window_from(start):
    """
    Given a start datetime, return (start, end) where end = start + 1 month - 1s.
    Uses relativedelta to add one month accurately.
    """
    from dateutil.relativedelta import relativedelta
    end = (start + relativedelta(months=1)) - timedelta(seconds=1)
    return start, end


def ensure_period(sub):
    """
    Ensure a rolling-month accounting window exists for the subscription and is current.
    - If no window exists: start NOW.
    - If 'now' is beyond current_period_end: roll forward by whole months until 'now' fits.
    Resets 'period_usage' whenever a new window starts.
    """
    from dateutil.relativedelta import relativedelta

    now = timezone.now()

    if not sub.current_period_start or not sub.current_period_end:
        s, e = billing_window_from(now)
        sub.current_period_start, sub.current_period_end = s, e
        sub.period_usage = 0
        sub.save(update_fields=["current_period_start", "current_period_end", "period_usage"])
        return sub

    # roll forward until now is within [start, end]
    while now > sub.current_period_end:
        new_start = sub.current_period_end + timedelta(seconds=1)
        new_end = (new_start + relativedelta(months=1)) - timedelta(seconds=1)
        sub.current_period_start, sub.current_period_end = new_start, new_end
        sub.period_usage = 0
        sub.save(update_fields=["current_period_start", "current_period_end", "period_usage"])

    return sub


# --------- Credit math ----------
def remaining_subscription_credits(sub) -> int:
    """
    Returns remaining *subscription* credits (matches) for the current rolling window.
    For 'free' plan: during trial, allow exactly 1 match total; after trial, 0.
    """
    ensure_period(sub)
    plan = sub.plan or "free"

    if plan == "free":
        if sub.status == "trialing" and sub.trial_end and sub.trial_end >= timezone.now():
            used = sub.period_usage or 0
            return max(0, LIMITS["free"]["trial_once"] - used)
        return 0

    cap = LIMITS.get(plan, {}).get("monthly", 0)
    used = sub.period_usage or 0
    return max(0, cap - used)


def stamp_free_trial(sub):
    """
    Initialize the 14-day free trial and the first *rolling* monthly window starting NOW.
    """
    now = timezone.now()
    s, e = billing_window_from(now)
    sub.status = "trialing"
    sub.trial_start = now
    sub.trial_end = now + timedelta(days=FREE_TRIAL_DAYS)
    sub.current_period_start = s
    sub.current_period_end = e
    sub.period_usage = 0

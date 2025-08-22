import stripe

def get_price_id(lookup_key: str) -> str:
    prices = stripe.Price.list(active=True, lookup_keys=[lookup_key], limit=1)
    if not prices.data:
        raise ValueError(f"No price found for {lookup_key}")
    return prices.data[0].id

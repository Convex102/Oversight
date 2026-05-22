import stripe
from app.config import settings
stripe.api_key = settings.STRIPE_SECRET_KEY

def create_checkout_session(price_id: str, customer_email: str, success_url: str, cancel_url: str, metadata: dict):
    return stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )

def create_portal_session(customer_id: str, return_url: str):
    return stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)

def construct_event(payload: bytes, sig: str):
    return stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)

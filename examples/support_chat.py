"""A long-running customer-support chat with tool lookups.

    python examples/support_chat.py

Support sessions run for dozens of turns, and every order/ticket lookup adds tool output. The
compactor folds resolved parts of the conversation into notes (order ids, promises made,
refund amounts), so the bot stays inside a small window but still remembers earlier details.
"""

import json
import random
import sys
from pathlib import Path

from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import build_agent, repl  # noqa: E402

rng = random.Random(4)
ORDERS = {
    f"A{1000 + i}": {
        "customer": "sam@example.com", "status": rng.choice(["delivered", "in transit", "delayed", "returned"]),
        "items": [{"sku": f"SKU-{rng.randint(100, 999)}", "name": rng.choice(["kettle", "lamp", "boots", "desk"]),
                   "price_eur": rng.randint(15, 220)} for _ in range(rng.randint(1, 3))],
        "tracking": "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(12)),
        "history": [f"2026-11-{d:02d} scan at hub {rng.randint(1, 40)}" for d in range(1, rng.randint(6, 20))],
    }
    for i in range(12)
}
POLICY = ("Refunds: full refund within 30 days of delivery; delayed orders over 7 days get a 10% voucher; "
          "returns need the original packaging; shipping fees are refunded only if we caused the issue. ") * 3


@tool
def list_orders(email: str) -> str:
    """List a customer's orders with status."""
    return json.dumps({k: v["status"] for k, v in ORDERS.items() if v["customer"] == email})


@tool
def get_order(order_id: str) -> str:
    """Full order details including items, tracking and scan history."""
    return json.dumps(ORDERS.get(order_id.upper(), {"error": "no such order"}), indent=2)


@tool
def refund_policy() -> str:
    """The refund and returns policy."""
    return POLICY


agent, compaction = build_agent(
    [list_orders, get_order, refund_policy],
    "You are a friendly support agent for an online shop. Look things up before answering; never invent "
    "order data. Keep track of what you have promised the customer.",
    window=5_000,
)
repl(agent, compaction, [
    "Hi, I'm sam@example.com. Can you check all my orders and tell me which ones have problems?",
    "For the delayed ones, what am I entitled to under your policy?",
    "Earlier you looked at my orders. What was the tracking number of the first delayed one, and its items?",
])

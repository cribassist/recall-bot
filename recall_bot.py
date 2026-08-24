"""
CPSC Recall Bot
----------------
Pulls new product recalls from the official CPSC (SaferProducts.gov) REST API
and posts them to X, formatted with a category tag, a severity-based urgency
flag, and a short attention-grabbing hook (AI-generated via Claude Haiku,
with a free template fallback if that call ever fails or the key is missing).

State (which recalls have already been posted) is tracked in post_state.json,
which this script updates and the GitHub Action commits back to the repo
after every run, so the bot never double-posts even though each run starts
from a clean container.

Env vars required (set as GitHub Actions secrets):
    X_API_KEY
    X_API_SECRET
    X_ACCESS_TOKEN
    X_ACCESS_SECRET

Env var optional (enables the AI hook line; falls back to a template if unset
or if the call fails for any reason):
    ANTHROPIC_API_KEY
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
STATE_FILE = "post_state.json"

LOOKBACK_DAYS = 30
INCLUDE_LINK = False
MAX_POSTS_PER_RUN = 5

CPSC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Categorization — free, keyword-based, no API calls
# ---------------------------------------------------------------------------

CATEGORIES = [
    # (label, emoji, keywords to match against the recall's text)
    ("VEHICLE RECALL", "🚗", ["motorcycle", "vehicle", "atv", "scooter", "bicycle",
                               "e-bike", "moped", "truck", "trailer"]),
    ("BABY & KID SAFETY", "👶", ["infant", "child", "children", "baby", "toddler",
                                  "crib", "stroller", "toy", "bassinet", "car seat"]),
    ("FOOD RECALL", "🍔", ["food", "beverage", "snack", "meat", "produce"]),
    ("AMAZON RECALL", "🛒", ["amazon"]),
]

CLOSING_QUESTIONS = {
    "VEHICLE RECALL": "Could this affect your ride?",
    "BABY & KID SAFETY": "Do you have this in your home?",
    "FOOD RECALL": "Check your pantry — do you have this?",
    "AMAZON RECALL": "Bought this on Amazon? Check your orders.",
    "GENERAL": "Do you own this product?",
}

FALLBACK_HOOKS = {
    "VEHICLE RECALL": "🚨 Check your ride before you drive it.",
    "BABY & KID SAFETY": "🚨 Parents — check your home for this one.",
    "FOOD RECALL": "⚠️ Check your pantry before you eat this.",
    "AMAZON RECALL": "🚨 Bought this on Amazon? Read this.",
    "GENERAL": "🚨 This product was just recalled.",
}

# Tier A = high-urgency hazard language; anything else defaults to Tier B.
TIER_A_KEYWORDS = [
    "death", "fire", "burn", "explosion", "crash", "laceration", "impact",
    "choking", "ingestion", "battery", "brake", "steering", "electrocution",
    "shock", "carbon monoxide", "strangulation", "suffocation", "amputation",
]


def searchable_text(recall):
    parts = [recall.get("Title", "")]
    for hazard in recall.get("Hazards", []) or []:
        parts.append(hazard.get("Name", ""))
    for product in recall.get("Products", []) or []:
        parts.append(product.get("Name", ""))
    for retailer in recall.get("Retailers", []) or []:
        parts.append(retailer.get("Name", ""))
    return " ".join(parts).lower()


def categorize(recall):
    text = searchable_text(recall)
    for label, emoji, keywords in CATEGORIES:
        if any(kw in text for kw in keywords):
            return label, emoji
    return "GENERAL", "🚨"


def tier_of(recall):
    text = searchable_text(recall)
    return "A" if any(kw in text for kw in TIER_A_KEYWORDS) else "B"


# ---------------------------------------------------------------------------
# AI hook line — small Claude Haiku call, with a free fallback
# ---------------------------------------------------------------------------

def generate_hook(category_label, product, hazard):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return FALLBACK_HOOKS.get(category_label, FALLBACK_HOOKS["GENERAL"])

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 40,
                "system": (
                    "Write ONE short, punchy, scroll-stopping opening line for a "
                    "product recall alert tweet. Under 70 characters. No hashtags, "
                    "no quotation marks, no emoji beyond one at the very start. "
                    "Output only the line itself, nothing else."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Category: {category_label}\n"
                            f"Product: {product}\n"
                            f"Hazard: {hazard}"
                        ),
                    }
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        return text if text else FALLBACK_HOOKS.get(category_label, FALLBACK_HOOKS["GENERAL"])
    except Exception as e:
        print(f"Hook generation failed, using fallback: {e}", file=sys.stderr)
        return FALLBACK_HOOKS.get(category_label, FALLBACK_HOOKS["GENERAL"])


# ---------------------------------------------------------------------------
# State handling
# ---------------------------------------------------------------------------

def load_posted_ids():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    return set(data.get("posted_ids", []))


def save_posted_ids(ids):
    with open(STATE_FILE, "w") as f:
        json.dump({"posted_ids": sorted(ids)}, f, indent=2)


# ---------------------------------------------------------------------------
# CPSC fetch
# ---------------------------------------------------------------------------

def fetch_recent_recalls():
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%m/%d/%Y")
    params = {"RecallDateStart": start, "format": "json"}
    resp = requests.get(CPSC_URL, params=params, headers=CPSC_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def first_name(items, key="Name", fallback="See recall notice"):
    if isinstance(items, list) and items:
        return items[0].get(key, fallback)
    return fallback


# ---------------------------------------------------------------------------
# Tweet formatting
# ---------------------------------------------------------------------------

def truncate(text, max_len):
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def format_tweet(recall):
    category_label, category_emoji = categorize(recall)
    tier = tier_of(recall)
    tier_flag = "🚨" if tier == "A" else "⚠️"

    product = truncate(recall.get("Title", "Product Recall"), 100)
    hazard = truncate(first_name(recall.get("Hazards", [])), 60)
    remedy = truncate(first_name(recall.get("Remedies", [])), 60)
    url = recall.get("URL", "")

    hook = generate_hook(category_label, product, hazard)
    closing = CLOSING_QUESTIONS.get(category_label, CLOSING_QUESTIONS["GENERAL"])

    # Build in priority order, dropping the lowest-priority piece first if
    # we run over the character limit: closing question, then remedy line.
    header = f"{tier_flag} {category_emoji} {category_label}"

    def assemble(include_closing=True, include_remedy=True):
        lines = [header, "", hook, "", f"Product: {product}", f"Hazard: {hazard}"]
        if include_remedy:
            lines.append(f"Remedy: {remedy}")
        if INCLUDE_LINK and url:
            lines += ["", url]
        if include_closing:
            lines += ["", closing]
        return "\n".join(lines)

    text = assemble()
    if len(text) > 280:
        text = assemble(include_closing=False)
    if len(text) > 280:
        text = assemble(include_closing=False, include_remedy=False)
    if len(text) > 280:
        text = text[:277] + "..."
    return text


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def post_to_x(text, auth):
    resp = requests.post(
        "https://api.twitter.com/2/tweets",
        auth=auth,
        json={"text": text},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"X API error {resp.status_code}: {resp.text}", file=sys.stderr)
        return False
    return True


def main():
    required = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"Missing env vars: {missing}", file=sys.stderr)
        sys.exit(1)

    auth = OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_SECRET"],
    )

    posted_ids = load_posted_ids()
    recalls = fetch_recent_recalls()
    print(f"Fetched {len(recalls)} recalls from the last {LOOKBACK_DAYS} day(s).")

    new_posts = 0
    for recall in recalls:
        recall_id = str(recall.get("RecallID"))
        if not recall_id or recall_id in posted_ids:
            continue
        if new_posts >= MAX_POSTS_PER_RUN:
            print("Hit MAX_POSTS_PER_RUN cap, saving remaining recalls for next run.")
            break

        tweet_text = format_tweet(recall)
        success = post_to_x(tweet_text, auth)
        if success:
            print(f"Posted recall {recall_id}: {recall.get('Title')}")
            posted_ids.add(recall_id)
            new_posts += 1
        else:
            print(f"Failed to post recall {recall_id}, will retry next run.")

    save_posted_ids(posted_ids)
    print(f"Done. Posted {new_posts} new recall(s).")


if __name__ == "__main__":
    main()

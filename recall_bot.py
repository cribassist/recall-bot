"""
CPSC Recall Bot
----------------
Pulls new product recalls from the official CPSC (SaferProducts.gov) REST API
and posts them to X. No API key needed for CPSC — it's a fully public,
official government endpoint.

State (which recalls have already been posted) is tracked in post_state.json,
which this script updates and the GitHub Action commits back to the repo
after every run, so the bot never double-posts even though each run starts
from a clean container.

Env vars required (set as GitHub Actions secrets):
    X_API_KEY
    X_API_SECRET
    X_ACCESS_TOKEN
    X_ACCESS_SECRET
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
STATE_FILE = "post_state.json"

# How far back to look each run. The workflow runs every 6 hours, so 2 days
# gives generous overlap in case a run is skipped or delayed.
LOOKBACK_DAYS = 30

# Toggle: include the official recall URL in the tweet.
# ON  -> more useful/actionable, but costs ~$0.20/post on X's API (link pricing)
# OFF -> cheaper (~$0.015/post), text-only, still names product + hazard + remedy
INCLUDE_LINK = True

# Safety cap so a bad run can never blow the whole budget in one shot.
MAX_POSTS_PER_RUN = 5


def load_posted_ids():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    return set(data.get("posted_ids", []))


def save_posted_ids(ids):
    with open(STATE_FILE, "w") as f:
        json.dump({"posted_ids": sorted(ids)}, f, indent=2)


def fetch_recent_recalls():
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%m/%d/%Y")
    params = {"RecallDateStart": start, "format": "json"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    resp = requests.get(CPSC_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()



def first_name(items, key="Name", fallback="See recall notice"):
    """Safely grab the first item's Name field from a list the CPSC API returns."""
    if isinstance(items, list) and items:
        return items[0].get(key, fallback)
    return fallback


def format_tweet(recall):
    title = recall.get("Title", "Product Recall")
    hazard = first_name(recall.get("Hazards", []))
    remedy = first_name(recall.get("Remedies", []))
    url = recall.get("URL", "")

    body = f"⚠️ RECALL: {title}\n\nHazard: {hazard}\nRemedy: {remedy}"

    if INCLUDE_LINK and url:
        body += f"\n\n{url}"

    # Hard safety trim to X's character limit.
    if len(body) > 280:
        body = body[:277] + "..."
    return body


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

#!/usr/bin/env python3
"""
Fast daily Notion sync: update performance metrics for existing posts + add new posts.

Uses filter_properties to only fetch ~6 fields per post (not all 232),
keeping API calls minimal and fast.

Usage:
  NOTION_API_KEY=ntn_... python scripts/sync_notion.py
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

DB_ID = "1f8e4fd0-8136-8094-b03d-fffe5b42de1a"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
POSTS_JSON = os.path.join(ROOT_DIR, "data", "posts.json")
EXTRAS_JSON = os.path.join(ROOT_DIR, "data", "post_extras.json")
INDEX_HTML = os.path.join(ROOT_DIR, "index.html")

# Property IDs for filter_properties (avoids fetching all 232 props)
# These were discovered via GET /databases/{id}
PROP_IDS = {
    "title": "title",           # Post title (the "id" property)
    "post_date": "%3EG%5D%5D",  # Post date
    "post_sequence": "dvkA",    # Post Sequence
    "brief_frame": "DMQ%60",    # Brief & Frame
    "influencer": "W%5C%5C~",   # Influencer (string)
    "accounts_created": "Bdic",  # Accounts Created — will discover below
    "delegations": "oy%7BV",     # Delegations
    "status": "Z%7DGo",          # Status
}


def get_token():
    token = os.environ.get("NOTION_API_KEY")
    if token:
        return token
    settings_file = os.path.expanduser("~/.claude/settings.json")
    if os.path.exists(settings_file):
        with open(settings_file) as f:
            d = json.load(f)
        for server in d.get("mcpServers", {}).values():
            h = server.get("env", {}).get("OPENAPI_MCP_HEADERS", "")
            if h:
                hd = json.loads(h)
                auth = hd.get("Authorization", "")
                if "ntn_" in auth:
                    return auth.replace("Bearer ", "")
    raise ValueError("No NOTION_API_KEY found")


def notion_request(token, url, method="POST", body=None, retries=3):
    """Make a request to the Notion API. url should be a full URL."""
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        method=method,
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                print(f"  Retry {attempt+1}: {e}")
            else:
                raise


def discover_property_ids(token):
    """Fetch the database schema to find property IDs for key metrics fields."""
    url = f"https://api.notion.com/v1/databases/{DB_ID}"
    resp = notion_request(token, url, method="GET")
    props = resp.get("properties", {})

    # Find IDs for fields we care about
    wanted = {
        "Accounts Created": "accounts_created",
        "Delegations": "delegations",
        "Landing Page Views": "landing_page_views",
        "Status": "status",
        "Post date": "post_date",
        "Post sequence": "post_sequence",
        "Brief & Frame": "brief_frame",
        "Influencer (string)": "influencer",
    }

    discovered = {}
    for prop_name, prop_def in props.items():
        if prop_name in wanted:
            discovered[wanted[prop_name]] = prop_def.get("id", "")
        # Also find the title property
        if prop_def.get("type") == "title":
            discovered["title"] = prop_def.get("id", "")

    return discovered


def fetch_posts_fast(token, prop_ids):
    """Fetch posts using filter_properties for speed."""
    # Build filter_properties query params (double-URL-encoded for Notion)
    fp_params = []
    for key in ["title", "post_date", "post_sequence", "accounts_created",
                 "delegations", "landing_page_views", "status", "influencer", "brief_frame"]:
        pid = prop_ids.get(key)
        if pid:
            encoded = urllib.parse.quote(pid, safe="")
            fp_params.append(f"filter_properties={encoded}")

    query_string = "&".join(fp_params)
    base_url = f"https://api.notion.com/v1/databases/{DB_ID}/query?{query_string}"

    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    all_posts = []
    cursor = None

    while True:
        body = {
            "filter": {
                "property": "Post date",
                "date": {"on_or_after": cutoff},
            },
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor

        resp = notion_request(token, base_url, body=body)
        results = resp.get("results", [])
        all_posts.extend(results)
        print(f"  Fetched {len(results)} posts (total: {len(all_posts)})")

        if resp.get("has_more") and resp.get("next_cursor"):
            cursor = resp["next_cursor"]
            time.sleep(0.3)
        else:
            break

    return all_posts


def extract_text(rich_text_arr):
    if not rich_text_arr:
        return ""
    return "".join(t.get("plain_text", "") for t in rich_text_arr)


def parse_notion_post(page):
    """Extract the few fields we fetch from a Notion page."""
    props = page.get("properties", {})
    page_id = page["id"]

    # Title
    title = ""
    for v in props.values():
        if isinstance(v, dict) and v.get("type") == "title":
            title = extract_text(v.get("title", []))
            break

    # Numbers
    def num(prop_val):
        if isinstance(prop_val, dict) and prop_val.get("type") == "number":
            return prop_val.get("number")
        return None

    # Date
    def dt(prop_val):
        if isinstance(prop_val, dict) and prop_val.get("type") == "date":
            d = prop_val.get("date")
            return d.get("start", "") if d else ""
        # Handle formula dates
        if isinstance(prop_val, dict) and prop_val.get("type") == "formula":
            f = prop_val.get("formula", {})
            if f.get("type") == "date" and f.get("date"):
                return f["date"].get("start", "")
            return f.get("string", "") or ""
        return ""

    # Rich text
    def rt(prop_val):
        if isinstance(prop_val, dict) and prop_val.get("type") == "rich_text":
            return extract_text(prop_val.get("rich_text", []))
        return ""

    # Formula string
    def formula_str(prop_val):
        if isinstance(prop_val, dict) and prop_val.get("type") == "formula":
            f = prop_val.get("formula", {})
            return f.get("string", "") or ""
        return ""

    # Status
    def status(prop_val):
        if isinstance(prop_val, dict) and prop_val.get("type") == "status":
            s = prop_val.get("status")
            return s.get("name", "") if s else ""
        return ""

    # Find properties by scanning (since we used filter_properties, only our fields are present)
    accounts_created = None
    delegations = None
    landing_page_views = None
    post_date = ""
    post_sequence = None
    status_val = ""
    influencer_name = ""
    brief_frame = ""

    for prop_name, prop_val in props.items():
        if not isinstance(prop_val, dict):
            continue
        ptype = prop_val.get("type", "")

        # Match by property name (case-insensitive)
        name_lower = prop_name.lower()
        if "accounts created" in name_lower:
            accounts_created = num(prop_val)
        elif name_lower == "delegations":
            delegations = num(prop_val)
        elif "landing page" in name_lower:
            landing_page_views = num(prop_val)
        elif "post date" in name_lower:
            post_date = dt(prop_val)
        elif "post sequence" in name_lower or name_lower == "post sequence":
            post_sequence = num(prop_val)
        elif name_lower == "status":
            status_val = status(prop_val)
        elif "influencer" in name_lower:
            influencer_name = formula_str(prop_val) or rt(prop_val)
        elif "brief" in name_lower and "frame" in name_lower:
            brief_frame = rt(prop_val)

    return {
        "id": page_id,
        "title": title,
        "post_date": post_date,
        "post_sequence": post_sequence,
        "accounts_created": accounts_created,
        "delegations": delegations,
        "landing_page_views": landing_page_views,
        "status": status_val,
        "influencer_name": influencer_name,
        "brief_frame": brief_frame,
        "notion_url": f"https://www.notion.so/{page_id.replace('-', '')}",
    }


def format_date(iso_date):
    """Convert 2026-03-19 to '19 Mar 2026'."""
    if not iso_date:
        return ""
    try:
        dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
        return dt.strftime("%-d %b %Y")
    except ValueError:
        return iso_date


def make_new_post(notion):
    """Create a basic post entry for a post not in our existing data."""
    name = notion.get("influencer_name") or notion.get("title", "Unknown")
    # Clean up name from title format like "@handle - post 3"
    if " - " in name and name.startswith("@"):
        name = name.split(" - ")[0].replace("@", "").strip()

    return {
        "id": notion["id"],
        "name": name,
        "post_sequence": notion.get("post_sequence"),
        "date": format_date(notion.get("post_date", "")),
        "format": "Story",
        "niche": "",
        "notion_url": notion.get("notion_url", ""),
        "image": "",
        "instagram_url": "",
        "instagram_handle": "",
        "campaign_timeline": [],
        "brief_info": {"niche_brief": "", "pillars": []},
        "performance": {
            "accounts_created": notion.get("accounts_created"),
            "delegations": notion.get("delegations"),
            "landing_page_views": notion.get("landing_page_views"),
            "post_fee": None,
            "ecac": None,
        },
        "brief_compliance": [
            {"label": "Problem-aware hook", "pass": False},
            {"label": "Discovery moment", "pass": False},
            {"label": "What Nous does", "pass": False},
            {"label": "Savings claim", "pass": False},
            {"label": "Sign-up ease", "pass": False},
            {"label": "@get_nous tag", "pass": False},
            {"label": "Save with Nous CTA", "pass": False},
            {"label": "Calming/lifestyle visual", "pass": False},
        ],
        "review_comments": [],
        "review_reflection": {"good": [], "missed": []},
    }


def inject_into_html(posts, extras):
    """Replace const posts and const postExtras in index.html."""
    with open(INDEX_HTML, "r") as f:
        html = f.read()

    posts_js = json.dumps(posts, indent=2, ensure_ascii=False)
    extras_js = json.dumps(extras, indent=2, ensure_ascii=False)

    html = re.sub(
        r'const posts = \[[\s\S]*?\n\];',
        f'const posts = {posts_js};',
        html, count=1,
    )
    html = re.sub(
        r'const postExtras = \{[\s\S]*?\n\};',
        f'const postExtras = {extras_js};',
        html, count=1,
    )

    with open(INDEX_HTML, "w") as f:
        f.write(html)


def main():
    token = get_token()

    # Step 1: Discover property IDs from the database schema
    print("Discovering property IDs...")
    prop_ids = discover_property_ids(token)
    print(f"  Found {len(prop_ids)} property IDs")

    # Step 2: Load existing data
    existing_posts = {}
    if os.path.exists(POSTS_JSON):
        with open(POSTS_JSON) as f:
            for post in json.load(f):
                existing_posts[post["id"]] = post
        print(f"Loaded {len(existing_posts)} existing posts")

    existing_extras = {}
    if os.path.exists(EXTRAS_JSON):
        with open(EXTRAS_JSON) as f:
            existing_extras = json.load(f)

    # Step 3: Fetch posts from Notion (fast — only key fields)
    print("\nFetching posts from Notion (lightweight)...")
    raw_pages = fetch_posts_fast(token, prop_ids)
    print(f"Got {len(raw_pages)} posts from Notion")

    # Step 4: Parse and merge
    notion_data = {}
    for page in raw_pages:
        try:
            parsed = parse_notion_post(page)
            notion_data[parsed["id"]] = parsed
        except Exception as e:
            print(f"  Error parsing {page.get('id', '?')}: {e}")

    new_count = 0
    updated_count = 0

    for pid, nd in notion_data.items():
        if pid in existing_posts:
            # Update performance metrics only
            post = existing_posts[pid]
            post["performance"]["accounts_created"] = nd["accounts_created"]
            post["performance"]["delegations"] = nd["delegations"]
            post["performance"]["landing_page_views"] = nd["landing_page_views"]
            # Recompute ecac if we have post_fee and accounts
            pf = post["performance"].get("post_fee")
            ac = nd["accounts_created"]
            if pf and ac and ac > 0:
                post["performance"]["ecac"] = round(pf / ac, 1)
            # Update date if it changed
            new_date = format_date(nd.get("post_date", ""))
            if new_date:
                post["date"] = new_date
            updated_count += 1
        else:
            # New post — add basic entry
            existing_posts[pid] = make_new_post(nd)
            new_count += 1

    print(f"\n  Updated: {updated_count}")
    print(f"  New: {new_count}")
    print(f"  Total: {len(existing_posts)}")

    # Sort by date descending
    def sort_key(post):
        d = post.get("date", "")
        try:
            return datetime.strptime(d, "%d %b %Y")
        except ValueError:
            return datetime.min

    posts_list = sorted(existing_posts.values(), key=sort_key, reverse=True)

    # Save
    os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
    with open(POSTS_JSON, "w") as f:
        json.dump(posts_list, f, indent=2, ensure_ascii=False)
    print(f"\nSaved data/posts.json")

    with open(EXTRAS_JSON, "w") as f:
        json.dump(existing_extras, f, indent=2, ensure_ascii=False)
    print(f"Saved data/post_extras.json")

    # Update index.html
    print("Updating index.html...")
    inject_into_html(posts_list, existing_extras)
    print("Done!")


if __name__ == "__main__":
    main()

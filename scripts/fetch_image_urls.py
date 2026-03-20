#!/usr/bin/env python3
"""Fetch 'Post visual' image URLs from Notion for posts missing images."""

import json
import os
import time
import urllib.request
import urllib.parse

# 1. Load Notion API token
settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path) as fh:
    d = json.load(fh)
token = None
for s in d.get("mcpServers", {}).values():
    h = s.get("env", {}).get("OPENAPI_MCP_HEADERS", "")
    if h:
        hd = json.loads(h)
        auth = hd.get("Authorization", "")
        if "ntn_" in auth:
            token = auth.replace("Bearer ", "")
            break

if not token:
    raise RuntimeError("Could not find Notion API token")

print(f"Token found: {token[:10]}...")

# 2. Load posts.json and find posts needing images
posts_path = os.path.join(os.path.dirname(__file__), "..", "data", "posts.json")
with open(posts_path) as fh:
    posts = json.load(fh)

needed_ids = set()
for p in posts:
    if p.get("image") == "" and p.get("niche") not in (None, "") and p.get("post_sequence") is not None:
        needed_ids.add(p["id"])

print(f"Posts needing images: {len(needed_ids)}")

# 3. Query Notion API with pagination
DB_ID = "1f8e4fd0-8136-8094-b03d-fffe5b42de1a"
base_url = f"https://api.notion.com/v1/databases/{DB_ID}/query"

headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

body_template = {
    "filter": {
        "property": "Post date",
        "date": {
            "on_or_after": "2026-01-01"
        }
    },
    "page_size": 20
}

image_map = {}
has_more = True
start_cursor = None
page_num = 0

while has_more:
    page_num += 1
    body = dict(body_template)
    if start_cursor:
        body["start_cursor"] = start_cursor

    # Build URL with filter_properties
    url = base_url + "?filter_properties=title&filter_properties=l%2540lc"

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            print(f"  Page {page_num} attempt {attempt+1} failed: {e}")
            time.sleep(3)
    else:
        print(f"  Skipping page {page_num} after 3 failures")
        break

    results = result.get("results", [])
    print(f"Page {page_num}: {len(results)} results")

    for page in results:
        page_id = page["id"]
        # Normalize ID format (Notion returns with dashes)
        props = page.get("properties", {})

        # Extract image URL from "Post visual" files property
        post_visual = props.get("Post visual", {})
        files = post_visual.get("files", [])

        image_url = None
        for f in files:
            if f.get("type") == "file":
                image_url = f.get("file", {}).get("url")
            elif f.get("type") == "external":
                image_url = f.get("external", {}).get("url")
            if image_url:
                break

        if image_url and page_id in needed_ids:
            image_map[page_id] = image_url
            # Get title for logging
            title_prop = props.get("Name", props.get("title", {}))
            title_parts = title_prop.get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else "?"
            print(f"  Found image for: {title} ({page_id})")

    has_more = result.get("has_more", False)
    start_cursor = result.get("next_cursor")

    if has_more:
        time.sleep(1)

# 4. Save results
output_path = os.path.join(os.path.dirname(__file__), "..", "data", "image_urls.json")
with open(output_path, "w") as fh:
    json.dump(image_map, fh, indent=2)

print(f"\nDone! Found {len(image_map)} image URLs out of {len(needed_ids)} needed.")
print(f"Saved to {os.path.abspath(output_path)}")

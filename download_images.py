#!/usr/bin/env python3
"""Download post images from Notion URLs and update posts.json + index.html."""

import json
import os
import re
import urllib.request
import ssl

BASE = "/Users/tejanpereira/Documents/nous-post-analysis"
IMAGES_DIR = os.path.join(BASE, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# 1. Load data
with open(os.path.join(BASE, "data/image_urls.json")) as f:
    image_urls = json.load(f)

with open(os.path.join(BASE, "data/posts.json")) as f:
    posts = json.load(f)

# Build lookup: post_id -> post index
post_lookup = {p["id"]: i for i, p in enumerate(posts)}

# 3. Download images
downloaded = 0
failed = 0
skipped = 0

# Create SSL context that doesn't verify (Notion S3 signed URLs)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

for post_id, url in image_urls.items():
    if not url:
        skipped += 1
        continue

    if post_id not in post_lookup:
        print(f"  SKIP: post_id {post_id} not found in posts.json")
        skipped += 1
        continue

    post = posts[post_lookup[post_id]]

    # Generate safe filename
    name = post.get("name", post_id)
    seq = post.get("post_sequence", 0)
    safe_name = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    filename = f"{safe_name}_post{seq}.jpg"
    filepath = os.path.join(IMAGES_DIR, filename)
    rel_path = f"images/{filename}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlretrieve(url, filepath)
        post["image"] = rel_path
        downloaded += 1
        if downloaded % 10 == 0:
            print(f"  Progress: {downloaded} downloaded...")
    except Exception as e:
        print(f"  FAIL: {name} — {e}")
        failed += 1

print(f"\nDone: {downloaded} downloaded, {failed} failed, {skipped} skipped")

# 4. Save updated posts.json
with open(os.path.join(BASE, "data/posts.json"), "w") as f:
    json.dump(posts, f, indent=2, ensure_ascii=True)
print("Saved data/posts.json")

# 5. Re-inject into index.html
with open(os.path.join(BASE, "index.html"), "r") as f:
    html = f.read()

with open(os.path.join(BASE, "data/post_extras.json")) as f:
    extras = json.load(f)

posts_js = json.dumps(posts, indent=2, ensure_ascii=True)
extras_js = json.dumps(extras, indent=2, ensure_ascii=True)

html = re.sub(
    r'const posts = \[[\s\S]*?\n\];',
    lambda m: f'const posts = {posts_js};',
    html,
    count=1
)

html = re.sub(
    r'const postExtras = \{[\s\S]*?\n\};',
    lambda m: f'const postExtras = {extras_js};',
    html,
    count=1
)

with open(os.path.join(BASE, "index.html"), "w") as f:
    f.write(html)
print("Updated index.html")

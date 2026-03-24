"""
Nous Thread Checker — checks Slack threads on bot posts in #approving-content,
analyzes team comments with Claude, and updates the Notion feedback database.

Usage:
    python thread_checker.py                # Default: last 48 hours
    python thread_checker.py --dry-run      # Preview without writing to Notion
    python thread_checker.py --hours 24     # Custom lookback window
    python thread_checker.py --verbose       # Print thread contents
"""

import os
import sys
import json
import re
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")
NOTION_FEEDBACK_DB = os.environ.get("NOTION_FEEDBACK_DB", "0e7d5f8cb1be416d9dc23b68103ce739")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CHANNEL = os.environ.get("SLACK_APPROVING_CONTENT_CHANNEL", "#approving-content")

STATE_FILE = Path(__file__).parent / "data" / "thread_check_state.json"

# Classification → Notion Rating mapping
CLASSIFICATION_TO_RATING = {
    "AGREES": "Accurate",
    "MINOR_CORRECTIONS": "Partially",
    "MAJOR_CORRECTIONS": "Off",
    "UNRELATED": None,  # No rating change
}

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def load_state() -> dict:
    """Load the state file tracking processed messages."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict):
    """Write state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------


def slack_headers():
    return {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}


def resolve_channel_id(channel_name: str) -> str | None:
    """Resolve a #channel-name to a Slack channel ID."""
    if channel_name.startswith("C") and not channel_name.startswith("#"):
        return channel_name  # Already an ID

    clean = channel_name.lstrip("#")
    cursor = None
    for _ in range(10):
        params = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.list",
            headers=slack_headers(),
            params=params,
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[ERROR] conversations.list failed: {data.get('error')}")
            return None
        for ch in data.get("channels", []):
            if ch.get("name") == clean:
                return ch["id"]
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None


def get_bot_user_id() -> str | None:
    """Get the bot's own user ID via auth.test."""
    resp = requests.post(
        "https://slack.com/api/auth.test",
        headers=slack_headers(),
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        return data.get("user_id")
    print(f"[ERROR] auth.test failed: {data.get('error')}")
    return None


def fetch_channel_history(channel_id: str, oldest_ts: float) -> list[dict]:
    """Fetch messages from the channel since oldest_ts."""
    messages = []
    cursor = None
    for _ in range(20):
        params = {
            "channel": channel_id,
            "oldest": str(oldest_ts),
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.history",
            headers=slack_headers(),
            params=params,
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[ERROR] conversations.history failed: {data.get('error')}")
            break
        messages.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor or not data.get("has_more"):
            break
    return messages


def fetch_thread_replies(channel_id: str, thread_ts: str) -> list[dict]:
    """Fetch all replies in a thread."""
    replies = []
    cursor = None
    for _ in range(10):
        params = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.replies",
            headers=slack_headers(),
            params=params,
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[ERROR] conversations.replies failed: {data.get('error')}")
            break
        replies.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor or not data.get("has_more"):
            break
    return replies


def get_user_name(user_id: str, _cache: dict = {}) -> str:
    """Resolve a Slack user ID to a display name (cached)."""
    if user_id in _cache:
        return _cache[user_id]
    resp = requests.get(
        "https://slack.com/api/users.info",
        headers=slack_headers(),
        params={"user": user_id},
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        profile = data["user"].get("profile", {})
        name = profile.get("display_name") or profile.get("real_name") or user_id
        _cache[user_id] = name
        return name
    _cache[user_id] = user_id
    return user_id


# ---------------------------------------------------------------------------
# Parse bot message to extract influencer / brief / frame
# ---------------------------------------------------------------------------


def parse_bot_message(text: str) -> dict:
    """Extract influencer name, brief, and frame from the bot's analysis message.

    The bot posts messages with a caption like:
        "Influencer Name - Brief Name - Frame N" or similar patterns.
    Also checks for structured analysis text in thread replies.
    """
    info = {"influencer": "", "brief": "", "frame": ""}

    if not text:
        return info

    # Try pattern: "Influencer - Brief - Frame N"
    m = re.search(r"^(.+?)\s*[-–]\s*(.+?)\s*[-–]\s*[Ff]rame\s*(\d+)", text, re.MULTILINE)
    if m:
        info["influencer"] = m.group(1).strip()
        info["brief"] = m.group(2).strip()
        info["frame"] = m.group(3).strip()
        return info

    # Try pattern: influencer name in bold or first line, brief/frame elsewhere
    # Look for "Frame N" or "Frame: N"
    frame_m = re.search(r"[Ff]rame\s*:?\s*(\d+)", text)
    if frame_m:
        info["frame"] = frame_m.group(1)

    # Look for brief name patterns
    brief_m = re.search(
        r"[Bb]rief\s*:?\s*(.+?)(?:\n|$|[-–]|\s*Frame)", text
    )
    if brief_m:
        info["brief"] = brief_m.group(1).strip().rstrip("-– ")

    # Influencer is often the first line or the text before the first dash
    first_line = text.split("\n")[0].strip()
    if first_line and not first_line.startswith(("*", "_", ">")):
        # Take text before first separator
        parts = re.split(r"\s*[-–|]\s*", first_line)
        if parts:
            candidate = parts[0].strip().strip("*_")
            if candidate and len(candidate) < 60:
                info["influencer"] = candidate

    return info


# ---------------------------------------------------------------------------
# Claude analysis of thread comments
# ---------------------------------------------------------------------------


THREAD_ANALYSIS_PROMPT = """You are analyzing a Slack thread where an AI bot posted an Instagram Story review, and team members have replied with feedback.

Here is the bot's original analysis:
---
{bot_message}
---

Here are the team's thread replies:
---
{thread_replies}
---

Classify the team's overall response into one of these categories:
- AGREES: The team confirms the AI review was accurate. Replies like "looks good", "agree", thumbs up, or no substantive disagreement.
- MINOR_CORRECTIONS: The team mostly agrees but has small tweaks or additions. E.g., "good but also the font is too small" or "I'd add that the CTA needs moving."
- MAJOR_CORRECTIONS: The team significantly disagrees with the AI's assessment. E.g., "this should be needs_work not good_to_go" or "the AI completely missed that there's no savings figure."
- UNRELATED: The replies are not about the accuracy of the AI review — just general discussion, questions, or off-topic chat.

Return ONLY a JSON object:
{{
  "classification": "AGREES" | "MINOR_CORRECTIONS" | "MAJOR_CORRECTIONS" | "UNRELATED",
  "summary": "<1-2 sentence summary of what the team said>",
  "key_points": ["<point 1>", "<point 2>"]
}}
"""


def analyze_thread_with_claude(bot_message: str, human_replies: list[dict]) -> dict | None:
    """Use Claude to classify the team's thread responses."""
    if not ANTHROPIC_API_KEY:
        print("[ERROR] ANTHROPIC_API_KEY not set, cannot analyze threads")
        return None

    replies_text = "\n".join(
        f"- {r['user_name']}: {r['text']}" for r in human_replies
    )

    prompt = THREAD_ANALYSIS_PROMPT.format(
        bot_message=bot_message[:3000],
        thread_replies=replies_text[:3000],
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip markdown fences if present
        if "```" in raw:
            fence_m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
            if fence_m:
                raw = fence_m.group(1).strip()

        # Parse JSON
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: extract first JSON object
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end > start:
                return json.loads(raw[start : end + 1])
            print(f"[WARN] Could not parse Claude response: {raw[:200]}")
            return None

    except Exception as e:
        print(f"[ERROR] Claude API error: {e}")
        return None


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------


def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _notion_rich_text(text: str) -> list:
    if not text:
        return []
    return [{"text": {"content": str(text)[:2000]}}]


def search_feedback_entry(influencer: str, brief: str, frame: str) -> dict | None:
    """Search the Notion feedback DB for an existing entry matching influencer + brief + frame."""
    filters = []
    if influencer:
        filters.append({"property": "Influencer", "rich_text": {"contains": influencer}})
    if brief:
        filters.append({"property": "Brief", "rich_text": {"contains": brief}})
    if frame:
        filters.append({"property": "Frame", "rich_text": {"equals": str(frame)}})

    if not filters:
        return None

    body = {
        "filter": {"and": filters} if len(filters) > 1 else filters[0],
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": 1,
    }

    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_FEEDBACK_DB}/query",
        headers=notion_headers(),
        json=body,
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"[WARN] Notion query failed ({resp.status_code}): {resp.text[:200]}")
        return None

    results = resp.json().get("results", [])
    return results[0] if results else None


def update_feedback_entry(page_id: str, comment_append: str, rating: str | None):
    """Append thread feedback comment to an existing Notion feedback page."""
    # First fetch existing comment
    resp = requests.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"[WARN] Could not fetch page {page_id}: {resp.status_code}")
        return False

    props = resp.json().get("properties", {})
    existing_comment_rt = props.get("Comment", {}).get("rich_text", [])
    existing_comment = existing_comment_rt[0]["plain_text"] if existing_comment_rt else ""

    new_comment = (existing_comment + "\n\n" + comment_append).strip() if existing_comment else comment_append

    update_props = {
        "Comment": {"rich_text": _notion_rich_text(new_comment)},
    }

    if rating and rating in ("Accurate", "Partially", "Off"):
        update_props["Rating"] = {"select": {"name": rating}}

    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(),
        json={"properties": update_props},
        timeout=15,
    )

    if resp.status_code == 200:
        return True
    else:
        print(f"[WARN] Notion update failed ({resp.status_code}): {resp.text[:200]}")
        return False


def create_feedback_entry(influencer: str, brief: str, frame: str, comment: str, rating: str | None, ai_verdict: str = ""):
    """Create a new Notion feedback entry from thread analysis."""
    properties = {
        "Name": {"title": _notion_rich_text(f"{influencer or 'Unknown'} - Frame {frame or '?'}")},
        "Reviewer": {"rich_text": _notion_rich_text("Thread Checker")},
        "Influencer": {"rich_text": _notion_rich_text(influencer)},
        "Brief": {"rich_text": _notion_rich_text(brief)},
        "Frame": {"rich_text": _notion_rich_text(str(frame))},
        "Comment": {"rich_text": _notion_rich_text(comment)},
    }

    if rating and rating in ("Accurate", "Partially", "Off"):
        properties["Rating"] = {"select": {"name": rating}}

    if ai_verdict in ("good_to_go", "needs_work"):
        properties["AI Verdict"] = {"select": {"name": ai_verdict}}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    properties["Date"] = {"date": {"start": today}}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json={"parent": {"database_id": NOTION_FEEDBACK_DB}, "properties": properties},
        timeout=15,
    )

    if resp.status_code == 200:
        return True
    else:
        print(f"[WARN] Notion create failed ({resp.status_code}): {resp.text[:200]}")
        return False


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run(hours: int = 48, dry_run: bool = False, verbose: bool = False):
    """Main entry point: scan threads, analyze, update Notion."""

    # Validate config
    missing = []
    if not SLACK_BOT_TOKEN:
        missing.append("SLACK_BOT_TOKEN")
    if not NOTION_TOKEN:
        missing.append("NOTION_API_KEY / NOTION_TOKEN")
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        print(f"[ERROR] Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    # Resolve channel
    print(f"Resolving channel: {CHANNEL}")
    channel_id = resolve_channel_id(CHANNEL)
    if not channel_id:
        print(f"[ERROR] Could not resolve channel '{CHANNEL}'")
        sys.exit(1)
    print(f"  Channel ID: {channel_id}")

    # Get bot's own user ID to filter its messages
    bot_user_id = get_bot_user_id()
    if not bot_user_id:
        print("[ERROR] Could not determine bot user ID")
        sys.exit(1)
    print(f"  Bot user ID: {bot_user_id}")

    # Load state
    state = load_state()
    processed = state.get("processed_threads", {})
    # processed = { message_ts: { "last_reply_count": N, "last_checked": iso_timestamp } }

    # Fetch messages
    oldest = datetime.now(timezone.utc) - timedelta(hours=hours)
    oldest_ts = oldest.timestamp()
    print(f"  Fetching messages from last {hours} hours (since {oldest.isoformat()})...")

    messages = fetch_channel_history(channel_id, oldest_ts)
    print(f"  Found {len(messages)} messages in channel")

    # Filter for bot messages with threads
    bot_messages = []
    for msg in messages:
        # Bot messages can be identified by:
        # 1. bot_id field present
        # 2. user field matching our bot user ID
        # 3. subtype "bot_message"
        is_bot = (
            msg.get("bot_id")
            or msg.get("user") == bot_user_id
            or msg.get("subtype") in ("bot_message", "file_share")
        )
        has_thread = msg.get("reply_count", 0) > 0
        if is_bot and has_thread:
            bot_messages.append(msg)

    print(f"  Found {len(bot_messages)} bot messages with thread replies")

    if not bot_messages:
        print("  Nothing to process.")
        return

    # Process each thread
    stats = {"analyzed": 0, "updated": 0, "created": 0, "skipped": 0, "unrelated": 0}

    for msg in bot_messages:
        ts = msg.get("ts", "")
        reply_count = msg.get("reply_count", 0)
        msg_text = msg.get("text", "")

        # Check if we already processed this thread with the same reply count
        prev = processed.get(ts, {})
        if prev.get("last_reply_count", 0) >= reply_count:
            stats["skipped"] += 1
            if verbose:
                print(f"\n  [SKIP] Thread {ts} — already processed ({reply_count} replies)")
            continue

        print(f"\n  Processing thread {ts} ({reply_count} replies)...")

        # Parse bot message for metadata
        # The bot's initial message may be a file share with initial_comment as caption,
        # and the analysis text is posted as a reply by the bot itself
        info = parse_bot_message(msg_text)

        # Fetch thread replies
        replies = fetch_thread_replies(channel_id, ts)

        # Separate bot replies (contain the analysis) from human replies
        bot_analysis_text = msg_text  # Start with the parent message
        human_replies = []

        for reply in replies:
            # Skip the parent message itself
            if reply.get("ts") == ts:
                continue

            reply_user = reply.get("user", "")
            is_bot_reply = (
                reply.get("bot_id")
                or reply_user == bot_user_id
            )

            if is_bot_reply:
                # This is likely the bot's analysis text
                bot_analysis_text = reply.get("text", bot_analysis_text)
                # Also try to extract info from the analysis
                if not info["influencer"]:
                    info = parse_bot_message(reply.get("text", ""))
            else:
                user_name = get_user_name(reply_user)
                human_replies.append({
                    "user_id": reply_user,
                    "user_name": user_name,
                    "text": reply.get("text", ""),
                    "ts": reply.get("ts", ""),
                })

        if not human_replies:
            if verbose:
                print(f"    No human replies found, skipping")
            stats["skipped"] += 1
            # Still mark as processed so we don't re-check
            processed[ts] = {
                "last_reply_count": reply_count,
                "last_checked": datetime.now(timezone.utc).isoformat(),
            }
            continue

        if verbose:
            print(f"    Influencer: {info['influencer'] or '(unknown)'}")
            print(f"    Brief: {info['brief'] or '(unknown)'}")
            print(f"    Frame: {info['frame'] or '(unknown)'}")
            print(f"    Human replies ({len(human_replies)}):")
            for r in human_replies:
                print(f"      - {r['user_name']}: {r['text'][:100]}")

        # Analyze with Claude
        print(f"    Analyzing {len(human_replies)} team replies with Claude...")
        analysis = analyze_thread_with_claude(bot_analysis_text, human_replies)
        stats["analyzed"] += 1

        if not analysis:
            print(f"    [WARN] Claude analysis failed, skipping")
            continue

        classification = analysis.get("classification", "UNRELATED")
        summary = analysis.get("summary", "")
        key_points = analysis.get("key_points", [])

        print(f"    Classification: {classification}")
        print(f"    Summary: {summary}")

        if classification == "UNRELATED":
            stats["unrelated"] += 1
            processed[ts] = {
                "last_reply_count": reply_count,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "classification": classification,
            }
            if not dry_run:
                save_state({"processed_threads": processed})
            continue

        # Build comment text
        reply_names = ", ".join(sorted(set(r["user_name"] for r in human_replies)))
        points_str = "; ".join(key_points) if key_points else summary
        comment = f"[Thread check] {classification} — {summary}"
        if key_points:
            comment += f"\nKey points: {points_str}"
        comment += f"\nReviewers: {reply_names}"

        # Map classification to Notion rating
        rating = CLASSIFICATION_TO_RATING.get(classification)

        if dry_run:
            print(f"    [DRY RUN] Would update Notion:")
            print(f"      Influencer: {info['influencer']}")
            print(f"      Brief: {info['brief']}")
            print(f"      Frame: {info['frame']}")
            print(f"      Rating: {rating}")
            print(f"      Comment: {comment[:120]}...")
        else:
            # Try to find existing entry
            existing = None
            if info["influencer"] or info["brief"]:
                existing = search_feedback_entry(
                    info["influencer"], info["brief"], info["frame"]
                )

            if existing:
                page_id = existing["id"]
                print(f"    Updating existing entry {page_id[:8]}...")
                if update_feedback_entry(page_id, comment, rating):
                    stats["updated"] += 1
                    print(f"    Updated successfully")
                else:
                    print(f"    [WARN] Update failed")
            else:
                print(f"    Creating new feedback entry...")
                if create_feedback_entry(
                    info["influencer"], info["brief"], info["frame"],
                    comment, rating,
                ):
                    stats["created"] += 1
                    print(f"    Created successfully")
                else:
                    print(f"    [WARN] Create failed")

        # Update state
        processed[ts] = {
            "last_reply_count": reply_count,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "classification": classification,
        }

        # Save state incrementally (like story check tracking pattern)
        if not dry_run:
            save_state({"processed_threads": processed})

    # Final save
    if not dry_run:
        save_state({"processed_threads": processed})

    # Summary
    print(f"\n--- Thread Check Complete ---")
    print(f"  Analyzed:  {stats['analyzed']}")
    print(f"  Updated:   {stats['updated']}")
    print(f"  Created:   {stats['created']}")
    print(f"  Skipped:   {stats['skipped']}")
    print(f"  Unrelated: {stats['unrelated']}")
    if dry_run:
        print(f"  (DRY RUN — no Notion changes made)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check Slack threads on bot posts and update Notion feedback DB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be updated without writing to Notion",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Look back N hours (default: 48)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print thread contents and details",
    )
    args = parser.parse_args()

    run(hours=args.hours, dry_run=args.dry_run, verbose=args.verbose)

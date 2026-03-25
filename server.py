"""
Nous Story Analyser — Flask server for analysing Instagram Story images.
Endpoint: POST /analyse
"""

import os
import json
import base64
import traceback
import requests as http_requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APPROVING_CONTENT_CHANNEL = os.environ.get("SLACK_APPROVING_CONTENT_CHANNEL", "#approving-content")
NOTION_TOKEN = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")
NOTION_FEEDBACK_DB = os.environ.get("NOTION_FEEDBACK_DB", "0e7d5f8cb1be416d9dc23b68103ce739")
NOTION_POSTS_DB = (os.environ.get("NOTION_POSTS_DB") or "").strip()
NOTION_CAMPAIGNS_DB = (os.environ.get("NOTION_CAMPAIGNS_DB") or "").strip()

BRIEFS = [
    {"brief": "Family/Lifestyle Brief 1", "frames": [1, 2, 3]},
    {"brief": "Family/Lifestyle Brief 2", "frames": [1, 2, 3]},
    {"brief": "Family Repeat", "frames": [1, 2, 3]},
    {"brief": "Home Brief 1", "frames": [1, 2, 3]},
    {"brief": "Home Brief 2", "frames": [1, 2, 3]},
    {"brief": "Celebrity Brief 1", "frames": [1, 2, 3]},
    {"brief": "Celebrity Brief 2", "frames": [1, 2, 3]},
    {"brief": "Fashion Brief 1", "frames": [1, 2, 3]},
    {"brief": "Fashion Brief 2", "frames": [1, 2, 3]},
    {"brief": "Lifestyle Brief 1", "frames": [1, 2, 3]},
    {"brief": "Nous March 2026", "frames": [1, 2, 3]},
]

# Per-brief, per-frame guidance extracted from Notion briefs.
# Keys: brief name prefix (matched case-insensitively) → frame number → guidance dict
BRIEF_FRAME_GUIDANCE = {
    "family": {
        1: {
            "title": "Nous is saving you hundreds of pounds this year",
            "visual": "A calming shot within the house",
            "cta": "Save with Nous",
            "messaging_focus": "Full story: hook about energy prices/overpaying, discovery of Nous, specific savings, sign-up ease. Should include @get_nous in body text. Confessional/personal tone.",
            "skip_criteria": [],
            "special_rules": [],
        },
        2: {
            "title": "Nous saves you time & stress by looking after your bills",
            "visual": "A calming shot within the house",
            "cta": "Save with Nous",
            "messaging_focus": "Discovery + explanation: how they found Nous, what it does (checks bills, finds better deals, switches you), ongoing management (tracks contracts, switches automatically). Should mention energy, broadband, phone/mobile.",
            "skip_criteria": ["problem_hook"],
            "special_rules": [],
        },
        3: {
            "title": "People love using Nous to help them manage their bills",
            "visual": "A calming shot within the house",
            "cta": "Save with Nous",
            "messaging_focus": "Social proof callback: hearing from followers about their savings, reinforcing the benefits. Can reference Trustpilot. Should still include @get_nous and savings figure.",
            "skip_criteria": ["problem_hook", "discovery_moment"],
            "special_rules": ["This is a social proof / community callback frame, NOT a short CTA-only frame. It should have substantial copy."],
        },
    },
    "celebrity": {
        1: {
            "title": "Nous means you're not wasting money on bills",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Hook about hating wasting money, discovery that bills were too high, Nous found better deals and switched. Personal, slightly dramatic tone.",
            "skip_criteria": [],
            "special_rules": [],
        },
        2: {
            "title": "Nous saves you time & stress by looking after your bills",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Busy life angle: between family/work/everything, never had time to sort bills. Nous did everything. Saved £500+ on energy, phone, internet. Emphasis on convenience and time saved.",
            "skip_criteria": ["problem_hook"],
            "special_rules": [],
        },
        3: {
            "title": "Nous has saved you hundreds on your bills",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Full discovery story: lightbulb moment about overpaying, signed up to @get_nous, they showed savings and sorted better deals. NOT a short callback — this is a full-length story frame.",
            "skip_criteria": ["discovery_moment"],
            "special_rules": ["This is a FULL STORY frame, not a short callback. It should have substantial copy with a personal discovery angle."],
        },
    },
    "fashion": {
        1: {
            "title": "I've found an amazing new way to save money (TEASER)",
            "visual": "A lifestyle shot or selfie",
            "cta": "Start saving here!",
            "messaging_focus": "SHORT TEASER only. Intriguing, mysterious. Must NOT mention Nous. Must NOT include @get_nous. Keep text minimal — just a hook about saving money on bills from their phone.",
            "skip_criteria": ["discovery_moment", "what_nous_does", "get_nous_tag"],
            "special_rules": [
                "MUST NOT mention Nous or @get_nous anywhere — this is a teaser frame.",
                "CTA button text MUST be 'Start saving here!' NOT 'Save with Nous'.",
                "Copy should be SHORT (3-4 lines max) to create intrigue.",
            ],
        },
        2: {
            "title": "Check out the results of my latest hack (REVEAL)",
            "visual": "A shot of a recent haul or purchase",
            "cta": "Save with Nous",
            "messaging_focus": "Reveal frame: latest haul was 'basically free' thanks to savings. Now introduces @get_nous by name. Explains how Nous saved them £600+ by finding better deals on bills. Shopping/treat angle.",
            "skip_criteria": ["problem_hook"],
            "special_rules": ["Visual should be a haul/purchase, not a calm home shot."],
        },
        3: {
            "title": "You realised you were overpaying but now Nous has sorted it",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Full overpaying story: 'loves a bargain' angle, fuming about overpaying, signed up to @get_nous, they sorted better deals. NOT a short callback — full-length story.",
            "skip_criteria": ["discovery_moment"],
            "special_rules": ["This is a FULL STORY frame, not a short callback. Should have substantial copy."],
        },
    },
    "home": {
        1: {
            "title": "Nous is saving you hundreds and making life easier",
            "visual": "A calming shot within the home",
            "cta": "Save with Nous",
            "messaging_focus": "Home-focused: thought they were switched-on about deals but were overpaying. Signed up to @get_nous. They checked all bills, found better deals, switched. Relief at having one less home admin task. Emphasis on saving money AND making home life easier.",
            "skip_criteria": [],
            "special_rules": ["Home niche: visual must be within the home, messaging should reference home/house admin."],
        },
        2: {
            "title": "The one thing in my home I was getting totally wrong",
            "visual": "A calming shot within the house",
            "cta": "Save with Nous",
            "messaging_focus": "Discovery frame: 'mistakes with the house' angle, bills were quietly costing hundreds. Signed up to @get_nous to remove boring admin. Mentions 'a few quick questions' ease. Home admin relief emphasis.",
            "skip_criteria": ["problem_hook"],
            "special_rules": ["Should tie into home improvement / home management content style."],
        },
        3: {
            "title": "Check out what I've bought thanks to Nous",
            "visual": "A shot of a recent purchase for the home",
            "cta": "Save with Nous",
            "messaging_focus": "Purchase showcase: showing something bought with the savings from @get_nous. Saved hundreds on bills. Easy sign-up. Now dealing with less admin and spending on things they love. Win-win angle.",
            "skip_criteria": ["problem_hook", "discovery_moment"],
            "special_rules": ["Visual should show a recent home purchase, NOT just a calm interior shot.", "This is a purchase showcase frame — the visual of the purchase is key."],
        },
    },
    "lifestyle": {
        1: {
            "title": "My latest life lesson",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Life lesson angle: recently found out they were massively overpaying. Suppliers quietly creep up bills. Discovered @get_nous, saved hundreds on energy, broadband, phone. They check, find better deals, switch you. No time/patience to do it yourself.",
            "skip_criteria": [],
            "special_rules": [],
        },
        2: {
            "title": "Realisation about my bills",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Phone bill shock stat: money experts say you shouldn't pay more than £7/month for mobile. Discovery of @get_nous, saved on phone + energy + internet. They did all research and switching. 'Prices just creep up' angle.",
            "skip_criteria": ["problem_hook"],
            "special_rules": ["May reference the £7/month phone bill stat."],
        },
        3: {
            "title": "You realised you were overpaying but now Nous has sorted it",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Emotional callback: still fuming about overpaying. Never had time to check. Signed up to @get_nous and they sorted better deals straight away. Saved hundreds. Full story, not short callback.",
            "skip_criteria": ["discovery_moment"],
            "special_rules": ["This is a FULL STORY frame, not a short callback. Should have substantial copy."],
        },
    },
    "nous march": {
        1: {
            "title": "How Nous is saving you money on your bills",
            "visual": "A lifestyle shot or selfie",
            "cta": "Save with Nous",
            "messaging_focus": "Direct discovery + savings: signed up to @get_nous, they spotted overpaying on everything (energy, broadband, mobile), switched to better deals, saving £600. Took 5 minutes.",
            "skip_criteria": [],
            "special_rules": [],
        },
        2: {
            "title": "Feedback from your followers",
            "visual": "Calm image that includes a DM/message screenshot from a follower about using Nous",
            "cta": "Save with Nous",
            "messaging_focus": "Social proof: showing follower messages about saving with Nous. 'Not the only one wasting money on bills.' Still includes own savings story. @get_nous tag required.",
            "skip_criteria": ["problem_hook", "discovery_moment"],
            "special_rules": ["Visual MUST include a screenshot of a follower DM/message about Nous savings.", "This is a social proof frame — the DM screenshot is the key visual element."],
        },
        3: {
            "title": "Text-only post about how Nous has saved you money",
            "visual": "Coloured background with TEXT ONLY — no lifestyle image, no selfie",
            "cta": "Save with Nous",
            "messaging_focus": "PSA-style text-only recap: still in shock at how easy it was. @get_nous spotted overpaying, switched everything, saving £600, took 5 minutes. Worth checking.",
            "skip_criteria": ["problem_hook", "discovery_moment"],
            "special_rules": [
                "Visual MUST be text-only on a coloured background — NO lifestyle image or selfie.",
                "This is a text-only frame. The visual style check should look for clean text on a solid/gradient background.",
            ],
        },
    },
}


def get_brief_guidance(brief_name, frame):
    """Look up per-brief, per-frame guidance. Returns guidance dict or None."""
    brief_lower = brief_name.lower()
    for prefix, frames in BRIEF_FRAME_GUIDANCE.items():
        if prefix in brief_lower:
            return frames.get(frame)
    return None

SYSTEM_PROMPT = """You are a content quality reviewer for Nous, a UK utility-switching service that saves users £500+ by switching energy, broadband, and mortgage providers.

You review Instagram Story images posted by influencers promoting Nous. Your job is to assess each story frame against a strict checklist and return structured feedback.

You MUST respond ONLY with valid JSON — no preamble, no markdown, no explanation outside the JSON object. Your entire response must be parseable by Python's json.loads().

The JSON structure must be exactly:
{
  "overall": "good_to_go" or "needs_work",
  "obvious_tweaks": [
    {
      "label": "<issue description>",
      "pass": <true or false>,
      "note": "<brief specific observation, 1-2 sentences>"
    }
  ],
  "brief_fit": [
    {
      "label": "<issue description>",
      "pass": <true or false>,
      "note": "<brief specific observation, 1-2 sentences>"
    }
  ],
  "summary": "<2-3 sentence summary of overall quality, referencing specific details visible in the image>",
  "improvements": ["<improvement 1>", "<improvement 2>", "<improvement 3>"],
  "copy_rewrite": "<if needs_work AND there are text/copy issues, provide a full rewritten version of the story copy that fixes the issues. If good_to_go or no copy issues, return empty string>",
  "email": "<single short email — addressed to agent if agent name provided, otherwise to influencer directly>"
}

"obvious_tweaks" covers visual/technical issues: text readability, text size (too small or too big), button placement, button text, CTA visibility, image quality, font size, contrast, text layout.

"brief_fit" covers content/messaging issues: hook quality, discovery moment, what Nous does, savings claims, sign-up ease, @get_nous tag usage, tone.

IMPORTANT: Always include ALL items in both arrays — both passing AND failing criteria. Do not omit items just because they pass. The frontend needs the full list to show what was checked.

"improvements" is a short array of exactly 3 concise bullet points summarising what needs to change (e.g. "Move CTA button to the bottom of the story", "Add a specific savings figure like £781/year"). If good_to_go, still include 3 minor suggestions. Each should be actionable and specific.

"copy_rewrite" should contain a complete rewritten version of the influencer's story text/copy that fixes any messaging issues found in brief_fit. Preserve the influencer's voice and tone while fixing compliance issues. If there are no text/copy issues (e.g. only visual problems), return an empty string.

Use "good_to_go" when score >= 80% of total criteria pass. Otherwise use "needs_work".
"""

CRITERIA_PROMPT = """
Evaluate this Instagram Story image against the following criteria. For each sub-criterion, determine pass (true) or fail (false) based only on what is visible in the image.

IMPORTANT — Frame-specific guidance:
Each brief has 3 frames. NOT all criteria apply equally to every frame:

Frame 1 (Hook): This is the opening story. It MUST have a problem-aware hook, personal confession, or shock stat. It should NOT mention Nous or @get_nous yet. Criteria 1 (Problem-aware hook) is critical here. Criteria 3-5 (What Nous does, Savings claim, Sign-up ease) are NOT expected on Frame 1 — do NOT fail them if absent.

Frame 2 (Discovery + Explanation): This is where the influencer introduces Nous. Criteria 2 (Discovery moment), 3 (What Nous does), 4 (Savings claim), and 5 (Sign-up ease) are critical here. Criterion 1 (Problem-aware hook) is NOT expected — do NOT fail it if absent.

Frame 3 (Callback / CTA): This is a short callback frame — a final nudge to sign up. It is typically very short (1-2 sentences). Criteria 1 (Problem-aware hook), 2 (Discovery moment), 3 (What Nous does), and 5 (Sign-up ease) are NOT expected on Frame 3 — do NOT fail them if absent. The most important things on Frame 3 are: CTA button (criteria 7-8), @get_nous tag, and a clear savings figure or callback to the saving mentioned earlier.

CRITERIA TO CHECK:

1. Problem-aware hook [FRAME 1 ONLY — skip for Frames 2-3]
   a. Doesn't open with brand name or product (@get_nous / "I've been using Nous")
   b. Opens with a personal problem, confession, or shock stat
   c. No "loads of people posting about Nous" or herd-following language
   d. Tone is confessional or self-deprecating, not corporate
   e. No unproven enthusiasm ("excited to see what we can save")

2. Discovery moment [FRAME 2 ONLY — skip for Frames 1 and 3]
   a. Discovery feels natural, not scripted ("I stumbled across", not "Nous asked me")
   b. Nous called a "tool", not a "company"
   c. No "I promise it's totally legit" disclaimer
   d. Creator has clearly signed up and used Nous themselves

3. What Nous does [FRAME 2 ONLY — skip for Frames 1 and 3]
   a. Mentions switching across energy, broadband and phone/mobile
   b. No claim that Nous finds the cheapest deal on the whole market
   c. No claim that Nous reminds you when contracts end
   d. Makes clear Nous handles the switching (zero effort for user)

4. Savings claim [FRAMES 2-3 — skip for Frame 1]
   a. Specific £ figure mentioned (personal saving OR approved stat: £781/yr, £250 energy, £7/mo phone, £500+)
   b. No vague language like "save loads" or "could save you money"
   c. No "cheapest deal" claim

5. Sign-up ease [FRAME 2 ONLY — skip for Frames 1 and 3]
   a. Mentions how quick it is ("2 minutes", "from my phone in like five minutes")
   b. Mentions Nous is free
   c. References "just a few quick questions" or minimal effort

6. @get_nous tag [ALL FRAMES]
   a. @get_nous appears in body text (unless this is a Fashion Secret/Teaser frame)
   b. @get_nous does not appear in the opening line

7. Save with Nous CTA [ALL FRAMES — critical for Frame 3]
   a. CTA button is present
   b. Button text is acceptable — creative variations are FINE (e.g. "SAVE £€ WITH NOUS", "Start saving here!", "Save hundreds with Nous"). Only fail this if the button text is clearly bad or unengaging (e.g. just "nous.co", a bare URL, or completely unrelated text). Do NOT fail for capitalisation, emoji, or personality in the button text.
   c. No "AD" text inside the button itself
   d. Link/chain emoji (🔗) present alongside CTA
   e. Button text is readable — good contrast, not too small
   f. No vote/poll sticker on the story that competes with the CTA

8. Button placement [ALL FRAMES — critical for Frame 3]
   a. CTA button is at the BOTTOM of the story
   b. Button is not obscured by AD label, stickers or text blocks
   c. Only one CTA button — no competing links

9. Calming/lifestyle visual [ALL FRAMES]
   a. Visual is calming — not busy, cluttered or high-contrast
   b. Visual matches niche expectation for this frame (home interior, lifestyle shot, etc.)

10. Text readability [ALL FRAMES]
    a. Font size is appropriate for phone viewing — fail if text is too small to read comfortably at a glance (common problem: influencers cramming too much copy into small font) OR if text is disproportionately large. Text should be easily readable on a phone screen without zooming.
    b. Text colour has strong contrast against the background
    c. Long copy is broken into multiple text blocks, not a single wall of text
    d. Text is not placed over faces or focal points of the image

Context:
- Brief: {brief}
- Frame number: {frame}
- Influencer: {influencer_name}
- Reviewer signing off as: {agent_name}
{brief_guidance}
Email guidance:

Write ONE short, casual email. If an agent name is provided, address the agent (e.g. "Hey Katie,"); otherwise address the influencer directly (e.g. "Hey {influencer_name},").

Structure for "needs_work":
1. One-line positive opener
2. Max 3 bullet points — short, specific changes needed (not explanations of why)
3. If there are copy issues, add the full rewritten copy below the bullets as "Suggested copy:" on its own line
4. Sign off

Structure for "good_to_go":
1. One line: looks great, happy for it to go live
2. Sign off

Example 1 (needs work, to agent):
"Hi Katie,

Looks great, thanks. Just a couple tweaks from me:

- Could she please cut the second sentence from paragraph 1
- Could she please capitalise "Nous" in the third paragraph

Thanks!
{agent_name}"

Example 2 (needs work, with copy rewrite):
"Hey Rosie,

Thanks for sending over! Could we have a small tweak to the copy for clarity:

- Replace "saving loads" with a specific figure like "£781 a year"
- Add that Nous is free
- Move the CTA button to the bottom of the frame

Suggested copy:
It turns out I've been foolishly overpaying. Luckily I found out about a new tool called Nous and decided to give it a go. It checked everything for me, it's automatically switching me to better deals and I'll be saving hundreds of pounds a year!

Many thanks,
{agent_name}"

Example 3 (good to go):
"Hey [name],

Looks great — happy for this to go live!

Thanks,
{agent_name}"

Key rules:
- Maximum 3 bullet points. Each bullet is ONE short instruction, not an explanation
- Do NOT say "I've included a suggested copy rewrite above" — just put it inline after "Suggested copy:"
- No formal greetings like "I hope this finds you well"
- Sign off as {agent_name}

Now evaluate the image and return ONLY the JSON object described above.
"""


def build_prompt(brief, frame, influencer_name, agent_name):
    guidance = get_brief_guidance(brief, int(frame) if frame else 1)
    brief_section = ""
    if guidance:
        brief_section = f"""

BRIEF-SPECIFIC GUIDANCE for "{brief}" Frame {frame}:
- Frame title: {guidance['title']}
- Expected visual: {guidance['visual']}
- Expected CTA button text: {guidance['cta']}
- Messaging focus: {guidance['messaging_focus']}
"""
        if guidance.get("skip_criteria"):
            brief_section += f"- Skip these criteria (mark as pass, they don't apply to this frame): {', '.join(guidance['skip_criteria'])}\n"
        if guidance.get("special_rules"):
            brief_section += "- SPECIAL RULES:\n"
            for rule in guidance["special_rules"]:
                brief_section += f"  * {rule}\n"

    return CRITERIA_PROMPT.format(
        brief=brief,
        frame=frame,
        influencer_name=influencer_name or "the influencer",
        agent_name=agent_name or "Nous Team",
        brief_guidance=brief_section,
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/briefs", methods=["GET"])
def briefs():
    return jsonify({"briefs": BRIEFS})


# --- Notion upcoming posts lookup (cached) ---
_upcoming_posts_cache = {"data": None, "fetched_at": 0, "error": None, "loading": False}
UPCOMING_POSTS_CACHE_TTL = 14400  # 4 hours — background refresh, disk cache survives restarts
DISK_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "upcoming_posts_cache.json")
_notion_headers = None
_posts_prop_ids = None  # cached property IDs for filter_properties


def _load_disk_cache():
    """Load cached posts from disk so startup is instant (no Notion calls needed)."""
    import time as _t
    try:
        if os.path.exists(DISK_CACHE_FILE):
            with open(DISK_CACHE_FILE, "r") as f:
                disk = json.load(f)
            if disk.get("data") and disk.get("fetched_at"):
                age_hours = (_t.time() - disk["fetched_at"]) / 3600
                _upcoming_posts_cache["data"] = disk["data"]
                _upcoming_posts_cache["fetched_at"] = disk["fetched_at"]
                print(f"[notion] disk cache loaded: {len(disk['data'])} posts, {age_hours:.1f}h old", flush=True)
                return True
    except Exception as e:
        print(f"[notion] disk cache load error: {e}", flush=True)
    return False


def _save_disk_cache(data, fetched_at):
    """Persist cache to disk so it survives restarts."""
    try:
        os.makedirs(os.path.dirname(DISK_CACHE_FILE), exist_ok=True)
        with open(DISK_CACHE_FILE, "w") as f:
            json.dump({"data": data, "fetched_at": fetched_at}, f)
        print(f"[notion] disk cache saved: {len(data)} posts", flush=True)
    except Exception as e:
        print(f"[notion] disk cache save error: {e}", flush=True)


# Load disk cache immediately on import (before any threads start)
_load_disk_cache()


def _get_notion_headers():
    global _notion_headers
    if not _notion_headers:
        _notion_headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
    return _notion_headers


def _get_posts_prop_ids():
    """Resolve and cache the Notion property IDs for Post date and I.Campaigns."""
    global _posts_prop_ids
    if _posts_prop_ids is not None:
        return _posts_prop_ids
    try:
        resp = http_requests.get(
            f"https://api.notion.com/v1/databases/{NOTION_POSTS_DB}",
            headers=_get_notion_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            db_props = resp.json().get("properties", {})
            _posts_prop_ids = []
            for name in ["Post date", "I.Campaigns"]:
                if name in db_props and "id" in db_props[name]:
                    _posts_prop_ids.append(db_props[name]["id"])
                    print(f"[notion] post prop '{name}' → id={db_props[name]['id']}", flush=True)
            # ETM relation isn't in schema API (301 of 311 props returned)
            # but exists with known ID from unfiltered page fetch
            _posts_prop_ids.append("RWE%3A")  # Experiment Treatment Manager
            print(f"[notion] added ETM prop id=RWE%3A (hardcoded)", flush=True)
    except Exception:
        pass
    if not _posts_prop_ids:
        _posts_prop_ids = []
    return _posts_prop_ids


def _notion_query(database_id, body, prop_ids=None):
    """Query a Notion database. Returns list of pages (handles pagination).
    prop_ids: list of property IDs to pass as filter_properties (reduces payload).
    """
    import time as _qt
    pages = []
    start_cursor = None
    max_pages = body.pop("_max_pages", 5)
    for page_num in range(max_pages):
        req_body = dict(body)
        req_body.setdefault("page_size", 100)
        if start_cursor:
            req_body["start_cursor"] = start_cursor
        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        query_params = [("filter_properties", pid) for pid in (prop_ids or [])]
        t0 = _qt.time()
        print(f"[notion] query page {page_num+1}/{max_pages}, page_size={req_body['page_size']}, filter_props={len(prop_ids or [])}", flush=True)
        try:
            resp = http_requests.post(
                url,
                headers=_get_notion_headers(),
                json=req_body,
                params=query_params,
                timeout=60,
            )
        except Exception as e:
            print(f"[notion] query page {page_num+1} FAILED after {int((_qt.time()-t0)*1000)}ms: {e}", flush=True)
            raise
        elapsed = int((_qt.time()-t0)*1000)
        if resp.status_code != 200:
            print(f"[notion] query page {page_num+1} returned {resp.status_code} in {elapsed}ms: {resp.text[:200]}", flush=True)
            raise Exception(f"Notion query failed: {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        batch = data.get("results", [])
        pages.extend(batch)
        print(f"[notion] query page {page_num+1}: {len(batch)} results in {elapsed}ms (total {len(pages)})", flush=True)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return pages


def _notion_get_page(page_id, prop_ids=None):
    """Fetch a single Notion page by ID, optionally filtering properties."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    query_params = [("filter_properties", pid) for pid in (prop_ids or [])]
    resp = http_requests.get(url, headers=_get_notion_headers(), params=query_params, timeout=15)
    if resp.status_code != 200:
        return None
    return resp.json()


def _extract_title(props, field_name="id"):
    """Extract plain text from a Notion title property."""
    title = props.get(field_name, {}).get("title", [])
    return title[0]["plain_text"] if title else ""


def _extract_date(props, field_name):
    """Extract date string from a Notion date property."""
    d = props.get(field_name, {}).get("date")
    return d["start"] if d else None


def _extract_relation_ids(props, field_name):
    """Extract relation page IDs from a Notion relation property."""
    return [r["id"] for r in props.get(field_name, {}).get("relation", [])]


def _extract_formula(props, field_name):
    """Extract string result from a Notion formula property."""
    f = props.get(field_name, {}).get("formula", {})
    return f.get("string") or f.get("number") or ""


def _resolve_campaign_prop_ids(campaign_id):
    """Resolve property IDs for campaign fields we need (cached after first call)."""
    if not hasattr(_resolve_campaign_prop_ids, "_ids"):
        try:
            resp = http_requests.get(
                f"https://api.notion.com/v1/databases/{NOTION_CAMPAIGNS_DB}",
                headers=_get_notion_headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                db_props = resp.json().get("properties", {})
                ids = []
                for name in ["id", "Influencer (string)"]:
                    if name in db_props and "id" in db_props[name]:
                        ids.append(db_props[name]["id"])
                _resolve_campaign_prop_ids._ids = ids
            else:
                _resolve_campaign_prop_ids._ids = []
        except Exception:
            _resolve_campaign_prop_ids._ids = []
    return _resolve_campaign_prop_ids._ids


def _refresh_upcoming_posts():
    """Fetch upcoming posts in background. Updates cache when done.
    Has a 120s total timeout to prevent getting stuck forever.
    """
    import time as _t
    if _upcoming_posts_cache.get("loading"):
        # Check if loading for too long (stuck thread) — allow retry after 120s
        load_start = _upcoming_posts_cache.get("_load_start", 0)
        if _t.time() - load_start < 600:
            return  # still within timeout (10 min for slow Notion queries)
        print("[notion] previous refresh appears stuck (>600s), allowing retry", flush=True)
    _upcoming_posts_cache["loading"] = True
    _upcoming_posts_cache["_load_start"] = _t.time()
    _upcoming_posts_cache["error"] = None
    try:
        if NOTION_TOKEN and NOTION_POSTS_DB:
            data = _fetch_upcoming_posts()
            now = _t.time()
            _upcoming_posts_cache["data"] = data
            _upcoming_posts_cache["fetched_at"] = now
            _save_disk_cache(data, now)
            print(f"[notion] refresh complete: {len(data)} upcoming posts cached in {int((now - _upcoming_posts_cache['_load_start'])*1000)}ms", flush=True)
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        _upcoming_posts_cache["error"] = str(e)
        print(f"[notion] refresh error: {e}", flush=True)
    finally:
        _upcoming_posts_cache["loading"] = False

import threading


def _fetch_upcoming_posts():
    """Fetch upcoming posts (next 30 days) from Notion Posts DB + campaign details.
    Runs in background — disk cache serves requests while this completes.
    Refreshes every 4 hours.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timedelta

    t_start = _time.time()
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    one_month = (now + timedelta(days=30)).strftime("%Y-%m-%d")

    # Resolve property IDs (3 props: Post date, I.Campaigns, ETM)
    post_pids = _get_posts_prop_ids()
    camp_pids = _resolve_campaign_prop_ids(None)
    print(f"[notion] prop IDs resolved in {int((_time.time()-t_start)*1000)}ms, post_pids={post_pids}", flush=True)

    # Query posts — even with filter_properties, Notion is slow on 311-prop DBs
    # page_size=10 works reliably (~22s per page), page_size=100 causes 30s timeouts
    t1 = _time.time()
    posts = _notion_query(NOTION_POSTS_DB, {
        "filter": {
            "and": [
                {"property": "Post date", "date": {"on_or_after": today}},
                {"property": "Post date", "date": {"on_or_before": one_month}},
            ]
        },
        "sorts": [{"property": "Post date", "direction": "ascending"}],
        "_max_pages": 10,
        "page_size": 10,
    }, prop_ids=post_pids)
    print(f"[notion] posts query: {len(posts)} posts in {int((_time.time()-t1)*1000)}ms", flush=True)

    # Collect campaign IDs and ETM IDs from posts
    post_campaign_map = {}
    post_etm_map = {}
    campaign_ids = set()
    etm_ids = set()
    for page in posts:
        pid = page["id"]
        camp_ids = _extract_relation_ids(page.get("properties", {}), "I.Campaigns")
        if camp_ids:
            post_campaign_map[pid] = camp_ids[0]
            campaign_ids.add(camp_ids[0])
        etm_rel = _extract_relation_ids(page.get("properties", {}), "Experiment Treatment Manager")
        if etm_rel:
            post_etm_map[pid] = etm_rel[0]
            etm_ids.add(etm_rel[0])

    if posts:
        sample_props = list(posts[0].get("properties", {}).keys())
        print(f"[notion] sample post property keys: {sample_props}", flush=True)
    print(f"[notion] {len(campaign_ids)} campaigns, {len(etm_ids)} ETMs to fetch", flush=True)

    # Parallel-fetch campaign details (each takes ~1.3s)
    campaign_cache = {}
    if campaign_ids and NOTION_CAMPAIGNS_DB:
        t2 = _time.time()

        def fetch_campaign(cid):
            qp = [("filter_properties", pid) for pid in (camp_pids or [])]
            try:
                resp = http_requests.get(
                    f"https://api.notion.com/v1/pages/{cid}",
                    headers=_get_notion_headers(), params=qp, timeout=30,
                )
                if resp.status_code != 200:
                    return cid, None
                props = resp.json().get("properties", {})
                return cid, {
                    "name": _extract_title(props, "id"),
                    "influencer_name": str(_extract_formula(props, "Influencer (string)") or ""),
                }
            except Exception:
                return cid, None

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(fetch_campaign, cid): cid for cid in campaign_ids}
            for f in as_completed(futures):
                cid, data = f.result()
                if data:
                    campaign_cache[cid] = data
        print(f"[notion] {len(campaign_cache)} campaigns in {int((_time.time()-t2)*1000)}ms", flush=True)

    # Parallel-fetch ETM pages for brief names (title = treatment brief name)
    etm_cache = {}
    if etm_ids:
        t3 = _time.time()

        def fetch_etm(eid):
            try:
                resp = http_requests.get(
                    f"https://api.notion.com/v1/pages/{eid}",
                    headers=_get_notion_headers(), timeout=30,
                )
                if resp.status_code != 200:
                    return eid, None
                props = resp.json().get("properties", {})
                # ETM title field is "Brief name" (rich_text) or page title
                bn = props.get("Brief name", {})
                brief_name = ""
                if bn.get("type") == "rich_text":
                    rt = bn.get("rich_text") or []
                    brief_name = rt[0]["plain_text"] if rt else ""
                if not brief_name:
                    brief_name = _extract_title(props) or _extract_title(props, "Name")
                print(f"[notion] ETM {eid[:8]} brief_name={brief_name}", flush=True)
                return eid, {"brief_name": brief_name}
            except Exception:
                return eid, None

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(fetch_etm, eid): eid for eid in etm_ids}
            for f in as_completed(futures):
                eid, data = f.result()
                if data:
                    etm_cache[eid] = data
        print(f"[notion] {len(etm_cache)} ETMs in {int((_time.time()-t3)*1000)}ms", flush=True)

    # Compute frame numbers per campaign
    campaign_posts = {}
    for page in posts:
        pid = page["id"]
        cid = post_campaign_map.get(pid)
        if cid:
            post_date = _extract_date(page.get("properties", {}), "Post date")
            campaign_posts.setdefault(cid, []).append((post_date or "", pid))

    frame_numbers = {}
    for cid, post_list in campaign_posts.items():
        post_list.sort(key=lambda x: x[0])
        for i, (_, pid) in enumerate(post_list):
            frame_numbers[pid] = i + 1

    # Build final list
    result = []
    for page in posts:
        pid = page["id"]
        props = page.get("properties", {})
        post_date = _extract_date(props, "Post date")
        cid = post_campaign_map.get(pid)
        campaign = campaign_cache.get(cid, {}) if cid else {}

        # Brief name from ETM, fallback to campaign name
        etm_id = post_etm_map.get(pid)
        etm = etm_cache.get(etm_id, {}) if etm_id else {}
        brief_name = etm.get("brief_name", "") or campaign.get("name", "")

        result.append({
            "influencer_name": campaign.get("influencer_name", ""),
            "post_date": post_date,
            "frame": frame_numbers.get(pid, 1),
            "brief": brief_name,
            "campaign_name": campaign.get("name", ""),
            "brief_link": campaign.get("brief_link", ""),
        })

    print(f"[notion] total: {len(result)} entries in {int((_time.time()-t_start)*1000)}ms", flush=True)
    return result


# Start background prewarm AFTER all functions are defined
threading.Thread(target=_refresh_upcoming_posts, daemon=True).start()


@app.route("/notion/debug", methods=["GET"])
def notion_debug():
    """Debug endpoint — returns cache status (no live Notion calls)."""
    import time
    results = {
        "token_set": bool(NOTION_TOKEN),
        "posts_db": NOTION_POSTS_DB[:8] + "..." if NOTION_POSTS_DB else "",
        "campaigns_db": NOTION_CAMPAIGNS_DB[:8] + "..." if NOTION_CAMPAIGNS_DB else "",
        "cache_has_data": _upcoming_posts_cache["data"] is not None,
        "cache_post_count": len(_upcoming_posts_cache["data"]) if _upcoming_posts_cache["data"] else 0,
        "cache_age_seconds": int(time.time() - _upcoming_posts_cache["fetched_at"]) if _upcoming_posts_cache["fetched_at"] else None,
        "cache_loading": _upcoming_posts_cache.get("loading", False),
        "cache_error": _upcoming_posts_cache.get("error"),
        "posts_prop_ids": _posts_prop_ids,
        "camp_prop_ids": getattr(_resolve_campaign_prop_ids, "_ids", None),
    }
    # Show sample data if cached
    if _upcoming_posts_cache["data"]:
        results["sample"] = _upcoming_posts_cache["data"][:3]
    # Live check: how many properties does the Posts DB schema return?
    try:
        resp = http_requests.get(
            f"https://api.notion.com/v1/databases/{NOTION_POSTS_DB}",
            headers=_get_notion_headers(), timeout=15,
        )
        if resp.status_code == 200:
            db_props = resp.json().get("properties", {})
            results["db_prop_count"] = len(db_props)
            etm_matches = {k: {"type": v.get("type"), "id": v.get("id")}
                           for k, v in db_props.items()
                           if "experiment" in k.lower() or "treatment" in k.lower()}
            results["etm_props"] = etm_matches
    except Exception as e:
        results["db_check_error"] = str(e)
    return jsonify(results)


@app.route("/notion/test-post-page", methods=["GET"])
def test_post_page():
    """Debug: fetch one post WITHOUT filter_properties to see if ETM is there."""
    from datetime import datetime, timedelta
    today = datetime.utcnow().strftime("%Y-%m-%d")
    one_week = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        resp = http_requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_POSTS_DB}/query",
            headers=_get_notion_headers(), timeout=60,
            json={
                "filter": {"and": [
                    {"property": "Post date", "date": {"on_or_after": today}},
                    {"property": "Post date", "date": {"on_or_before": one_week}},
                ]},
                "page_size": 1,
            },
        )
        if resp.status_code != 200:
            return jsonify({"error": resp.status_code, "body": resp.text[:500]}), 500
        results = resp.json().get("results", [])
        if not results:
            return jsonify({"error": "No posts found"}), 404
        props = results[0].get("properties", {})
        etm_keys = {k: str(v)[:200] for k, v in props.items()
                    if "experiment" in k.lower() or "treatment" in k.lower() or "brief" in k.lower()}
        return jsonify({"page_id": results[0]["id"], "total_props": len(props), "etm_related": etm_keys})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notion/upcoming-posts", methods=["GET"])
def upcoming_posts():
    import time

    if not NOTION_TOKEN or not NOTION_POSTS_DB:
        return jsonify({"error": "NOTION_TOKEN and NOTION_POSTS_DB must be configured"}), 500

    now = time.time()
    cache_age = now - _upcoming_posts_cache["fetched_at"]

    # If cache is fresh, return it
    if _upcoming_posts_cache["data"] is not None and cache_age < UPCOMING_POSTS_CACHE_TTL:
        return jsonify({"posts": _upcoming_posts_cache["data"]})

    # If cache is stale (or empty), trigger background refresh
    if not _upcoming_posts_cache.get("loading"):
        threading.Thread(target=_refresh_upcoming_posts, daemon=True).start()

    # Return stale data if we have it (better than nothing)
    if _upcoming_posts_cache["data"] is not None:
        return jsonify({"posts": _upcoming_posts_cache["data"], "stale": True})

    # No data yet — still loading from prewarm
    if _upcoming_posts_cache.get("loading"):
        return jsonify({"posts": [], "loading": True, "message": "Loading posts from Notion..."}), 202

    # Prewarm failed — return the error
    if _upcoming_posts_cache.get("error"):
        return jsonify({"error": _upcoming_posts_cache["error"]}), 500

    return jsonify({"posts": [], "message": "No data available yet"}), 202


@app.route("/notion/refresh", methods=["POST"])
def refresh_posts():
    """Force a background refresh of the posts cache."""
    if _upcoming_posts_cache.get("loading"):
        return jsonify({"ok": True, "message": "Already refreshing"})
    threading.Thread(target=_refresh_upcoming_posts, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})


@app.route("/analyse", methods=["POST"])
def analyse():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY environment variable not set"}), 500

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    image_base64 = data.get("image_base64")
    brief = data.get("brief")
    frame = data.get("frame")
    influencer_name = data.get("influencer_name", "")
    agent_name = data.get("agent_name", "Nous Team")

    if not image_base64:
        return jsonify({"error": "image_base64 is required"}), 400
    if not brief:
        return jsonify({"error": "brief is required"}), 400
    if frame is None:
        return jsonify({"error": "frame is required"}), 400

    # Detect media type — default to JPEG
    media_type = "image/jpeg"
    if image_base64.startswith("iVBOR"):
        media_type = "image/png"
    elif image_base64.startswith("/9j/"):
        media_type = "image/jpeg"
    elif image_base64.startswith("R0lGOD"):
        media_type = "image/gif"
    elif image_base64.startswith("UklGR"):
        media_type = "image/webp"

    prompt_text = build_prompt(brief, frame, influencer_name, agent_name)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt_text,
                        },
                    ],
                }
            ],
        )

        raw_text = message.content[0].text.strip()

        # Strip markdown code fences if present
        if "```" in raw_text:
            import re as _re
            fence_match = _re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw_text, _re.DOTALL)
            if fence_match:
                raw_text = fence_match.group(1).strip()

        # Try direct parse first
        try:
            result = json.loads(raw_text)
            return jsonify(result)
        except json.JSONDecodeError:
            pass

        # Fallback: extract the first JSON object from the response
        first_brace = raw_text.find("{")
        last_brace = raw_text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_substr = raw_text[first_brace:last_brace + 1]
            result = json.loads(json_substr)
            return jsonify(result)

        # If we get here, truly no JSON found
        raise json.JSONDecodeError("No JSON object found in response", raw_text, 0)

    except json.JSONDecodeError as e:
        return jsonify({
            "error": "Claude returned non-JSON response",
            "raw": raw_text if "raw_text" in dir() else "",
            "detail": str(e),
        }), 502
    except anthropic.APIStatusError as e:
        return jsonify({
            "error": "Anthropic API error",
            "status_code": e.status_code,
            "detail": str(e.message),
        }), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "error": "Internal server error",
            "detail": str(e),
        }), 500


RATING_MAP = {"good": "Accurate", "bad": "Off", "mixed": "Partially"}


def notion_headers():
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_API_KEY') or os.environ.get('NOTION_TOKEN') or NOTION_TOKEN or ''}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def _notion_rich_text(text):
    if not text:
        return []
    return [{"text": {"content": str(text)[:2000]}}]


@app.route("/feedback", methods=["POST"])
def post_feedback():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    token = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN") or NOTION_TOKEN or ""
    if not token:
        return jsonify({"error": "NOTION_TOKEN not configured"}), 500

    rating_raw = data.get("rating", "")
    rating_select = RATING_MAP.get(rating_raw, rating_raw)

    ai_improvements = data.get("ai_improvements", [])
    if isinstance(ai_improvements, list):
        ai_improvements = "\n".join(ai_improvements)

    timestamp = data.get("timestamp", "")

    properties = {
        "Name": {"title": _notion_rich_text(f"{data.get('influencer', 'Unknown')} - Frame {data.get('frame', '?')}")},
        "Reviewer": {"rich_text": _notion_rich_text(data.get("reviewer", ""))},
        "Influencer": {"rich_text": _notion_rich_text(data.get("influencer", ""))},
        "Brief": {"rich_text": _notion_rich_text(data.get("brief", ""))},
        "Frame": {"rich_text": _notion_rich_text(str(data.get("frame", "")))},
        "Comment": {"rich_text": _notion_rich_text(data.get("comment", ""))},
        "AI Improvements": {"rich_text": _notion_rich_text(ai_improvements)},
    }

    if rating_select in ("Accurate", "Partially", "Off"):
        properties["Rating"] = {"select": {"name": rating_select}}

    ai_verdict = data.get("ai_verdict", "")
    if ai_verdict in ("good_to_go", "needs_work"):
        properties["AI Verdict"] = {"select": {"name": ai_verdict}}

    if timestamp:
        try:
            properties["Date"] = {"date": {"start": timestamp[:10]}}
        except Exception:
            pass

    resp = http_requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers(),
        json={"parent": {"database_id": NOTION_FEEDBACK_DB}, "properties": properties},
    )

    if resp.status_code == 200:
        return jsonify({"ok": True})
    else:
        print(f"[FEEDBACK] Notion API error {resp.status_code}: {resp.text}")
        return jsonify({"ok": False, "error": f"Notion API error {resp.status_code}: {resp.text}"}), 500


@app.route("/feedback", methods=["GET"])
def get_feedback():
    token = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN") or NOTION_TOKEN or ""
    if not token:
        return jsonify({"error": "NOTION_TOKEN not configured"}), 500

    # Build Notion filter from query params
    filters = []
    for param, prop in [("influencer", "Influencer"), ("brief", "Brief"), ("reviewer", "Reviewer")]:
        val = request.args.get(param, "")
        if val:
            filters.append({"property": prop, "rich_text": {"contains": val}})
    for param, prop in [("rating", "Rating"), ("verdict", "AI Verdict")]:
        val = request.args.get(param, "")
        if val:
            filters.append({"property": prop, "select": {"equals": val}})

    body = {}
    if filters:
        body["filter"] = {"and": filters} if len(filters) > 1 else filters[0]
    body["sorts"] = [{"property": "Date", "direction": "descending"}]

    resp = http_requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_FEEDBACK_DB}/query",
        headers=notion_headers(),
        json=body,
    )

    if resp.status_code != 200:
        return jsonify({"error": f"Notion query failed: {resp.text}"}), 500

    entries = []
    for page in resp.json().get("results", []):
        props = page.get("properties", {})
        def get_text(name):
            p = props.get(name, {})
            rt = p.get("rich_text") or p.get("title") or []
            return rt[0]["plain_text"] if rt else ""
        def get_select(name):
            s = props.get(name, {}).get("select")
            return s["name"] if s else ""
        def get_date(name):
            d = props.get(name, {}).get("date")
            return d["start"] if d else ""

        entries.append({
            "timestamp": get_date("Date"),
            "reviewer": get_text("Reviewer"),
            "influencer": get_text("Influencer"),
            "brief": get_text("Brief"),
            "frame": get_text("Frame"),
            "rating": get_select("Rating"),
            "comment": get_text("Comment"),
            "ai_verdict": get_select("AI Verdict"),
            "ai_improvements": get_text("AI Improvements"),
        })

    return jsonify({"feedback": entries, "total": len(entries)})


def resolve_slack_channel_id(channel_name, headers):
    """Resolve a channel name (e.g. #approving-content) to a Slack channel ID."""
    clean = channel_name.lstrip("#")
    # Try conversations.list to find the channel
    cursor = None
    for _ in range(5):  # max 5 pages
        params = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = http_requests.get(
            "https://slack.com/api/conversations.list",
            headers=headers,
            params=params,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return None
        for ch in data.get("channels", []):
            if ch.get("name") == clean:
                return ch["id"]
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None


# Cache resolved channel ID
_slack_channel_id_cache = {}


@app.route("/slack", methods=["POST"])
def send_to_slack():
    if not SLACK_BOT_TOKEN:
        return jsonify({"error": "SLACK_BOT_TOKEN not configured"}), 500

    data = request.get_json(force=True, silent=True)
    if not data or not data.get("text"):
        return jsonify({"error": "text is required"}), 400

    slack_headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    image_b64 = data.get("image_base64")

    # If image included, post image first then text as threaded reply
    if image_b64:
        try:
            image_bytes = base64.b64decode(image_b64)
            filename = data.get("filename", "story.jpg")
            caption = data.get("caption", "")

            # Resolve channel ID (needed for file uploads)
            channel = SLACK_APPROVING_CONTENT_CHANNEL
            if not channel.startswith("C"):
                if channel not in _slack_channel_id_cache:
                    resolved = resolve_slack_channel_id(channel, slack_headers)
                    if resolved:
                        _slack_channel_id_cache[channel] = resolved
                channel = _slack_channel_id_cache.get(channel, channel)

            # Step 1: Resolve channel name → real ID by posting + deleting a temp message
            resolve_resp = http_requests.post(
                "https://slack.com/api/chat.postMessage",
                headers=slack_headers,
                json={"channel": channel, "text": "."},
                timeout=10,
            )
            resolve_data = resolve_resp.json()
            if not resolve_data.get("ok"):
                return jsonify({"error": f"Slack channel resolve failed: {resolve_data.get('error')}"}), 502

            channel_id = resolve_data.get("channel", channel)
            temp_ts = resolve_data.get("ts", "")

            # Delete the temp message
            if temp_ts:
                http_requests.post(
                    "https://slack.com/api/chat.delete",
                    headers=slack_headers,
                    json={"channel": channel_id, "ts": temp_ts},
                    timeout=5,
                )

            # Step 2: Get upload URL for image
            url_resp = http_requests.get(
                "https://slack.com/api/files.getUploadURLExternal",
                headers=slack_headers,
                params={"filename": filename, "length": len(image_bytes)},
                timeout=10,
            )
            url_data = url_resp.json()
            if not url_data.get("ok"):
                return jsonify({"error": f"Upload URL failed: {url_data.get('error')}"}), 502

            # Step 3: PUT the file bytes
            http_requests.post(
                url_data["upload_url"],
                files={"file": (filename, image_bytes, "image/jpeg")},
                timeout=30,
            )

            # Step 4: Complete upload — image + caption as main channel post
            complete_resp = http_requests.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers={**slack_headers, "Content-Type": "application/json"},
                json={
                    "files": [{"id": url_data["file_id"], "title": filename}],
                    "channel_id": channel_id,
                    "initial_comment": caption or "",
                },
                timeout=10,
            )
            complete_data = complete_resp.json()
            if not complete_data.get("ok"):
                return jsonify({"error": f"Image share failed: {complete_data.get('error')}"}), 502

            # Step 5: Get the file share message ts for threading the analysis
            import time
            time.sleep(1)
            info_resp = http_requests.get(
                "https://slack.com/api/files.info",
                headers=slack_headers,
                params={"file": url_data["file_id"]},
                timeout=10,
            )
            file_ts = ""
            info_data = info_resp.json()
            if info_data.get("ok"):
                shares = info_data.get("file", {}).get("shares", {})
                for share_type in shares.values():
                    for ch_shares in share_type.values():
                        if ch_shares:
                            file_ts = ch_shares[0].get("ts", "")
                            break
                    if file_ts:
                        break

            # Step 6: Post the bullet analysis as a thread reply
            if file_ts:
                http_requests.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=slack_headers,
                    json={
                        "channel": channel_id,
                        "text": data["text"],
                        "thread_ts": file_ts,
                    },
                    timeout=10,
                )
            else:
                http_requests.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=slack_headers,
                    json={"channel": channel_id, "text": data["text"]},
                    timeout=10,
                )

            return jsonify({"ok": True})

        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": f"Image upload failed: {str(e)}"}), 500

    # No image — simple text message
    resp = http_requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=slack_headers,
        json={
            "channel": SLACK_APPROVING_CONTENT_CHANNEL,
            "text": data["text"],
        },
        timeout=10,
    )

    result = resp.json()
    if result.get("ok"):
        return jsonify({"ok": True})
    else:
        return jsonify({"error": result.get("error", "Unknown Slack error")}), 502


if __name__ == "__main__":
    print("Nous Story Analyser running on http://localhost:8765")
    app.run(host="0.0.0.0", port=8765, debug=False)

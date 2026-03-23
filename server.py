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

"obvious_tweaks" covers visual/technical issues: text readability, button placement, button text, CTA visibility, image quality, AD label placement, font size, contrast, text layout.

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
   b. Button text is "Save with Nous" (or "Start saving here!" for Fashion Frame 2 only)
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
   c. AD label is placed below the product shot, not covering the image

10. Text readability [ALL FRAMES]
    a. Font is large enough to read comfortably on a phone screen
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
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines).strip()

        result = json.loads(raw_text)
        return jsonify(result)

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


FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "data", "feedback.json")


def load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE) as f:
            return json.load(f)
    return []


def save_feedback(entries):
    os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
    with open(FEEDBACK_FILE, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


@app.route("/feedback", methods=["POST"])
def post_feedback():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    entry = {
        "timestamp": data.get("timestamp", ""),
        "reviewer": data.get("reviewer", ""),
        "influencer": data.get("influencer", ""),
        "brief": data.get("brief", ""),
        "frame": data.get("frame", ""),
        "rating": data.get("rating", ""),           # "good", "bad", "mixed"
        "comment": data.get("comment", ""),
        "ai_verdict": data.get("ai_verdict", ""),    # "good_to_go" or "needs_work"
        "ai_improvements": data.get("ai_improvements", []),
    }

    entries = load_feedback()
    entries.append(entry)
    save_feedback(entries)

    return jsonify({"ok": True, "total": len(entries)})


@app.route("/feedback", methods=["GET"])
def get_feedback():
    entries = load_feedback()
    # Optional query filters
    influencer = request.args.get("influencer", "").lower()
    brief = request.args.get("brief", "").lower()
    rating = request.args.get("rating", "").lower()
    reviewer = request.args.get("reviewer", "").lower()
    verdict = request.args.get("verdict", "").lower()

    if influencer:
        entries = [e for e in entries if influencer in e.get("influencer", "").lower()]
    if brief:
        entries = [e for e in entries if brief in e.get("brief", "").lower()]
    if rating:
        entries = [e for e in entries if e.get("rating", "").lower() == rating]
    if reviewer:
        entries = [e for e in entries if reviewer in e.get("reviewer", "").lower()]
    if verdict:
        entries = [e for e in entries if e.get("ai_verdict", "").lower() == verdict]

    return jsonify({"feedback": entries, "total": len(entries)})


@app.route("/slack", methods=["POST"])
def send_to_slack():
    if not SLACK_BOT_TOKEN:
        return jsonify({"error": "SLACK_BOT_TOKEN not configured"}), 500

    data = request.get_json(force=True, silent=True)
    if not data or not data.get("text"):
        return jsonify({"error": "text is required"}), 400

    resp = http_requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
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

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
    {"brief": "Home Brief 1", "frames": [1, 2, 3]},
    {"brief": "Home Brief 2", "frames": [1, 2, 3]},
    {"brief": "Celebrity Brief 1", "frames": [1, 2, 3]},
    {"brief": "Celebrity Brief 2", "frames": [1, 2, 3]},
    {"brief": "Fashion Brief 1", "frames": [1, 2, 3]},
    {"brief": "Fashion Brief 2", "frames": [1, 2, 3]},
    {"brief": "Lifestyle Brief 1", "frames": [1, 2, 3]},
]

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
    return CRITERIA_PROMPT.format(
        brief=brief,
        frame=frame,
        influencer_name=influencer_name or "the influencer",
        agent_name=agent_name or "Nous Team",
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

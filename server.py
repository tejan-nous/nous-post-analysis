"""
Nous Story Analyser — Flask server for analysing Instagram Story images.
Endpoint: POST /analyse
"""

import os
import json
import base64
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic

app = Flask(__name__)
CORS(app, origins=["https://tejan-nous.github.io", "http://localhost:8080", "http://127.0.0.1:8080", "null"])

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ACCESS_PASSWORD = os.environ.get("ACCESS_PASSWORD", "")

BRIEFS = [
    {"brief": "👗 Fashion Exp: Secret first", "frames": [1, 2, 3]},
    {"brief": "💃 Fashion Exp: Girl Maths first", "frames": [1, 2, 3]},
    {"brief": "⭐ Celebrity Exp: Hate Waste first", "frames": [1, 2, 3]},
    {"brief": "👑 Celebrity Exp: Supermom first", "frames": [1, 2, 3]},
    {"brief": "🏠 Home Exp: Bargain first", "frames": [1, 2, 3]},
    {"brief": "🏡 Home Exp: Must Have first", "frames": [1, 2, 3]},
    {"brief": "🔄 Repeat Exp: OG brief (post level)", "frames": [1, 2, 3]},
    {"brief": "📊 Repeat Exp: Startling Stats", "frames": [1, 2, 3]},
    {"brief": "👨‍👩‍👧 New Family Exp: Identical message", "frames": [1, 2, 3]},
    {"brief": "📏 Message Length Exp: Short first", "frames": [1, 2, 3]},
    {"brief": "📝 Message Length Exp: Long first", "frames": [1, 2, 3]},
    {"brief": "📬 New Visuals: Follower DM first", "frames": [1, 2, 3]},
    {"brief": "⬜ New Visuals: Blank background first", "frames": [1, 2, 3]},
    {"brief": "🌿 Lifestyle Exp: Life Lesson first", "frames": [1, 2, 3]},
    {"brief": "📱 Lifestyle Exp: Mobile Savings first", "frames": [1, 2, 3]},
    {"brief": "👨‍👩‍👧‍👦 Family: No experiment", "frames": [1, 2, 3]},
]

SYSTEM_PROMPT = """You are a content quality reviewer for Nous, a UK utility-switching service that saves users £500+ by switching energy, broadband, and mortgage providers.

You review Instagram Story images posted by influencers promoting Nous. Your job is to assess each story frame and return structured feedback in two categories: obvious formatting issues, and brief compliance.

You MUST respond ONLY with valid JSON — no preamble, no markdown, no explanation outside the JSON object. Your entire response must be parseable by Python's json.loads().

The JSON structure must be exactly:
{
  "overall": "good_to_go" or "needs_work",
  "obvious_tweaks": [
    {
      "label": "<what the issue is>",
      "pass": <true or false>,
      "note": "<one specific observation from the image>"
    }
  ],
  "brief_fit": [
    {
      "label": "<what the issue is>",
      "pass": <true or false>,
      "note": "<one specific observation from the image>"
    }
  ],
  "email_influencer": "",
  "email_agent": "<email text>"
}

Use "good_to_go" when ALL obvious_tweaks pass AND no more than 1 brief_fit item fails. Otherwise "needs_work".
Keep obvious_tweaks to the 4-5 hard formatting rules. Keep brief_fit to the top 3 things that matter most for this brief and frame — do not list everything, only the most important.
"""

CRITERIA_PROMPT = """
Review this Instagram Story image. Return feedback in two categories:

─── CATEGORY 1: OBVIOUS TWEAKS ───
These are hard formatting rules. Check all of them:

1. Text is broken into multiple short blocks — NOT one long paragraph of copy
2. "Save with Nous" CTA button is at the BOTTOM of the story
3. No "AD" text inside the button itself (AD label can appear elsewhere on the story)
4. @get_nous tag is present in the body (skip this check if this is a Fashion Secret/Teaser frame)
5. No competing poll/vote sticker that distracts from the CTA

─── CATEGORY 2: FITTING THE BRIEF ───
Context:
- Brief: {brief}
- Frame: {frame}
- Influencer: {influencer_name}

For this specific brief and frame, identify the TOP 3 most important things to check — things that are core to whether this post is actually following the brief. Do not try to check everything. Focus on what matters most.

Key rules to draw from (use your judgement on which 3 apply most to this brief/frame):
- Hook must open with a personal problem, confession, or shock stat — NEVER with @get_nous or "I've been using Nous"
- No "loads of people posting about Nous" or herd-following language
- Must include a specific £ figure (personal saving OR approved stat: £781/yr, £250 energy, £7/mo phone, £500+) — no vague "save loads"
- No claim Nous finds the cheapest deal on the whole market
- No claim Nous reminds you when contracts end
- Discovery should feel natural and organic ("I stumbled across this tool"), not scripted
- Nous referred to as a "tool" not a "company"
- Sign-up described as quick and free (2 minutes, zero effort)
- Copy and tone match the brief pattern (e.g. Girl Maths logic, Secret/mystery reveal, Startling stat opener, etc.)

─── EMAIL ───
Write ONE short email to the agent. Set "email_influencer" to "".
Opening: "Hi {agent_name}," (or "Hi," if no agent name)

If overall = "good_to_go": one warm sentence, e.g. "Hi {agent_name}, looks good — happy for this to go live. Best, {reviewer_name}"

If overall = "needs_work": one warm opening sentence (acknowledge something that works). Then bullet point only the FAILING items using • — be specific and actionable. Ask agent to pass to {influencer_name}.
{review_sign_off}

Always reference {influencer_name}, {brief} and Frame {frame}.

Now return ONLY the JSON object.
"""


def build_prompt(brief, frame, influencer_name, agent_name, reviewer_name="Bekki", first_review=True):
    sign_off = (
        f'End with: "Once {influencer_name or "they"} has made the changes, please send back for a final review before it goes live. Best, {reviewer_name or "Bekki"}"'
        if first_review else
        f'End "Best, {reviewer_name or "Bekki"}"'
    )
    return CRITERIA_PROMPT.format(
        brief=brief,
        frame=frame,
        influencer_name=influencer_name or "the influencer",
        agent_name=agent_name or "",
        reviewer_name=reviewer_name or "Bekki",
        review_sign_off=sign_off,
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

    if ACCESS_PASSWORD:
        auth = request.headers.get("X-Access-Password", "")
        if auth != ACCESS_PASSWORD:
            return jsonify({"error": "Unauthorised"}), 401

    image_base64 = data.get("image_base64")
    brief = data.get("brief")
    frame = data.get("frame")
    influencer_name = data.get("influencer_name", "")
    agent_name = data.get("agent_name", "")
    reviewer_name = data.get("reviewer_name", "Bekki")
    first_review = data.get("first_review", True)

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

    prompt_text = build_prompt(brief, frame, influencer_name, agent_name, reviewer_name, first_review)

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"Nous Story Analyser running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

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
]

SYSTEM_PROMPT = """You are a content quality reviewer for Nous, a UK utility-switching service that saves users £500+ by switching energy, broadband, and mortgage providers.

You review Instagram Story images posted by influencers promoting Nous. Your job is to assess each story frame against a strict checklist and return structured feedback.

You MUST respond ONLY with valid JSON — no preamble, no markdown, no explanation outside the JSON object. Your entire response must be parseable by Python's json.loads().

The JSON structure must be exactly:
{
  "overall": "good_to_go" or "needs_work",
  "score": <integer, number of criteria that pass>,
  "total": <integer, total number of criteria checked>,
  "criteria": [
    {
      "label": "<parent criterion name>",
      "sub_label": "<specific sub-criterion>",
      "pass": <true or false>,
      "note": "<brief specific observation about this image, 1-2 sentences>"
    },
    ...
  ],
  "summary": "<2-3 sentence summary of overall quality, referencing specific details visible in the image>",
  "email_influencer": "<full email text to send to the influencer>",
  "email_agent": "<full email text to send to the talent agent>"
}

Use "good_to_go" when score >= 80% of total criteria pass. Otherwise use "needs_work".
"""

CRITERIA_PROMPT = """
Evaluate this Instagram Story image against the following criteria. For each sub-criterion, determine pass (true) or fail (false) based only on what is visible in the image.

CRITERIA TO CHECK:

1. Problem-aware hook
   a. Doesn't open with brand name or product (@get_nous / "I've been using Nous")
   b. Opens with a personal problem, confession, or shock stat
   c. No "loads of people posting about Nous" or herd-following language
   d. Tone is confessional or self-deprecating, not corporate
   e. No unproven enthusiasm ("excited to see what we can save")

2. Discovery moment
   a. Discovery feels natural, not scripted ("I stumbled across", not "Nous asked me")
   b. Nous called a "tool", not a "company"
   c. No "I promise it's totally legit" disclaimer
   d. Creator has clearly signed up and used Nous themselves

3. What Nous does
   a. Mentions switching across energy, broadband and phone/mobile
   b. No claim that Nous finds the cheapest deal on the whole market
   c. No claim that Nous reminds you when contracts end
   d. Makes clear Nous handles the switching (zero effort for user)

4. Savings claim
   a. Specific £ figure mentioned (personal saving OR approved stat: £781/yr, £250 energy, £7/mo phone, £500+)
   b. No vague language like "save loads" or "could save you money"
   c. No "cheapest deal" claim

5. Sign-up ease
   a. Mentions how quick it is ("2 minutes", "from my phone in like five minutes")
   b. Mentions Nous is free
   c. References "just a few quick questions" or minimal effort

6. @get_nous tag
   a. @get_nous appears in body text (unless this is a Fashion Secret/Teaser frame)
   b. @get_nous does not appear in the opening line

7. Save with Nous CTA
   a. CTA button is present
   b. Button text is "Save with Nous" (or "Start saving here!" for Fashion Frame 2 only)
   c. No "AD" text inside the button itself
   d. Link/chain emoji (🔗) present alongside CTA
   e. Button text is readable — good contrast, not too small
   f. No vote/poll sticker on the story that competes with the CTA

8. Button placement
   a. CTA button is at the BOTTOM of the story
   b. Button is not obscured by AD label, stickers or text blocks
   c. Only one CTA button — no competing links

9. Visual type & effectiveness
   a. Image type suits the brief — people/faces tend to drive stronger engagement than empty rooms or product shots alone
   b. Visual is appropriate to the niche — e.g. family lifestyle, home interior, fashion haul, celebrity setting
   c. AD label is placed below the product shot, not covering the image

10. Text readability
    a. Font is large enough to read comfortably on a phone screen
    b. Text colour has strong contrast against the background
    c. Long copy is broken into multiple text blocks, not a single wall of text
    d. Text is not placed over faces or focal points of the image

Context:
- Brief: {brief}
- Frame number: {frame}
- Influencer: {influencer_name}
- Agent (email recipient): {agent_name}
- Reviewer signing off as: {reviewer_name}

Write ONE short email addressed to the agent. Set "email_influencer" to an empty string.

Opening: "Hi {agent_name}," (if no agent name provided, just "Hi,")

If overall = "good_to_go":
  One warm sentence. E.g. "Hi {agent_name}, looks good — happy for this to go live. Best, {reviewer_name}"

If overall = "needs_work":
  One warm opening sentence (something specific that works). Then 2-3 bullet point changes using • character, each one specific and actionable. Ask the agent to pass to {influencer_name}.
  {review_sign_off}

Always reference {influencer_name}, {brief} and Frame {frame}.

Now evaluate the image and return ONLY the JSON object described above.
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

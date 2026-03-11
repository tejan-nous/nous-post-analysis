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
CORS(app)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

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

9. Calming/lifestyle visual
   a. Visual is calming — not busy, cluttered or high-contrast
   b. Visual matches niche expectation for this frame (home interior, lifestyle shot, etc.)
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
- Reviewer signing off as: {agent_name}

Email guidance:

If overall = "needs_work":
  - Influencer email: Warm and friendly. Start with what's good. List 2-3 specific changes needed based on failed criteria. Sign off as {agent_name}. Tone: collaborative, not critical.
  - Agent email: Professional and concise. Bullet-pointed changes needed. State which criteria failed and why. Ask agent to pass feedback to influencer. Signed {agent_name}.

If overall = "good_to_go":
  - Influencer email: Short and enthusiastic. "Just reviewed your post and it looks great! Happy for this to go live." Signed {agent_name}.
  - Agent email: "Reviewed {influencer_name}'s post — all looks good. Happy to approve." Signed {agent_name}.

In both emails, reference the specific brief ({brief}, Frame {frame}) and the influencer's name ({influencer_name}).

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


if __name__ == "__main__":
    print("Nous Story Analyser running on http://localhost:8765")
    app.run(host="0.0.0.0", port=8765, debug=False)

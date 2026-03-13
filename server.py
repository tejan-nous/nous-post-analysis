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
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "C0A9VNNMTU7")  # #approving-content
NOTION_TOKEN = os.environ.get("NOTION_API_KEY", "")
NOTION_INFLUENCERS_DB = "1f8e4fd0-8136-8094-b03d-fffe5b42de1a"  # I.Influencers

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

You review Instagram Story images posted by influencers promoting Nous. Your job is to assess each story frame and return structured feedback.

You MUST respond ONLY with valid JSON — no preamble, no markdown, no explanation outside the JSON object. Your entire response must be parseable by Python's json.loads().

The JSON structure must be exactly:
{
  "overall": "good_to_go" or "needs_work",
  "tweaks": [
    {
      "label": "<short actionable fix>",
      "note": "<one specific observation — what to change and why>"
    }
  ],
  "strengths": [
    {
      "label": "<what works well>",
      "note": "<brief positive observation>"
    }
  ],
  "slack_bullets": ["<short punchy bullet>", "<another bullet>"],
  "email_agent": "<email text>"
}

Rules:
- "tweaks" = everything that needs fixing (formatting AND brief compliance). Only list things that FAIL. Be specific and actionable.
- "strengths" = what this post does well. 2-4 genuine positives.
- "slack_bullets" = ultra-short bullet points for Slack. Each bullet is a quick instruction like "Break up text" or "Change X to Y". No full sentences — just the action. Max 4-5 bullets. If good_to_go, just ["Looks good — happy for this to go live"].
- Use "good_to_go" when there are 0-1 minor tweaks. Use "needs_work" when there are real issues.
"""

CRITERIA_PROMPT = """
Review this Instagram Story image.

Context:
- Brief: {brief}
- Frame: {frame}
- Influencer: {influencer_name}

─── CHECK THESE (only add to "tweaks" if they FAIL) ───

Formatting rules:
1. Text is broken into multiple short blocks — NOT one long paragraph of copy
2. "Save with Nous" CTA button is at the BOTTOM of the story
3. No "AD" text inside the button itself (AD label can appear elsewhere on the story)
4. @get_nous tag is present in the body (skip this check if this is a Fashion Secret/Teaser frame)
5. No competing poll/vote sticker that distracts from the CTA

Brief compliance rules (use your judgement on which apply most to this brief/frame):
- Hook must open with a personal problem, confession, or shock stat — NEVER with @get_nous or "I've been using Nous"
- No "loads of people posting about Nous" or herd-following language
- Must include a specific £ figure (personal saving OR approved stat: £781/yr, £250 energy, £7/mo phone, £500+) — no vague "save loads"
- No claim Nous finds the cheapest deal on the whole market
- No claim Nous reminds you when contracts end
- Discovery should feel natural and organic ("I stumbled across this tool"), not scripted
- Nous referred to as a "tool" not a "company"
- Sign-up described as quick and free (2 minutes, zero effort)
- Copy and tone match the brief pattern (e.g. Girl Maths logic, Secret/mystery reveal, Startling stat opener, etc.)

─── STRENGTHS ───
Note 2-4 genuine things this post does well — tone, layout, specific wording, etc.

─── SLACK BULLETS ───
Write ultra-short action bullets for a Slack message. Think of how a reviewer would quickly jot down feedback:
  "Break up text"
  "Change sentence one to be: 'with the way the world is going...'"
  "Move CTA button to bottom"
Each bullet = one quick instruction, not a full explanation. If good_to_go, just: "Looks good — happy for this to go live"

─── EMAIL ───
Write ONE short email to the agent.
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


@app.route("/lookup", methods=["GET"])
def lookup():
    notion_token = os.environ.get("NOTION_API_KEY", "") or NOTION_TOKEN
    if not notion_token:
        return jsonify({"error": "NOTION_API_KEY not configured"}), 500

    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"results": []})

    import requests as http_requests

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    # Search I.Influencers by name (contains filter)
    body = {
        "database_id": NOTION_INFLUENCERS_DB,
        "filter": {
            "property": "Name",
            "title": {"contains": q},
        },
        "page_size": 10,
    }

    try:
        resp = http_requests.post(
            "https://api.notion.com/v1/databases/" + NOTION_INFLUENCERS_DB + "/query",
            headers=headers,
            json=body,
        )
        data = resp.json()
        if not data.get("results"):
            return jsonify({"results": []})

        results = []
        for page in data["results"]:
            props = page.get("properties", {})

            def get_title(p):
                t = props.get(p, {}).get("title", [])
                return t[0]["plain_text"] if t else ""

            def get_text(p):
                rt = props.get(p, {}).get("rich_text", [])
                return rt[0]["plain_text"] if rt else ""

            def get_email(p):
                return props.get(p, {}).get("email", "") or get_text(p)

            results.append({
                "name": get_title("Name"),
                "agency": get_text("Agency"),
                "agent_email": get_email("Agent email"),
                "influencer_email": get_email("Influencer email"),
                "url": page.get("url", ""),
            })

        return jsonify({"results": results})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/slack", methods=["POST"])
def send_to_slack():
    if ACCESS_PASSWORD:
        auth = request.headers.get("X-Access-Password", "")
        if auth != ACCESS_PASSWORD:
            return jsonify({"error": "Unauthorised"}), 401

    # Read at request time so Railway env var changes take effect without redeploy
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "") or SLACK_TOKEN
    if not slack_token:
        return jsonify({"error": "SLACK_BOT_TOKEN not configured on the server"}), 500

    channel = os.environ.get("SLACK_CHANNEL_ID", "") or SLACK_CHANNEL
    if not channel:
        return jsonify({"error": "SLACK_CHANNEL_ID not configured on the server"}), 500

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    message_text = data.get("message", "")
    image_base64 = data.get("image_base64", "")
    filename = data.get("filename", "story.jpg")

    if not message_text:
        return jsonify({"error": "message is required"}), 400

    import requests as http_requests

    headers = {"Authorization": f"Bearer {slack_token}"}

    try:
        # If image provided, upload it first then post with the file
        if image_base64:
            image_bytes = base64.b64decode(image_base64)

            # Step 1: Get upload URL
            upload_resp = http_requests.post(
                "https://slack.com/api/files.getUploadURLExternal",
                headers=headers,
                data={"filename": filename, "length": len(image_bytes)},
            )
            upload_data = upload_resp.json()
            if not upload_data.get("ok"):
                return jsonify({"error": "Slack upload URL failed", "detail": upload_data.get("error", "")}), 502

            # Step 2: Upload the file
            http_requests.post(
                upload_data["upload_url"],
                files={"file": (filename, image_bytes)},
            )

            # Step 3: Complete upload with channel + message
            complete_resp = http_requests.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "files": [{"id": upload_data["file_id"]}],
                    "channel_id": channel,
                    "initial_comment": message_text,
                },
            )
            complete_data = complete_resp.json()
            if not complete_data.get("ok"):
                return jsonify({"error": "Slack complete upload failed", "detail": complete_data.get("error", "")}), 502

            return jsonify({"ok": True, "method": "file_upload"})

        else:
            # Text-only message
            resp = http_requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={**headers, "Content-Type": "application/json"},
                json={"channel": channel, "text": message_text},
            )
            resp_data = resp.json()
            if not resp_data.get("ok"):
                return jsonify({"error": "Slack post failed", "detail": resp_data.get("error", "")}), 502

            return jsonify({"ok": True, "method": "chat_post"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Slack send failed", "detail": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    print(f"Nous Story Analyser running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

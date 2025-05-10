import os
import json
import boto3

s3      = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

# top-level folder under which we’ll dump our analyses
ANALYSIS_SUBFOLDER = os.getenv("ANALYSIS_SUBFOLDER", "analysis")

def lambda_handler(event, context):
    rec    = event["Records"][0]["s3"]
    bucket = rec["bucket"]["name"]
    key    = rec["object"]["key"]               # e.g. "meetings/foo.vtt"

    # ─── 0️⃣ Prevent infinite loop: skip anything in the analysis subfolder ───
    if f"/{ANALYSIS_SUBFOLDER}/" in key:
        return {"status": "skipped", "reason": f"ignore files in {ANALYSIS_SUBFOLDER}/"}

    # ─── 1️⃣ only .vtt or .json files ───
    base_key, ext = os.path.splitext(key)
    ext = ext.lower()
    if ext not in (".vtt", ".json"):
        return {"status":"skipped", "reason": f"{ext} not supported"}

    # ─── 2️⃣ derive a clean video name + analysis filename ───
    #    e.g. key="meetings/foo.vtt" → videoname="foo", analysis_videoname="foo-feedback.json"
    videoname         = os.path.splitext(os.path.basename(key))[0]
    analysis_videoname = f"{videoname}-feedback.json"

    # ─── 3️⃣ load transcript (.vtt) if present, or peer .vtt if this is .json ───
    transcript = ""
    if ext == ".vtt":
        transcript = _get_s3_text(bucket, key)
    else:
        try:
            transcript = _get_s3_text(bucket, base_key + ".vtt")
        except s3.exceptions.NoSuchKey:
            pass

    # ─── 4️⃣ load stats (.json) if present, or peer .json if this is .vtt ───
    stats_json = ""
    if ext == ".json":
        stats_json = _get_s3_text(bucket, key)
    else:
        try:
            stats_json = _get_s3_text(bucket, base_key + ".json")
        except s3.exceptions.NoSuchKey:
            pass

    if not (transcript or stats_json):
        return {"status":"skipped", "reason":"no transcript or stats found"}

    # ─── 5️⃣ build the prompt ───
    prompt_parts = []
    if transcript:
        prompt_parts.append("Here is the meeting transcript:\n" + transcript)
    if stats_json:
        prompt_parts.append("Here are the engagement stats:\n" + stats_json)
    prompt_body = "\n\n".join(prompt_parts)

    human_assistant_dialog = (
        f"Human: {prompt_body}\n\n"
        "Assistant: Please analyze speaker engagement, note interruptions, and "
        "summarize key takeaways. Also provide feedback on each speaker’s "
        "clarity, confidence, engagement, and delivery in JSON format."
    )

    # ─── 6️⃣ invoke Haiku ───
    payload = {
        "prompt": human_assistant_dialog,
        "max_tokens_to_sample": 800,
        "temperature": 0.3,
        "stop_sequences": ["\n\nHuman:"]
    }
    resp = bedrock.invoke_model(
        body=json.dumps(payload).encode("utf-8"),
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        contentType="application/json",
        accept="application/json"
    )
    analysis = json.loads(resp["body"].read())["completion"].strip()

    # ─── 7️⃣ write back as JSON under analysis/ using our analysis_videoname ───
    folder  = os.path.dirname(base_key)  # e.g. "meetings"
    out_key = f"{folder}/{ANALYSIS_SUBFOLDER}/{analysis_videoname}"

    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=analysis.encode("utf-8"),
        ContentType="application/json"
    )

    return {"status":"OK", "analysis_key": out_key}


def _get_s3_text(bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8")

import os
import boto3
import re
import pandas as pd
import json
from datetime import timedelta

TIME_RE   = re.compile(r'^\d\d:\d\d:\d\d\.\d{3}\s+-->\s+\d\d:\d\d:\d\d\.\d{3}$')
V_TAG_RE  = re.compile(r'<v\s+([^>]+?)>', flags=re.I)
NOTE_RE   = re.compile(r'^NOTE\b', flags=re.I)

def parse_vtt(path: str) -> pd.DataFrame:
    rows, start, end, text_lines = [], None, None, []

    with open(path, encoding='utf-8') as f:
        for raw_line in f:
            line = raw_line.rstrip('\n')

            if TIME_RE.match(line):
                if start is not None:
                    rows.append(_build_row(start, end, text_lines))
                start, end = [t.strip() for t in line.split('-->')]
                text_lines = []
                continue

            if line.strip() == '':
                if start is not None:
                    rows.append(_build_row(start, end, text_lines))
                start = end = None
                text_lines = []
                continue

            text_lines.append(line)

    if start is not None:
        rows.append(_build_row(start, end, text_lines))

    return pd.DataFrame(rows)

def parse_transcribe_json(json_data) -> pd.DataFrame:
    """Parse Amazon Transcribe JSON output into a dataframe format similar to VTT parsing."""
    rows = []
    
    # First try to use audio_segments if available
    if 'audio_segments' in json_data['results']:
        for segment in json_data['results']['audio_segments']:
            start_time = segment.get('start_time', '0.0')
            end_time = segment.get('end_time', '0.0')
            
            # Format times to match VTT format (HH:MM:SS.mmm)
            start = _seconds_to_hhmmss(float(start_time))
            end = _seconds_to_hhmmss(float(end_time))
            
            # Try to identify speaker if available
            speaker = "Unknown"
            if 'speaker_label' in segment:
                speaker = segment['speaker_label']
                
            rows.append(dict(
                start=start,
                end=end,
                speaker=speaker,
                text=segment.get('transcript', ''),
                comment=False
            ))
    # Fall back to items if audio_segments not available or empty
    elif len(rows) == 0 and 'items' in json_data['results']:
        items = json_data['results']['items']
        current_speaker = "Unknown"
        current_text = []
        current_start = None
        current_end = None
        
        for item in items:
            if item['type'] == 'pronunciation':
                if current_start is None:
                    current_start = item.get('start_time', '0.0')
                
                current_end = item.get('end_time', '0.0')
                if 'alternatives' in item and len(item['alternatives']) > 0:
                    current_text.append(item['alternatives'][0].get('content', ''))
            
            elif item['type'] == 'punctuation' and current_text:
                current_text[-1] += item['alternatives'][0].get('content', '')
            
            # Check for speaker change markers (if present in your JSON structure)
            if 'speaker_label' in item and item['speaker_label'] != current_speaker:
                if current_text:  # Save the previous segment
                    rows.append(dict(
                        start=_seconds_to_hhmmss(float(current_start)),
                        end=_seconds_to_hhmmss(float(current_end)),
                        speaker=current_speaker,
                        text=' '.join(current_text),
                        comment=False
                    ))
                current_speaker = item['speaker_label']
                current_text = []
                current_start = item.get('start_time', '0.0')
        
        # Add the last segment
        if current_text:
            rows.append(dict(
                start=_seconds_to_hhmmss(float(current_start)),
                end=_seconds_to_hhmmss(float(current_end)),
                speaker=current_speaker,
                text=' '.join(current_text),
                comment=False
            ))
    
    return pd.DataFrame(rows)

def _seconds_to_hhmmss(seconds: float) -> str:
    """Convert seconds to HH:MM:SS.mmm format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds_remainder = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds_remainder:06.3f}"

def _build_row(start, end, lines):
    raw = ' '.join(lines).strip()

    if NOTE_RE.match(raw):
        return dict(start=start, end=end, speaker=None,
                    text=raw[len('NOTE'):].strip(), comment=True)

    m = V_TAG_RE.search(raw)
    if m:
        speaker = m.group(1).strip()
        text = V_TAG_RE.sub('', raw, count=1).strip()
    else:
        speaker = 'Unknown'
        text = raw

    return dict(start=start, end=end, speaker=speaker,
                text=re.sub(r'\s+', ' ', text), comment=False)


def _hhmmss_to_seconds(ts: str) -> float:
    h, m, s = ts.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)

def compute_stats(df, gap_threshold=0.5, round_to=2):
    """
    Returns
    -------
    dict  – keyed by speaker
        {
          "total_time_sec":  123.4,
          "time_share_pct":  56.78,
          "interruptions":   3,
          "interrupt_share_pct": 60.0
        }
    """
    df["duration_sec"] = (
        df["end"].apply(_hhmmss_to_seconds) -
        df["start"].apply(_hhmmss_to_seconds)
    )

    totals = (
        df[~df.comment]
          .groupby("speaker")["duration_sec"]
          .sum()
          .to_dict()
    )
    overall_total = sum(totals.values())

    cues = df[~df.comment].sort_values("start").reset_index(drop=True)
    interruptions = {spk: 0 for spk in cues["speaker"].unique()}

    for i in range(1, len(cues)):
        prev, curr = cues.loc[i - 1], cues.loc[i]
        gap = (_hhmmss_to_seconds(curr["start"]) -
               _hhmmss_to_seconds(prev["end"]))
        if curr["speaker"] != prev["speaker"] and gap <= gap_threshold:
            interruptions[curr["speaker"]] += 1

    total_interrupts = sum(interruptions.values())

    results = {}
    speakers = totals.keys() | interruptions.keys()
    for spk in speakers:
        t_sec = totals.get(spk, 0.0)
        int_cnt = interruptions.get(spk, 0)
        results[spk] = {
            "total_time_sec": round(t_sec, round_to),
            "time_share_pct": round(100 * t_sec / overall_total, round_to)
                              if overall_total else 0.0,
            "interruptions": int_cnt,
            "interrupt_share_pct": round(100 * int_cnt / total_interrupts, round_to)
                                   if total_interrupts else 0.0
        }

    return results

s3 = boto3.client('s3')
BUCKET = os.environ['BUCKET_NAME']

def post_process_vtt(vtt_path: str) -> dict:
    """
    Parse the .vtt at `vtt_path`, compute stats, and return a dict
    that includes both the speaker‐stats and the cue count.
    """
    df = parse_vtt(vtt_path)
    stats = compute_stats(df, gap_threshold=0.4)
    stats['cues'] = len(df)
    stats['source_format'] = 'vtt'
    return stats

def post_process_json(json_data: dict) -> dict:
    """
    Parse the Transcribe JSON data, compute stats, and return a dict
    that includes both the speaker‐stats and the cue count.
    """
    df = parse_transcribe_json(json_data)
    stats = compute_stats(df, gap_threshold=0.4)
    stats['cues'] = len(df)
    stats['source_format'] = 'json'
    stats['job_name'] = json_data.get('jobName', 'unknown')
    stats['job_status'] = json_data.get('status', 'unknown')
    return stats

def check_for_json(bucket, videoname):
    """
    Look for a Transcribe JSON for this videoname in `bucket`.
    """
    potential_keys = [
        f"{videoname}.json"                  # root-level file
    ]
    for key in potential_keys:
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            return json.loads(resp['Body'].read().decode('utf-8'))
        except s3.exceptions.NoSuchKey:
            continue
        except Exception as e:
            print(f"Error checking for JSON at {key}: {e}")
    return None


def lambda_handler(event, context):
    if "Records" in event:
        rec = event["Records"][0]["s3"]
        bucket = rec["bucket"]["name"]
        key    = rec["object"]["key"]
    elif "detail" in event:
        detail = event["detail"]
        bucket = detail["bucket"]["name"]
        key    = detail["object"]["key"]
    else:
        raise ValueError(f"Unrecognized event: {event}")

    # Skip non-VTT files or files in subdirectories
    if '/' in key or not key.lower().endswith(('.vtt', '.json')):
        return {'status': 'skipped', 'reason': key}

    filename = os.path.basename(key)            # e.g. "video123.vtt"
    videoname, ext = os.path.splitext(filename)
    tmp_path = f"/tmp/{filename}"
    
    parsed = None
    source_type = None
    
    if ext.lower() == '.json':
        # direct JSON upload
        body = s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8')
        parsed = post_process_json(json.loads(body))
        source_type = 'json'

    elif ext.lower() == '.vtt':
        # VTT upload
        s3.download_file(bucket, key, tmp_path)
        parsed = post_process_vtt(tmp_path)
        source_type = 'vtt'

    else:
        # (we already filtered these out)
        return {'status': 'skipped', 'reason': key}

    # Convert the parsed data to JSON
    json_bytes = json.dumps(parsed, ensure_ascii=False, indent=2).encode('utf-8')

    # Write back to S3
    out_key = f"{videoname}/{videoname}.json"
    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=json_bytes,
        ContentType='application/json'
    )

    return {
        'status': 'success',
        'outputKey': out_key,
        'itemCount': parsed['cues'],
        'sourceType': source_type
    }
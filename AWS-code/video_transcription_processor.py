import json
import boto3
import urllib.parse
import time
import os
from datetime import datetime

s3_client        = boto3.client('s3')
transcribe_client = boto3.client('transcribe')

def lambda_handler(event, context):
    # 1. Buckets and key
    input_bucket  = event['Records'][0]['s3']['bucket']['name']
    key           = urllib.parse.unquote_plus(
                      event['Records'][0]['s3']['object']['key'])
    output_bucket = os.environ['OUTPUT_BUCKET']

    # 2. Only process video files
    if not key.lower().endswith(('.mp4','.mov','.avi','.mkv')):
        print(f"Skipping non-video: {key}")
        return {'statusCode':200,'body':'Not a video'}

    # 3. Unique job name
    base_name  = os.path.splitext(os.path.basename(key))[0]
    timestamp  = datetime.now().strftime("%Y%m%d%H%M%S")
    job_name   = f"transcribe-{base_name}-{timestamp}"
    job_name   = ''.join(c if c.isalnum() or c=='-' else '-' for c in job_name)

    # 4. Tell Transcribe where to read/write
    response = transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={'MediaFileUri': f"s3://{input_bucket}/{key}"},
        MediaFormat=key.split('.')[-1],
        LanguageCode='en-US',
        OutputBucketName=output_bucket,
        OutputKey=f"{base_name}.json",
        Settings={
        'ShowSpeakerLabels': True,
        'MaxSpeakerLabels': 10    # up to 30 speakers supported
      }
    )

    # 5. Poll for completion…
    job_status = None
    max_tries  = 60
    for i in range(max_tries):
        time.sleep(30)
        job = transcribe_client.get_transcription_job(
                TranscriptionJobName=job_name)
        job_status = job['TranscriptionJob']['TranscriptionJobStatus']
        print(f"Status {job_status} (try {i+1}/{max_tries})")
        if job_status in ('COMPLETED','FAILED'):
            break

    if job_status == 'COMPLETED':
        return {
            'statusCode':200,
            'body':json.dumps(f"Done: {job_name}")
        }
    else:
        return {
            'statusCode':500,
            'body':json.dumps(f"Job {job_status}")
        }

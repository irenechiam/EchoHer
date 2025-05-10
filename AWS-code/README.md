# 🎥 AWS Video Transcription & Summary Backend

This project is a serverless AWS backend pipeline that accepts video uploads via API Gateway, transcribes the content using Amazon Transcribe, analyzes the output with a Lambda function, and summarizes the transcript using an Amazon Bedrock agent. All outputs are stored in S3.

## 🧱 Tech Stack

- **API Gateway** – Entry point for uploading videos via HTTP
- **Amazon S3** – Storage for raw videos, transcripts, and analysis results
- **AWS Lambda** – Orchestrates processing and analysis
- **Amazon Transcribe** – Converts speech in videos to text
- **Amazon Bedrock (Claude/Anthropic Agent)** – Summarizes transcript content using a GenAI agent
- **IAM Roles and Triggers** – Manages permissions and event-based invocation

## 🛠 Architecture Overview

```text
            +------------------+
            |   API Gateway    |
            +------------------+
                    |
                    v
            +------------------+
            |     S3 Bucket    |  (Raw Video Upload)
            +------------------+
                    |
            [Event Trigger]
                    |
                    v
            +------------------+
            |  Amazon Transcribe|
            +------------------+
                    |
            [Transcripts Stored in S3]
                    |
                    v
            +------------------+
            |     Lambda       | → Parses .vtt/.json, computes stats
            +------------------+
                    |
                    v
           +-------------------+
           | Amazon Bedrock AI |
           +-------------------+
                    |
                    v
            +------------------+
            |  S3 (Final Output)|
            +------------------+

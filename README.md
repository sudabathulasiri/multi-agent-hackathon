# Multi-Agent AI System — Deployment Guide

## Architecture

```
POST /process
      │
      ▼
┌─────────────────────────────────────┐
│   PRIMARY COORDINATOR AGENT         │
│   LangGraph ReAct (claude-sonnet)   │
└──────────┬──────────────────────────┘
           │  routes tool calls
    ┌──────┴──────┐
    │             │
    ▼             ▼
┌────────┐  ┌─────────────────────┐
│AGENT A │  │      AGENT B        │
│  The   │  │   The Executor      │
│Librarian│  │  (MCP-style tools)  │
│        │  │                     │
│save_   │  │ schedule_event      │
│ note   │  │ list_events         │
│search_ │  │ create_task         │
│ notes  │  │ list_tasks          │
│list_   │  │ update_task_status  │
│ notes  │  └─────────────────────┘
│delete_ │
│ note   │
└────────┘
     │           │
     └─────┬─────┘
           ▼
      SQLite DB
   (calendar_events,
    tasks, notes)
```

## Quick Start (Local)

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python main.py

# 4. Test
curl -X POST http://localhost:8080/process \
  -H "Content-Type: application/json" \
  -d '{"query": "Save a note about my AI hackathon project, then schedule a review meeting for tomorrow at 3pm and create a task to prepare the slides."}'
```

## Deploy to Google Cloud Run

### Prerequisites
```bash
# Install gcloud CLI, then:
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
```

### Option A — Direct from source (fastest for hackathon)
```bash
cd multi_agent_system

gcloud run deploy multi-agent-system \
  --source . \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-YOUR_KEY \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120
```

### Option B — Build then deploy (more control)
```bash
# Build
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/multi-agent-system

# Deploy
gcloud run deploy multi-agent-system \
  --image gcr.io/YOUR_PROJECT_ID/multi-agent-system \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-YOUR_KEY \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120
```

### Production: Store API Key in Secret Manager (recommended)
```bash
# Create secret
echo -n "sk-ant-YOUR_KEY" | gcloud secrets create anthropic-api-key --data-file=-

# Deploy with secret
gcloud run deploy multi-agent-system \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --memory 512Mi \
  --timeout 120
```

## API Reference

### POST /process
```json
{
  "query": "Check my notes for the project idea, then schedule a meeting about it"
}
```
Response:
```json
{
  "status": "success",
  "query": "...",
  "response": "I found your note about X and scheduled a meeting for...",
  "steps": [
    {"type": "tool_call", "tool": "search_notes", "args": {"query": "project idea"}},
    {"type": "tool_result", "content": "[{...}]"},
    {"type": "tool_call", "tool": "schedule_event", "args": {...}}
  ],
  "duration_ms": 3241.5
}
```

### GET /health       — liveness probe
### GET /notes        — inspect stored notes
### GET /events       — inspect calendar events
### GET /tasks        — inspect tasks (?status=pending|in_progress|done|all)

## Example Queries

```bash
SERVICE_URL=https://YOUR-SERVICE-URL.run.app

# Multi-step: save + schedule
curl -X POST $SERVICE_URL/process \
  -H "Content-Type: application/json" \
  -d '{"query": "Save a note titled Hackathon Idea with content: AI multi-agent system for task automation. Then schedule a demo meeting for 2025-09-01T14:00:00 and create a high-priority task to prepare the presentation."}'

# Retrieve and act
curl -X POST $SERVICE_URL/process \
  -H "Content-Type: application/json" \
  -d '{"query": "Search my notes for hackathon, summarise what you find, and create a task to follow up on the most important point."}'

# Pure calendar
curl -X POST $SERVICE_URL/process \
  -H "Content-Type: application/json" \
  -d '{"query": "List all my upcoming meetings and all pending tasks."}'
```

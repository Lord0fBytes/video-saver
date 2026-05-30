# video-saver

Polls Todoist for tasks labeled `video`, transcribes + summarizes the linked video, and saves a formatted entry to **Inbox > Read Later** in Todoist.

## How it works

1. You add a URL to Todoist with the `video` label (from iOS share sheet or manually)
2. Cron on your server runs `poll_videos.py` every 5 minutes
3. Script pulls the URL, gets metadata + transcript via yt-dlp
4. Falls back to OpenAI Whisper for platforms without native transcripts (Instagram, TikTok, etc.)
5. Claude Haiku summarizes and tags the content
6. A formatted task is created in Todoist **Inbox > Read Later**
7. Original task is closed

---

## Server Setup

### 1. System dependencies

```bash
sudo apt install -y ffmpeg python3-pip
pip3 install requests yt-dlp
```

### 2. Deploy the script

```bash
sudo mkdir -p /opt/video-saver
sudo scp poll_videos.py your-server:/opt/video-saver/poll_videos.py
```

Or clone the repo directly:

```bash
git clone https://github.com/Lord0fBytes/video-saver.git /opt/video-saver
```

### 3. Environment file

```bash
cat > /opt/video-saver/.env << 'EOF'
TODOIST_TOKEN=your_todoist_api_token
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # optional — only needed for non-YouTube transcription
TRIGGER_LABEL=video          # Todoist label to watch (default: video)
EOF

chmod 600 /opt/video-saver/.env
```

Get your Todoist API token: Todoist Settings → Integrations → Developer → API token

### 4. Test manually

```bash
set -a && source /opt/video-saver/.env && set +a
python3 /opt/video-saver/poll_videos.py
```

### 5. Cron (every 5 minutes)

```bash
crontab -e
```

Add:

```
*/5 * * * * set -a && . /opt/video-saver/.env && set +a && python3 /opt/video-saver/poll_videos.py >> /var/log/poll_videos.log 2>&1
```

---

## Usage

From iOS, share any video link to Todoist and add the `video` label. Within 5 minutes it will appear in **Inbox > Read Later** with:

- Linked title
- 2-3 sentence summary
- Key points
- Action items (if any)
- Tags
- Source attribution

The original Todoist task is closed automatically.

---

## Supported platforms

Any site supported by yt-dlp (~1000+ platforms). Native transcript extraction works best on YouTube. Instagram, TikTok, and others fall back to audio download + Whisper transcription (requires `OPENAI_API_KEY`).

## Dependencies

| Tool | Purpose |
|------|---------|
| yt-dlp | Metadata + transcript/audio extraction |
| ffmpeg | Audio processing (required by yt-dlp) |
| OpenAI Whisper API | Audio transcription fallback |
| Claude Haiku | Summarization and tagging |
| Todoist REST API v1 | Task read/write |

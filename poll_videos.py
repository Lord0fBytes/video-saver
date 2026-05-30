#!/usr/bin/env python3
"""
poll_videos.py
Polls Todoist for tasks labeled 'video', transcribes + summarizes, saves to Read Later.
"""

import os, sys, json, re, subprocess, tempfile
from pathlib import Path
import requests

TODOIST_TOKEN    = os.environ["TODOIST_TOKEN"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]
OPENAI_KEY       = os.environ.get("OPENAI_API_KEY", "")

TRIGGER_LABEL        = os.environ.get("TRIGGER_LABEL", "video")
READ_LATER_SECTION   = "Read Later"

API  = "https://api.todoist.com/api/v1"
HDR  = {"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"}


# ── Todoist helpers ────────────────────────────────────────────────────────────

def unwrap(data):
    """Handle both plain list and paginated {"results": [...]} responses."""
    if isinstance(data, list):
        return data
    return data.get("results", [])

MAX_TASKS = 10

def get_tasks_with_label(label):
    """Fetch all active tasks and filter client-side by label name."""
    r = requests.get(f"{API}/tasks", headers=HDR)
    r.raise_for_status()
    all_tasks = unwrap(r.json())
    if all_tasks:
        print(f"  [debug] task keys: {list(all_tasks[0].keys())}")
        print(f"  [debug] first task: {json.dumps(all_tasks[0], indent=2)}")
    matched = [t for t in all_tasks if label in t.get("labels", [])]
    if len(matched) > MAX_TASKS:
        raise RuntimeError(
            f"Sanity check failed: {len(matched)} tasks found with label '{label}' "
            f"(max {MAX_TASKS}). Check that the label is set correctly."
        )
    return matched

def get_inbox_id():
    """Get inbox project ID from the user object."""
    r = requests.get(f"{API}/user", headers=HDR)
    r.raise_for_status()
    project_id = r.json().get("inbox_project_id")
    if not project_id:
        raise RuntimeError("inbox_project_id not found in user response")
    return project_id

def get_or_create_section(project_id, name):
    r = requests.get(f"{API}/sections", headers=HDR, params={"project_id": project_id})
    r.raise_for_status()
    for s in unwrap(r.json()):
        if s["name"] == name:
            return s["id"]
    r = requests.post(f"{API}/sections", headers=HDR,
                      json={"name": name, "project_id": project_id})
    r.raise_for_status()
    return r.json()["id"]

def close_task(task_id):
    requests.post(f"{API}/tasks/{task_id}/close", headers=HDR).raise_for_status()

def create_task(project_id, section_id, content, description):
    r = requests.post(f"{API}/tasks", headers=HDR, json={
        "content": content,
        "description": description,
        "project_id": project_id,
        "section_id": section_id,
    })
    r.raise_for_status()
    return r.json()


# ── yt-dlp helpers ─────────────────────────────────────────────────────────────

def get_metadata(url):
    r = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-download", url],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return {}

def get_native_transcript(url, tmpdir):
    """Try to pull auto-generated or manual subtitles (YouTube, etc.)."""
    subprocess.run([
        "yt-dlp",
        "--skip-download",
        "--write-auto-subs", "--write-subs",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "-o", str(Path(tmpdir) / "video"),
        url
    ], capture_output=True)

    vtt_files = list(Path(tmpdir).glob("*.vtt"))
    if vtt_files:
        return clean_vtt(vtt_files[0].read_text())
    return None

def clean_vtt(text):
    """Strip timestamps and tags, deduplicate lines."""
    seen, out = set(), []
    for line in text.splitlines():
        line = re.sub(r'<[^>]+>', '', line).strip()
        if not line or re.match(r'[\d:.]+ --> ', line) or line == "WEBVTT":
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return ' '.join(out)

def transcribe_audio(url, tmpdir):
    """Download audio and send to OpenAI Whisper API."""
    if not OPENAI_KEY:
        return None

    r = subprocess.run([
        "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
        "-o", str(Path(tmpdir) / "audio.%(ext)s"), url
    ], capture_output=True)

    audio_files = list(Path(tmpdir).glob("audio.*"))
    if not audio_files:
        return None

    with open(audio_files[0], "rb") as f:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            files={"file": (audio_files[0].name, f, "audio/mpeg")},
            data={"model": "whisper-1"}
        )
    return resp.json().get("text") if resp.ok else None


# ── Claude ─────────────────────────────────────────────────────────────────────

def summarize(title, url, platform, duration_s, transcript):
    excerpt = (transcript or "No transcript available.")[:8000]
    duration = f"{int(duration_s)//60}:{int(duration_s)%60:02d}" if duration_s else "unknown"

    prompt = f"""You are organizing a saved video for a personal knowledge system.

Title: {title}
URL: {url}
Platform: {platform}
Duration: {duration}

Transcript:
{excerpt}

Return ONLY valid JSON (no markdown fences):
{{
  "clean_title": "readable title, max 60 chars",
  "summary": "2-3 sentence summary of the key idea",
  "key_points": ["point 1", "point 2", "point 3"],
  "action_items": [],
  "tags": ["tag1", "tag2"]
}}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"]
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return None


# ── Formatting ─────────────────────────────────────────────────────────────────

def format_task(url, meta, ai):
    title = ai.get("clean_title") or meta.get("title", url)
    content = f"[{title}]({url})"

    parts = [f"**Summary:** {ai.get('summary', '')}"]

    if ai.get("key_points"):
        pts = "\n".join(f"- {p}" for p in ai["key_points"])
        parts.append(f"**Key Points:**\n{pts}")

    if ai.get("action_items"):
        items = "\n".join(f"- [ ] {t}" for t in ai["action_items"])
        parts.append(f"**Action Items:**\n{items}")

    if ai.get("tags"):
        parts.append(" ".join(f"#{t}" for t in ai["tags"]))

    uploader = meta.get("uploader") or meta.get("channel", "")
    if uploader:
        parts.append(f"*via {uploader}*")

    return content, "\n\n".join(parts)


# ── Main ───────────────────────────────────────────────────────────────────────

def process(task, inbox_id, section_id):
    text = task["content"] + " " + task.get("description", "")
    urls = re.findall(r'https?://\S+', text)
    if not urls:
        print(f"  [skip] no URL in: {task['content']}")
        return

    url = urls[0].rstrip(')')
    print(f"  URL: {url}")

    with tempfile.TemporaryDirectory() as tmpdir:
        meta = get_metadata(url)
        title = meta.get("title", url)
        print(f"  Title: {title}")

        transcript = get_native_transcript(url, tmpdir)
        if transcript:
            print(f"  Transcript: native ({len(transcript)} chars)")
        else:
            print("  Transcript: none native, trying audio...")
            transcript = transcribe_audio(url, tmpdir)
            if transcript:
                print(f"  Transcript: whisper ({len(transcript)} chars)")
            else:
                print("  Transcript: unavailable")

        ai = summarize(
            title=title,
            url=url,
            platform=meta.get("extractor", "unknown"),
            duration_s=meta.get("duration", 0),
            transcript=transcript,
        )

        if not ai:
            print("  [error] Claude returned no structured result")
            return

        content, description = format_task(url, meta, ai)
        create_task(project_id=inbox_id, section_id=section_id,
                    content=content, description=description)
        close_task(task["id"])
        print(f"  Saved: {ai.get('clean_title', title)}")


def main():
    print(f"Polling Todoist label='{TRIGGER_LABEL}'...")
    tasks = get_tasks_with_label(TRIGGER_LABEL)

    if not tasks:
        print("Nothing to process.")
        return

    print(f"{len(tasks)} task(s) found")
    inbox_id   = get_inbox_id()
    section_id = get_or_create_section(inbox_id, READ_LATER_SECTION)

    for task in tasks:
        print(f"\n→ {task['content']}")
        try:
            process(task, inbox_id, section_id)
        except Exception as e:
            print(f"  [error] {e}")


if __name__ == "__main__":
    main()

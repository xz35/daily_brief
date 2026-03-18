# Morning Audio Brief

Automated daily podcast delivered every weekday morning. Covers macro and credit market news, and a detailed prior-day investment grade bond new issues report.

Runs on GitHub Actions at 6am PT. Hosted on GitHub Pages. Cost: $0.

## Setup

See `PROGRESS.md` in the parent directory for the full setup checklist.

Required secrets (add to GitHub repo Settings → Secrets → Actions):
- `GEMINI_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `GITHUB_PAGES_BASE_URL`

## Local Development

```bash
cp .env.example .env
# Fill in your credentials in .env

pip install -r requirements.txt

# Test individual modules
python tests/test_rss.py
python tests/test_edgar.py
python tests/test_synthesizer.py

# Full run (synthesis only, no audio)
python main.py --skip-tts

# Full run
python main.py
```

## Podcast Feed

Subscribe in your podcast app using the feed URL:
`https://[yourusername].github.io/morning-brief/feed.xml`

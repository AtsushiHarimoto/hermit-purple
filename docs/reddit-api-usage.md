# Reddit API Usage — Hermit Purple

## Overview

Hermit Purple uses the Reddit Data API **exclusively in read-only mode** to fetch
public posts from a small, fixed list of AI-related subreddits. The Reddit adapter
is one of several data sources in the project; this document describes **only** the
Reddit-specific scope, behavior, and data handling.

## Scope of API Access

| Capability | Used? |
|---|---|
| Read public posts | Yes |
| Read public comments | No |
| Post / comment / reply | **No** |
| Vote (upvote / downvote) | **No** |
| Send direct messages | **No** |
| Moderate actions | **No** |
| Access private / quarantined subreddits | **No** |
| User profiling or tracking | **No** |

## Target Subreddits (Fixed List)

The application only queries the following subreddits, configured in `config.yaml`:

- r/LocalLLaMA
- r/MachineLearning
- r/ClaudeAI
- r/ChatGPT
- r/artificial

No other subreddits are accessed.

## Data Collected

For each matching public post, the following metadata is collected:

- Post title
- Post body text (first 500 characters only)
- Permalink (link back to original Reddit thread)
- Author username (public)
- Score, upvote ratio, comment count
- Subreddit name
- Creation timestamp

**No images, videos, or private user data are collected.**

## Data Retention & Handling

- Results are stored in a **local SQLite database** on the user's own machine.
- Data is used to generate **private, local-only** summary reports (Markdown/HTML).
- Deleted or removed posts are **not** retained once detected as removed.
- No Reddit data is uploaded, republished, or shared with third parties.
- No Reddit data is used for **AI/ML model training**.
- No Reddit data is **sold, resold, or redistributed** in any form.

## Request Volume

- The tool is designed for **periodic batch research** (typically once per week).
- A typical run makes fewer than **60 API requests** (5 subreddits × ~10 paginated requests).
- Daily request volume is well under **100 requests/day** in normal usage.
- Built-in rate limiting respects Reddit API guidelines.

## Non-Commercial Use

This project is licensed under **CC BY-NC 4.0** (Creative Commons Attribution-NonCommercial).
It is not operated as a service, does not have paying users, and does not generate revenue.
Reddit data is not monetized in any way.

## Source Code Reference

- Reddit scraper adapter: [`src/scrapers/reddit_scraper.py`](../src/scrapers/reddit_scraper.py)
- Reddit data source: [`src/sources/reddit.py`](../src/sources/reddit.py)
- Subreddit configuration: [`config.yaml`](../config.yaml) → `platforms.reddit.subreddits`

## Contact

Repository: https://github.com/AtsushiHarimoto/hermit-purple
Maintainer: [@AtsushiHarimoto](https://github.com/AtsushiHarimoto)

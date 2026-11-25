#!/usr/bin/env python3
"""
Generate dark hacker-green SVG telemetry cards for GitHub Profile README
including private repositories via GitHub GraphQL API.
"""
import datetime
import html
import json
import os
import sys
import urllib.error
import urllib.request

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($cursor: String) {
  viewer {
    login
    name
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        name
        isPrivate
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def graphql_request(token: str, cursor: str | None = None) -> dict:
    payload = json.dumps({"query": QUERY, "variables": {"cursor": cursor}}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "dhanesh686-m-telemetry-bot",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]["viewer"]


def compute_streaks(days: list[dict]) -> tuple[int, str, int, str, str]:
    """Return (current_streak, current_range, longest_streak, longest_range, total_range)."""
    if not days:
        return 0, "", 0, "", ""

    sorted_days = sorted(days, key=lambda d: d["date"])
    first_date = sorted_days[0]["date"]
    total_range = f"{first_date} — Present"

    # Longest streak
    longest = 0
    longest_start = ""
    longest_end = ""
    cur_run = 0
    cur_start = ""

    for d in sorted_days:
        if d["contributionCount"] > 0:
            if cur_run == 0:
                cur_start = d["date"]
            cur_run += 1
            if cur_run > longest:
                longest = cur_run
                longest_start = cur_start
                longest_end = d["date"]
        else:
            cur_run = 0

    longest_range = f"{longest_start} — {longest_end}" if longest > 0 else "No streak yet"

    # Current streak (allow today to be 0 if yesterday had contributions)
    today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    idx = len(sorted_days) - 1
    if idx >= 0 and sorted_days[idx]["date"] == today_str and sorted_days[idx]["contributionCount"] == 0:
        idx -= 1

    current = 0
    cur_end = sorted_days[idx]["date"] if idx >= 0 else ""
    cur_begin = ""
    while idx >= 0 and sorted_days[idx]["contributionCount"] > 0:
        cur_begin = sorted_days[idx]["date"]
        current += 1
        idx -= 1

    current_range = f"{cur_begin} — {cur_end}" if current > 0 else "Inactive today"
    return current, current_range, longest, longest_range, total_range


def fetch_metrics(token: str) -> dict:
    cursor = None
    repos = []
    viewer_base = None

    while True:
        viewer = graphql_request(token, cursor)
        if viewer_base is None:
            viewer_base = viewer
        repo_conn = viewer["repositories"]
        repos.extend(repo_conn["nodes"])
        if not repo_conn["pageInfo"]["hasNextPage"]:
            break
        cursor = repo_conn["pageInfo"]["endCursor"]

    cc = viewer_base["contributionsCollection"]
    cal = cc["contributionCalendar"]
    all_days = [day for week in cal["weeks"] for day in week["contributionDays"]]
    cur_streak, cur_range, long_streak, long_range, total_range = compute_streaks(all_days)

    total_stars = sum(r["stargazerCount"] for r in repos)
    total_repos = len(repos)
    private_repos = sum(1 for r in repos if r["isPrivate"])
    total_commits = cc["totalCommitContributions"] + cc["restrictedContributionsCount"]

    lang_bytes: dict[str, dict] = {}
    for r in repos:
        for edge in r["languages"]["edges"]:
            name = edge["node"]["name"]
            color = edge["node"]["color"] or "#22c55e"
            size = edge["size"]
            if name not in lang_bytes:
                lang_bytes[name] = {"size": 0, "color": color}
            lang_bytes[name]["size"] += size

    sorted_langs = sorted(lang_bytes.items(), key=lambda x: x[1]["size"], reverse=True)
    total_lang_size = sum(v["size"] for _, v in sorted_langs) or 1
    top_langs = [
        {
            "name": name,
            "color": info["color"],
            "pct": round((info["size"] / total_lang_size) * 100, 1),
        }
        for name, info in sorted_langs[:6]
    ]

    return {
        "name": viewer_base.get("name") or viewer_base["login"],
        "total_repos": total_repos,
        "private_repos": private_repos,
        "total_stars": total_stars,
        "total_commits": total_commits,
        "total_prs": cc["totalPullRequestContributions"],
        "total_issues": cc["totalIssueContributions"],
        "contributed_to": cc["totalRepositoriesWithContributedCommits"],
        "total_contributions": cal["totalContributions"],
        "current_streak": cur_streak,
        "current_range": cur_range,
        "longest_streak": long_streak,
        "longest_range": long_range,
        "total_range": total_range,
        "top_langs": top_langs,
    }


def render_stats_svg(m: dict) -> str:
    title = html.escape(f"{m['name']}'s GitHub Telemetry (All Repos)")
    rows = [
        ("Total Repositories (Private + Public)", f"{m['total_repos']} ({m['private_repos']} private)"),
        ("Total Commits (Last Year)", f"{m['total_commits']}"),
        ("Total Stars Earned", f"{m['total_stars']}"),
        ("Total PRs &amp; Issues", f"{m['total_prs']} PRs / {m['total_issues']} Issues"),
        ("Repos Contributed To", f"{m['contributed_to']}"),
    ]
    row_svg = []
    for idx, (label, val) in enumerate(rows):
        y = 66 + idx * 25
        row_svg.append(
            f'<text x="24" y="{y}" fill="#94a3b8" font-size="12.5" font-family="Segoe UI, Ubuntu, sans-serif">{label}</text>'
            f'<text x="372" y="{y}" text-anchor="end" fill="#f8fafc" font-size="12.5" font-weight="700" font-family="Consolas, monospace">{html.escape(val)}</text>'
        )

    score = min(99, 45 + m["total_commits"] // 10 + m["total_repos"] * 2)
    grade = "S" if score >= 90 else ("A+" if score >= 80 else ("A" if score >= 70 else "B+"))

    return f"""<svg width="495" height="195" viewBox="0 0 495 195" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="0.5" y="0.5" width="494" height="194" rx="10" fill="#050505" stroke="#14532d"/>
  <text x="24" y="34" fill="#22c55e" font-size="15" font-weight="700" font-family="Segoe UI, Ubuntu, sans-serif">{title}</text>
  <line x1="24" y1="45" x2="372" y2="45" stroke="#14532d" stroke-width="1"/>
  {''.join(row_svg)}
  <circle cx="436" cy="105" r="36" stroke="#14532d" stroke-width="6" fill="none"/>
  <circle cx="436" cy="105" r="36" stroke="#22c55e" stroke-width="6" fill="none" stroke-dasharray="195 226" stroke-linecap="round" transform="rotate(-90 436 105)"/>
  <text x="436" y="112" text-anchor="middle" fill="#f8fafc" font-size="22" font-weight="800" font-family="Segoe UI, Ubuntu, sans-serif">{grade}</text>
</svg>
"""


def render_langs_svg(m: dict) -> str:
    langs = m["top_langs"]
    if not langs:
        langs = [{"name": "Awaiting Token Sync", "color": "#22c55e", "pct": 100.0}]

    bar_segments = []
    x_offset = 24.0
    total_bar_w = 342.0
    for lang in langs:
        w = max(2.0, (lang["pct"] / 100.0) * total_bar_w)
        bar_segments.append(
            f'<rect x="{x_offset:.1f}" y="54" width="{w:.1f}" height="9" fill="{html.escape(lang["color"])}"/>'
        )
        x_offset += w

    legend = []
    for idx, lang in enumerate(langs[:6]):
        col = idx % 2
        row = idx // 2
        lx = 24 + col * 176
        ly = 90 + row * 28
        legend.append(
            f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{html.escape(lang["color"])}"/>'
            f'<text x="{lx + 16}" y="{ly}" fill="#e2e8f0" font-size="12" font-family="Segoe UI, Ubuntu, sans-serif">'
            f'{html.escape(lang["name"])} <tspan fill="#94a3b8" font-family="Consolas, monospace">{lang["pct"]}%</tspan></text>'
        )

    return f"""<svg width="390" height="195" viewBox="0 0 390 195" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="0.5" y="0.5" width="389" height="194" rx="10" fill="#050505" stroke="#14532d"/>
  <text x="24" y="34" fill="#22c55e" font-size="15" font-weight="700" font-family="Segoe UI, Ubuntu, sans-serif">Most Used Languages (All Repos)</text>
  <clipPath id="bar-clip"><rect x="24" y="54" width="342" height="9" rx="4.5"/></clipPath>
  <g clip-path="url(#bar-clip)">
    {''.join(bar_segments)}
  </g>
  {''.join(legend)}
</svg>
"""


def render_streak_svg(m: dict) -> str:
    return f"""<svg width="600" height="165" viewBox="0 0 600 165" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="0.5" y="0.5" width="599" height="164" rx="10" fill="#050505" stroke="#14532d"/>
  <line x1="200" y1="24" x2="200" y2="141" stroke="#14532d" stroke-width="1"/>
  <line x1="400" y1="24" x2="400" y2="141" stroke="#14532d" stroke-width="1"/>

  <text x="100" y="72" text-anchor="middle" fill="#ffffff" font-size="28" font-weight="800" font-family="Segoe UI, Ubuntu, sans-serif">{m['total_contributions']}</text>
  <text x="100" y="100" text-anchor="middle" fill="#22c55e" font-size="13" font-weight="700" font-family="Segoe UI, Ubuntu, sans-serif">Total Contributions</text>
  <text x="100" y="122" text-anchor="middle" fill="#94a3b8" font-size="10.5" font-family="Consolas, monospace">{html.escape(m['total_range'])}</text>

  <circle cx="300" cy="64" r="32" stroke="#22c55e" stroke-width="4" fill="none"/>
  <text x="300" y="73" text-anchor="middle" fill="#ffffff" font-size="26" font-weight="800" font-family="Segoe UI, Ubuntu, sans-serif">{m['current_streak']}</text>
  <text x="300" y="115" text-anchor="middle" fill="#22c55e" font-size="13" font-weight="700" font-family="Segoe UI, Ubuntu, sans-serif">Current Streak</text>
  <text x="300" y="135" text-anchor="middle" fill="#94a3b8" font-size="10.5" font-family="Consolas, monospace">{html.escape(m['current_range'])}</text>

  <text x="500" y="72" text-anchor="middle" fill="#ffffff" font-size="28" font-weight="800" font-family="Segoe UI, Ubuntu, sans-serif">{m['longest_streak']}</text>
  <text x="500" y="100" text-anchor="middle" fill="#22c55e" font-size="13" font-weight="700" font-family="Segoe UI, Ubuntu, sans-serif">Longest Streak</text>
  <text x="500" y="122" text-anchor="middle" fill="#94a3b8" font-size="10.5" font-family="Consolas, monospace">{html.escape(m['longest_range'])}</text>
</svg>
"""


def main() -> int:
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    os.makedirs(out_dir, exist_ok=True)

    token = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if "--demo" in sys.argv or not token:
        print("[!] No METRICS_TOKEN found; generating placeholder telemetry SVGs.")
        metrics = {
            "name": "Dhanesh Maheshwari",
            "total_repos": 0,
            "private_repos": 0,
            "total_stars": 0,
            "total_commits": 0,
            "total_prs": 0,
            "total_issues": 0,
            "contributed_to": 0,
            "total_contributions": 0,
            "current_streak": 0,
            "current_range": "Add METRICS_TOKEN secret",
            "longest_streak": 0,
            "longest_range": "Run GitHub Action",
            "total_range": "Private + Public Repos",
            "top_langs": [
                {"name": "Python", "color": "#3572A5", "pct": 40.0},
                {"name": "C++ / QML", "color": "#f34b7d", "pct": 30.0},
                {"name": "C", "color": "#555555", "pct": 15.0},
                {"name": "HTML / JS", "color": "#e34c26", "pct": 15.0},
            ],
        }
    else:
        metrics = fetch_metrics(token)

    with open(os.path.join(out_dir, "github-stats.svg"), "w", encoding="utf-8") as f:
        f.write(render_stats_svg(metrics))
    with open(os.path.join(out_dir, "top-langs.svg"), "w", encoding="utf-8") as f:
        f.write(render_langs_svg(metrics))
    with open(os.path.join(out_dir, "github-streak.svg"), "w", encoding="utf-8") as f:
        f.write(render_streak_svg(metrics))

    print(f"[+] Wrote SVGs to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

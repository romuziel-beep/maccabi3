#!/usr/bin/env python3
"""סורק את דף הנבחרת באתר איגוד הכדורעף וכותב data/games_scraped.json.

עקרונות:
- לעולם לא מפיל את ה-workflow: כשל נרשם ב-state/scrape_status.json, והאתר ממשיך להציג
  את הנתונים האחרונים. בדיקת הבריאות (build.py --check-health) היא זו שמתריעה.
- לעולם לא דורס נתונים טובים בנתונים חשודים (0 משחקים, או ירידה חדה במספרם).
- מזהה המשחק הוא ה-GameId של ההתאחדות, כך ששינוי תאריך/שעה מעדכן את אותו אירוע ביומן.

מבנה הדף שאומת ב-3.11.2026 (טבלה אחת: table.table-team, 6 עמודות):
  תאריך ושעה | מסגרת ומחזור | מארחת | אורחת | אולם | תוצאה (קישור ל-game-zone/?GameId=...)
לא נצפו עדיין משחקים שנדחו/בוטלו/עם תוצאה, ולכן הטיפול בהם הוא ניחוש מתועד (ראו parse_games).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Maccabi3ScheduleBot/1.0 (+personal team schedule; contact: team coach)"

DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}).*?(\d{1,2}):(\d{2})")
ROUND_RE = re.compile(r"מחזור\s*(\d+)")
GAMEID_RE = re.compile(r"GameId=([A-Za-z0-9]+)")
SCORE_RE = re.compile(r"(\d+)\s*[-:–]\s*(\d+)")
POSTPONED_RE = re.compile(r"נדח")
CANCELLED_RE = re.compile(r"בוטל")


class ScrapeError(Exception):
    pass


def parse_games(html: str, team_full_name: str, source_url: str = "") -> list[dict]:
    from bs4 import BeautifulSoup  # מיובא כאן כדי שבדיקות אחרות לא ידרשו את החבילה

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.table-team")
    if table is None:
        raise ScrapeError("לא נמצאה הטבלה table.table-team. ייתכן שמבנה הדף השתנה.")

    rows = table.select("tbody tr") or table.select("tr")[1:]
    games: list[dict] = []
    problems: list[str] = []

    for idx, tr in enumerate(rows, start=1):
        tds = tr.find_all("td")
        if len(tds) < 6:
            problems.append(f"שורה {idx}: {len(tds)} תאים במקום 6")
            continue

        txt = [td.get_text(" ", strip=True) for td in tds]
        m = DATE_RE.search(txt[0])
        if not m:
            problems.append(f"שורה {idx}: לא זוהה תאריך/שעה ב-'{txt[0]}'")
            continue
        day, month, year, hh, mm = (int(x) for x in m.groups())
        date_iso = f"{year:04d}-{month:02d}-{day:02d}"
        time_hm = f"{hh:02d}:{mm:02d}"

        home_name, away_name, venue = txt[2], txt[3], txt[4]
        if team_full_name == home_name:
            is_home, opponent = True, away_name
        elif team_full_name == away_name:
            is_home, opponent = False, home_name
        else:
            problems.append(f"שורה {idx}: '{team_full_name}' לא מופיעה בין '{home_name}' ל-'{away_name}'")
            continue

        rm = ROUND_RE.search(txt[1])
        league = ROUND_RE.sub("", txt[1]).strip()

        gid = None
        for a in tds[5].find_all("a", href=True):
            gm = GAMEID_RE.search(a["href"])
            if gm:
                gid = gm.group(1)
                break
        if not gid:
            problems.append(f"שורה {idx}: אין GameId ({date_iso} נגד {opponent})")
            continue

        # ניחוש מתועד: טקסט בעמודת התוצאה = תוצאה; 'נדחה'/'בוטל' בכל מקום בשורה = סטטוס.
        # לא נצפה עדיין בדף אמיתי. אם הדף מציג זאת אחרת, יש לתקן כאן או דרך data/overrides.json.
        row_text = " ".join(txt)
        status = "scheduled"
        if CANCELLED_RE.search(row_text):
            status = "cancelled"
        elif POSTPONED_RE.search(row_text):
            status = "postponed"

        result_text = txt[5].strip()
        game = {
            "id": gid,
            "date": date_iso,
            "time": time_hm,
            "opponent": opponent,
            "home": is_home,
            "venue": venue,
            "round": int(rm.group(1)) if rm else None,
            "league": league,
            "status": status,
            "source_url": f"https://www.iva.org.il/game-zone/?GameId={gid}",
        }
        if result_text:
            game["result_text"] = result_text
            sm = SCORE_RE.search(result_text)
            if sm:
                game["score"] = {"home": int(sm.group(1)), "away": int(sm.group(2))}
        games.append(game)

    if problems and not games:
        raise ScrapeError("אף שורה לא פוענחה: " + "; ".join(problems[:5]))
    if problems:
        # שורה בעייתית אחת לא מפילה הכול, אבל חייבת להיראות בלוג.
        print("אזהרות פענוח:\n  " + "\n  ".join(problems), file=sys.stderr)

    ids = [g["id"] for g in games]
    if len(ids) != len(set(ids)):
        raise ScrapeError("נמצאו GameId כפולים בדף.")
    return games


def fetch(url: str) -> str:
    import requests

    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "he"}, timeout=30)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="לפרסר קובץ HTML מקומי במקום להוריד (לבדיקות)")
    ap.add_argument("--out", default=str(ROOT / "data" / "games_scraped.json"))
    ap.add_argument("--status", default=str(ROOT / "state" / "scrape_status.json"))
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    status_path, out_path = Path(args.status), Path(args.out)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    prev_status = {}
    if status_path.exists():
        try:
            prev_status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    url = (cfg.get("sources") or {}).get("games_url")
    if not url and not args.html:
        atomic_write(status_path, {"configured": False, "ok": None, "at": now})
        print("לא הוגדר games_url: עובדים עם נתונים ידניים בלבד.")
        return 0

    try:
        html = Path(args.html).read_text(encoding="utf-8") if args.html else fetch(url)
        games = parse_games(html, cfg["team_full_name"], url or "")
        if not games:
            raise ScrapeError("הפענוח החזיר 0 משחקים.")

        prev_count = 0
        if out_path.exists():
            try:
                prev_count = len(json.loads(out_path.read_text(encoding="utf-8")).get("games", []))
            except json.JSONDecodeError:
                pass
        if prev_count >= 4 and len(games) < prev_count * 0.5:
            raise ScrapeError(f"ירידה חשודה במספר המשחקים ({prev_count} -> {len(games)}). לא דורסים.")

        atomic_write(out_path, {"scraped_at": now, "source": url, "games": games})
        atomic_write(status_path, {"configured": True, "ok": True, "at": now, "last_ok_at": now, "count": len(games), "error": None})
        print(f"נסרקו {len(games)} משחקים.")
    except Exception as e:  # noqa: BLE001 - כל כשל נרשם ולא מפיל את ה-workflow
        atomic_write(
            status_path,
            {"configured": True, "ok": False, "at": now, "last_ok_at": prev_status.get("last_ok_at"), "count": prev_status.get("count"), "error": f"{type(e).__name__}: {e}"},
        )
        print(f"הסריקה נכשלה: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

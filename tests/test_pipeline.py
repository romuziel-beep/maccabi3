#!/usr/bin/env python3
"""בדיקת מקצה לקצה: סריקה -> בנייה -> אימות ICS -> שינוי שעה -> כשלים.

חשוב: ה-fixture הוא שחזור של מבנה הדף כפי שנצפה ב-3.11.2026 (טבלה table.table-team, 6 תאים),
לא עותק של ה-HTML המקורי. הוא מוכיח שהפרסר עובד על המבנה שתועד, לא שהדף האמיתי לא השתנה.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TEAM = "מכבי תל אביב 3"

ROWS = [
    ("13/11/2026 יום שישי, 12:30", 217, "בליך עומרי ועידו רמת גן", TEAM, "תיכון בליך רמת גן"),
    ("13/11/2026 יום שישי, 13:45", 218, TEAM, "מכבי תל אביב 2", "תיכון בליך רמת גן"),
    ("04/12/2026 יום שישי, 13:30", 226, TEAM, "מ.כ. גני תקווה", "יהודה מכבי תל אביב"),
    ("04/12/2026 יום שישי, 16:00", 228, TEAM, "הפועל גבעתיים", "יהודה מכבי תל אביב"),
    ("25/12/2026 יום שישי, 12:30", 235, "מכבי ראשון לציון", TEAM, 'פרס נובל ראשל"צ'),
    ("25/12/2026 יום שישי, 13:45", 236, TEAM, "מכבי תל אביב 4", 'פרס נובל ראשל"צ'),
    ("15/01/2027 יום שישי, 13:30", 247, TEAM, "מ.ס. רמת אביב 2", "יהודה מכבי תל אביב"),
    ("15/01/2027 יום שישי, 16:00", 249, TEAM, "ק.ק. תל אביב 1", "יהודה מכבי תל אביב"),
    ("05/02/2027 יום שישי, 13:30", 253, TEAM, "בליך עומרי ועידו רמת גן", "יהודה מכבי תל אביב"),
    ("05/02/2027 יום שישי, 16:00", 255, "מכבי תל אביב 2", TEAM, "יהודה מכבי תל אביב"),
    ("20/02/2027 יום שבת, 12:15", 263, "מ.כ. גני תקווה", TEAM, 'אולם בי"ס שמעוני גבעתיים'),
    ("20/02/2027 יום שבת, 13:30", 264, "הפועל גבעתיים", TEAM, 'אולם בי"ס שמעוני גבעתיים'),
    ("19/03/2027 יום שישי, 13:00", 271, "מכבי תל אביב 4", TEAM, "אולם בית ספר גורדון"),
    ("19/03/2027 יום שישי, 14:15", 272, TEAM, "מכבי ראשון לציון", "אולם בית ספר גורדון"),
    ("09/04/2027 יום שישי, 14:15", 284, "ק.ק. תל אביב 1", TEAM, "אולם תיכון צמרות תל אביב"),
    ("09/04/2027 יום שישי, 15:30", 285, "מ.ס. רמת אביב 2", TEAM, "אולם תיכון צמרות תל אביב"),
]


def fixture_html(rows=ROWS, extra_cell=None):
    body = []
    for i, (when, rnd, home, away, venue) in enumerate(rows):
        gid = f"T{i:02d}abc"
        res = extra_cell(i) if extra_cell else ""
        link = f'<a href="https://www.iva.org.il/game-zone/?GameId={gid}">{res}</a>'
        body.append(
            f'<tr><td class="sticky-col">{when}</td><td>ליגת נערים ב\' (U15) מחזור {rnd}</td>'
            f"<td>{home}</td><td>{away}</td><td>{venue}</td><td class=\"td_ltr_center\">{link}</td></tr>"
        )
    return (
        '<html><body><table class="common-table table table-team"><thead><tr><th>תאריך</th><th>מסגרת</th>'
        "<th>מארחת</th><th>אורחת</th><th>אולם</th><th>תוצאה</th></tr></thead><tbody>" + "".join(body) + "</tbody></table></body></html>"
    )


def run(args, **kw):
    return subprocess.run([PY, *map(str, args)], capture_output=True, text=True, **kw)


def check(cond, msg):
    if not cond:
        print("נכשל:", msg)
        sys.exit(1)
    print("ok  :", msg)


def main():
    scratch = os.environ.get("SCRATCH")
    with tempfile.TemporaryDirectory(dir=scratch) as td:
        proj = Path(td) / "proj"
        shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns(".git", "tests", "docs", "__pycache__"))
        (proj / "docs").mkdir()
        (proj / "state" / "scrape_status.json").unlink(missing_ok=True)
        (proj / "state" / "seq.json").unlink(missing_ok=True)
        html = Path(td) / "page.html"
        html.write_text(fixture_html(), encoding="utf-8")

        scrape = [proj / "scripts" / "scrape.py", "--html", html, "--config", proj / "config.json",
                  "--out", proj / "data" / "games_scraped.json", "--status", proj / "state" / "scrape_status.json"]
        build = [proj / "scripts" / "build.py", "--root", proj]

        # 1. סריקה
        r = run(scrape)
        check(r.returncode == 0, "scrape יוצא 0")
        scraped = json.loads((proj / "data" / "games_scraped.json").read_text(encoding="utf-8"))["games"]
        check(len(scraped) == 16, f"נסרקו 16 משחקים ({len(scraped)})")
        g0 = scraped[0]
        check(g0["home"] is False and g0["opponent"] == "בליך עומרי ועידו רמת גן", "משחק 1: חוץ, יריבה נכונה")
        check(g0["date"] == "2026-11-13" and g0["time"] == "12:30" and g0["round"] == 217, "משחק 1: תאריך/שעה/מחזור")
        check(scraped[1]["home"] is True and scraped[1]["opponent"] == "מכבי תל אביב 2", "משחק 2: בית נגד ת\"א 2")
        check(sum(g["home"] for g in scraped) == 8, "8 משחקי בית ו-8 חוץ")

        # 2. בנייה
        r = run(build + ["--now", "2026-11-03T10:00:00Z"])
        check(r.returncode == 0, "build יוצא 0: " + r.stderr[:200])
        data = json.loads((proj / "docs" / "data.json").read_text(encoding="utf-8"))
        check(len(data["games"]) == 16, "data.json: 16 משחקים")
        check(all(not g["conflicts"] for g in data["games"]), "אין התנגשויות עם חופשות (כל המשחקים בשישי/שבת מחוץ לחופשות)")
        check(all(h["applies_to"] in ("all", "secondary") for h in data["holidays"]), "חופשות מסוננות לפי שכבת גיל")
        home_game = next(g for g in data["games"] if g["venue"] == "יהודה מכבי תל אביב")
        check(home_game["address"] == "אנטיגונס 6, תל אביב" and home_game["address_verified"], "כתובת מאומתת לאולם הבית")
        away_game = data["games"][0]
        check(away_game["address"] == "" and not away_game["address_verified"], "אולם חוץ בלי כתובת מסומן כלא מאומת")

        # 3. ICS
        from icalendar import Calendar

        raw = (proj / "docs" / "maccabi3.ics").read_bytes()
        check(b"\r\n" in raw and all(len(l) <= 75 for l in raw.split(b"\r\n")), "ICS: CRLF ושורות <= 75 בתים")
        cal = Calendar.from_ical(raw)
        evs = [c for c in cal.walk("VEVENT")]
        check(len(evs) == 8, "ICS: אירוע אחד לכל יום טורניר (8)")
        check(len(data["days"]) == 8 and all(d["day_size"] == 2 for d in map(lambda x: next(g for g in data["games"] if g["id"] == x["game_ids"][0]), data["days"])), "data.json: 8 ימי טורניר של 2 משחקים")
        check(sum(1 for d in data["days"] if d["home"]) == 3 and sum(1 for d in data["days"] if not d["home"]) == 5, "3 ימי בית ו-5 ימי חוץ לפי האולם")
        check(sum(1 for g in data["games"] if g["home"]) == 6, "6 משחקי בית לפי האולם (ולא 8 לפי תווית ההתאחדות)")
        d1 = next(d for d in data["days"] if d["date"] == "2026-11-13")
        check(d1["home"] is False and d1["gather"] == "12:00" and len(d1["teams"]) == 3, "13.11: חוץ, כינוס אחד 12:00, 3 קבוצות")
        check(sum(1 for g in data["games"] if g["gather"]) == 8, "כינוס מוצג פעם אחת בכל יום")
        ev0 = evs[0]
        start = ev0.decoded("DTSTART")
        check(start.tzinfo is not None and start.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:%M") == "12:00", "ICS: האירוע מתחיל בכינוס 12:00 שעון ישראל")
        check(str(ev0["UID"]) == "T00abc@maccabi3.schedule", "ICS: UID נגזר מ-GameId")
        check("טורניר חוץ" in str(ev0["SUMMARY"]) and "מכבי 3" in str(ev0["SUMMARY"]), "ICS: כותרת בעברית תקינה")
        check("בליך" in str(ev0["DESCRIPTION"]) and "כינוס: 12:00" in str(ev0["DESCRIPTION"]), "ICS: התיאור מפרט משחקים וכינוס")
        # קיץ/חורף: משחק ב-9.4.2027 (שעון קיץ מתחיל 26.3.2027) חייב להיות +03:00
        last = evs[-1].decoded("DTSTART")
        check(last.utcoffset() == timedelta(hours=3), "ICS: אפריל 2027 בשעון קיץ (+03:00)")
        check(evs[0].decoded("DTSTART").utcoffset() == timedelta(hours=2), "ICS: נובמבר 2026 בשעון חורף (+02:00)")
        cal2 = Calendar.from_ical((proj / "docs" / "maccabi3-with-holidays.ics").read_bytes())
        n2 = len(list(cal2.walk("VEVENT")))
        check(n2 > 8, f"פיד עם חופשות: {n2} אירועים (>8)")

        # 4. שינוי שעה: אותו UID, SEQUENCE עולה
        scraped[3]["time"] = "17:00"
        (proj / "data" / "games_scraped.json").write_text(json.dumps({"games": scraped}, ensure_ascii=False), encoding="utf-8")
        r = run(build + ["--now", "2026-11-04T10:00:00Z"])
        check(r.returncode == 0, "build אחרי שינוי שעה")
        evs = list(Calendar.from_ical((proj / "docs" / "maccabi3.ics").read_bytes()).walk("VEVENT"))
        changed = [e for e in evs if int(e["SEQUENCE"]) > 0]
        check(len(changed) == 1 and str(changed[0]["UID"]) == "T02abc@maccabi3.schedule", "רק יום הטורניר ששונה קיבל SEQUENCE>0, אותו UID")
        check(changed[0].decoded("DTEND").strftime("%H:%M") == "18:15" and changed[0].decoded("DTSTART").strftime("%H:%M") == "13:00", "סוף האירוע זז, הכינוס נשאר")
        data = json.loads((proj / "docs" / "data.json").read_text(encoding="utf-8"))
        check(sum(g["recently_changed"] for g in data["games"]) == 1, "data.json מסמן 'עודכן לאחרונה' רק על המשחק ששונה")

        # 5. דחייה ידנית דרך overrides
        (proj / "data" / "overrides.json").write_text(json.dumps({"T05abc": {"status": "postponed", "note": "נדחה, מועד יפורסם"}}, ensure_ascii=False), encoding="utf-8")
        r = run(build + ["--now", "2026-11-05T10:00:00Z"])
        check(r.returncode == 0, "build עם override")
        evs = {str(e["UID"]): e for e in Calendar.from_ical((proj / "docs" / "maccabi3.ics").read_bytes()).walk("VEVENT")}
        e5 = evs["T04abc@maccabi3.schedule"]
        check(str(e5["SUMMARY"]).startswith("[שינוי]") and str(e5["STATUS"]) == "TENTATIVE", "דחיית משחק אחד בטורניר: כותרת [שינוי] ו-TENTATIVE")
        check("[נדחה]" in str(e5["DESCRIPTION"]), "התיאור מסמן איזה משחק נדחה")

        # 6. התנגשות עם חופשה (משחק בחנוכה)
        manual = {"games": [{"date": "2026-12-08", "time": "18:00", "opponent": "בדיקה", "venue": "יהודה מכבי תל אביב"}]}
        (proj / "data" / "games_manual.json").write_text(json.dumps(manual, ensure_ascii=False), encoding="utf-8")
        r = run(build + ["--now", "2026-11-05T11:00:00Z"])
        data = json.loads((proj / "docs" / "data.json").read_text(encoding="utf-8"))
        m = next(g for g in data["games"] if g["opponent"] == "בדיקה")
        check(m["conflicts"] and m["conflicts"][0]["name"] == "חופשת חנוכה", "משחק בחנוכה מסומן כהתנגשות")
        check(m["id"].startswith("m"), "משחק ידני קיבל מזהה יציב")
        saved = json.loads((proj / "data" / "games_manual.json").read_text(encoding="utf-8"))
        check(saved["games"][0]["id"] == m["id"], "המזהה נכתב חזרה לקובץ הידני")

        # 7. שגיאת קלט ידנית מפילה את הבנייה, ולא מוחקת את האתר
        before = (proj / "docs" / "data.json").read_text(encoding="utf-8")
        (proj / "data" / "games_manual.json").write_text(json.dumps({"games": [{"date": "2026-13-45", "time": "18:00", "opponent": "x"}]}), encoding="utf-8")
        r = run(build + ["--now", "2026-11-06T10:00:00Z"])
        check(r.returncode == 1 and "תאריך" in r.stderr, "תאריך שגוי: build נכשל עם הודעה בעברית")
        check((proj / "docs" / "data.json").read_text(encoding="utf-8") == before, "האתר הקודם נשאר כמות שהוא")
        (proj / "data" / "games_manual.json").write_text('{"games": []}', encoding="utf-8")

        # 8. כשלי סריקה
        good = (proj / "data" / "games_scraped.json").read_text(encoding="utf-8")
        for label, bad in (("טבלה חסרה", "<html><body>אין כלום</body></html>"), ("0 שורות", fixture_html(rows=[])),
                           ("ירידה חדה", fixture_html(rows=ROWS[:3]))):
            html.write_text(bad, encoding="utf-8")
            r = run(scrape)
            st = json.loads((proj / "state" / "scrape_status.json").read_text(encoding="utf-8"))
            check(r.returncode == 0 and st["ok"] is False, f"{label}: הסריקה נכשלת בשקט ונרשמת")
            check((proj / "data" / "games_scraped.json").read_text(encoding="utf-8") == good, f"{label}: הנתונים הטובים לא נדרסו")

        # 9. סימון תוצאה/דחייה בדף (ניחוש מתועד, נבדק רק מול fixture)
        html.write_text(fixture_html(extra_cell=lambda i: "3 - 1" if i == 0 else ("נדחה" if i == 1 else "")), encoding="utf-8")
        run(scrape)
        s = json.loads((proj / "data" / "games_scraped.json").read_text(encoding="utf-8"))["games"]
        check(s[0].get("score") == {"home": 3, "away": 1}, "תוצאה בפורמט 'N - M' מפוענחת (fixture בלבד)")
        check(s[1]["status"] == "postponed", "'נדחה' בשורה מזוהה כדחייה (fixture בלבד)")

        # 10. בדיקת בריאות
        st = {"configured": True, "ok": False, "at": "2026-11-10T00:00:00Z", "last_ok_at": (datetime.now(ZoneInfo("UTC")) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"), "error": "x"}
        (proj / "state" / "scrape_status.json").write_text(json.dumps(st), encoding="utf-8")
        r = run(build + ["--check-health"])
        check(r.returncode == 1, "health: סריקה ישנה -> יציאה 1")
        st["last_ok_at"] = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        (proj / "state" / "scrape_status.json").write_text(json.dumps(st), encoding="utf-8")
        r = run(build + ["--check-health"])
        check(r.returncode == 0, "health: סריקה טרייה -> יציאה 0")

    print("\nכל הבדיקות עברו.")


if __name__ == "__main__":
    main()

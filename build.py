#!/usr/bin/env python3
"""בונה את האתר: docs/data.json ושני פידים של יומן (ICS).

קלט:  config.json, data/games_scraped.json, data/games_manual.json, data/overrides.json,
      data/holidays.json, data/venues.json, state/scrape_status.json, state/seq.json
פלט:  docs/data.json, docs/maccabi3.ics (משחקים), docs/maccabi3-with-holidays.ics

כללים:
- שגיאה בנתונים ידניים (תאריך שגוי, סטטוס לא מוכר) מפילה את הבנייה בכוונה: האתר הקודם נשאר באוויר
  ובעל הריפו מקבל מייל מ-GitHub. עדיף משחק שלא התעדכן מאשר משחק שנעלם בשקט.
- UID ביומן = מזהה המשחק (GameId של ההתאחדות). SEQUENCE עולה אוטומטית כשפרט מהותי משתנה,
  כדי שאפליקציות יומן יעדכנו את האירוע הקיים ולא ייצרו כפילות.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Jerusalem")
STATUSES = {"scheduled", "postponed", "cancelled"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SIG_FIELDS = ("date", "time", "opponent", "home", "venue", "address", "status", "gather", "duration_min", "note", "round")
HOLIDAY_FEED_TYPES = {"break", "off", "short_day", "disputed"}

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Asia/Jerusalem
BEGIN:STANDARD
DTSTART:19701025T020000
TZOFFSETFROM:+0300
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
TZNAME:IST
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700327T020000
TZOFFSETFROM:+0200
TZOFFSETTO:+0300
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=FR;BYMONTHDAY=23,24,25,26,27,28,29
TZNAME:IDT
END:DAYLIGHT
END:VTIMEZONE"""


# ---------------------------------------------------------------- io helpers
def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON לא תקין ב-{path}: {e}")


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8", newline="")
    return True


def utc_stamp(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------- games
def normalize(raw: dict, cfg: dict, venues: dict, errors: list[str], origin: str):
    g = dict(raw)
    label = f"{origin}: {g.get('date', '?')} {g.get('opponent', '?')}"

    d, t = g.get("date"), g.get("time")
    if not (isinstance(d, str) and DATE_RE.match(d)):
        errors.append(f"{label}: תאריך חייב להיות בפורמט YYYY-MM-DD")
        return None
    try:
        day = date.fromisoformat(d)
    except ValueError:
        errors.append(f"{label}: תאריך לא קיים")
        return None
    if not (isinstance(t, str) and TIME_RE.match(t)):
        errors.append(f"{label}: שעה חייבת להיות בפורמט HH:MM")
        return None
    opponent = (g.get("opponent") or "").strip()
    if not opponent:
        errors.append(f"{label}: חסר שם יריבה")
        return None
    status = g.get("status", "scheduled")
    if status not in STATUSES:
        errors.append(f"{label}: סטטוס '{status}' לא מוכר (מותר: {', '.join(sorted(STATUSES))})")
        return None

    g["status"], g["opponent"] = status, opponent
    g["venue"] = (g.get("venue") or "").strip()
    g["note"] = (g.get("note") or "").strip()
    g.setdefault("home", None)

    venue_info = venues.get(g["venue"], {})
    explicit = (g.get("address") or "").strip()
    if explicit:
        g["address"], g["address_verified"] = explicit, True
    elif (venue_info.get("address") or "").strip():
        g["address"], g["address_verified"] = venue_info["address"].strip(), bool(venue_info.get("verified"))
    else:
        g["address"], g["address_verified"] = "", False

    # בית/חוץ נקבע לפי האולם, לא לפי תווית 'מארחת' של ההתאחדות: בטורניר של 3 קבוצות
    # התווית נקבעת לכל משחק בנפרד, וההורים מתכוונים לשאלה 'האם זה באולם שלנו'.
    g["iva_home"] = g.get("home")
    home_venue = (cfg.get("home_venue") or "").strip()
    if g["venue"] and home_venue:
        g["home"] = g["venue"] == home_venue

    gm = int(g.get("gather_minutes", cfg["default_gather_min"]))
    g["gather_minutes"] = gm
    start = datetime.combine(day, time.fromisoformat(t))
    g["gather"] = (start - timedelta(minutes=gm)).strftime("%H:%M")
    g["duration_min"] = int(g.get("duration_min", cfg["default_duration_min"]))
    return g


def collect_games(cfg, venues, errors):
    scraped = load(ROOT_DATA / "games_scraped.json", {"games": []})
    manual_path = ROOT_DATA / "games_manual.json"
    manual = load(manual_path, {"games": []})
    overrides = load(ROOT_DATA / "overrides.json", {})

    # מזהים למשחקים ידניים חדשים: נכתבים בחזרה לקובץ כדי להישאר יציבים גם כשהתאריך משתנה.
    dirty = False
    for m in manual.get("games", []):
        if not m.get("id") and m.get("date") and m.get("opponent"):
            seed = f"{m['date']}|{m['opponent']}|{m.get('time', '')}"
            m["id"] = "m" + hashlib.sha1(seed.encode()).hexdigest()[:8]
            dirty = True
    if dirty and manual_path.exists():
        manual_path.write_text(json.dumps(manual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    merged: dict[str, dict] = {}
    for raw in scraped.get("games", []):
        merged[raw["id"]] = {**raw, "origin": "scraped"}
    for raw in manual.get("games", []):
        if raw.get("id") in merged:
            merged[raw["id"]] = {**merged[raw["id"]], **raw, "origin": "scraped+manual"}
        else:
            merged[raw.get("id") or f"anon-{len(merged)}"] = {**raw, "origin": "manual"}
    for gid, patch in overrides.items():
        if gid.startswith("_"):
            continue
        if gid in merged:
            merged[gid].update(patch)
            merged[gid]["origin"] += "+override"
        else:
            errors.append(f"overrides.json: המזהה '{gid}' לא קיים במשחקים")

    games = []
    for gid, raw in merged.items():
        raw["id"] = gid
        g = normalize(raw, cfg, venues, errors, raw["origin"])
        if g:
            games.append(g)
    games.sort(key=lambda g: (g["date"], g["time"], g["id"]))
    return games


def collect_holidays(cfg):
    h = load(ROOT_DATA / "holidays.json", {"items": []})
    level = cfg.get("school_level", "all")
    items = [i for i in h.get("items", []) if i.get("applies_to", "all") in ("all", level)]
    items.sort(key=lambda i: (i["start"], i["key"]))
    return items, h


def annotate_conflicts(games, holidays):
    for g in games:
        found = []
        for h in holidays:
            inside = h["start"] <= g["date"] <= h["end"]
            if not inside:
                continue
            if h["type"] in ("break", "off"):
                found.append({"name": h["name"], "kind": "holiday"})
            elif h["type"] == "disputed":
                found.append({"name": h["name"], "kind": "uncertain"})
            elif h["type"] == "short_day":
                found.append({"name": h["name"], "kind": "note"})
        g["conflicts"] = found


def update_state(games, state, now_iso):
    sigs = {}
    for g in games:
        sig_src = {k: g.get(k) for k in SIG_FIELDS}
        sig = hashlib.sha1(json.dumps(sig_src, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        s = state.get(g["id"])
        if s is None:
            s = state[g["id"]] = {"sig": sig, "seq": 0, "modified": now_iso, "first_seen": now_iso}
        elif s["sig"] != sig:
            s.update(sig=sig, seq=s["seq"] + 1, modified=now_iso)
        sigs[g["id"]] = sig
        g["seq"], g["modified"] = s["seq"], s["modified"]
        age = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - datetime.fromisoformat(s["modified"].replace("Z", "+00:00"))
        g["recently_changed"] = s["seq"] > 0 and age <= timedelta(days=7)
    return sigs


def load_standings(root):
    """דירוג הליגה (state/standings.json, נכתב ע"י הסורק). פגום או חסר: מתעלמים, לא עוצרים בנייה."""
    path = root / "state" / "standings.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"אזהרה: {path} פגום ({e}). הדירוג לא יוצג, הלוז נבנה כרגיל.", file=sys.stderr)
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return None
    rows = []
    for r in raw["rows"]:
        if not isinstance(r, dict) or not str(r.get("team", "")).strip():
            continue
        def n(k):
            v = r.get(k)
            return v if isinstance(v, int) and not isinstance(v, bool) else None
        rows.append({"rank": n("rank"), "team": str(r["team"]).strip(), "played": n("played"),
                     "wins": n("wins"), "losses": n("losses"), "diff": n("diff"), "points": n("points")})
    if not rows:
        return None
    return {"league": str(raw.get("league", "")), "region": str(raw.get("region", "")),
            "updated": raw.get("updated"), "rows": rows}


def build_days(games, cfg):
    """קיבוץ משחקים ליום טורניר: אותו תאריך ואותו אולם. כינוס אחד, בית/חוץ אחד, 3 קבוצות."""
    groups: dict = {}
    for g in games:
        groups.setdefault((g["date"], g["venue"]), []).append(g)
    days = []
    for (d, venue), gs in sorted(groups.items()):
        gs.sort(key=lambda g: (g["time"], g["id"]))
        first = gs[0]
        day = date.fromisoformat(d)
        first_start = datetime.combine(day, time.fromisoformat(first["time"]))
        gather = first_start - timedelta(minutes=first["gather_minutes"])
        end = max(datetime.combine(day, time.fromisoformat(g["time"])) + timedelta(minutes=g["duration_min"]) for g in gs)
        opponents: list[str] = []
        for g in gs:
            if g["opponent"] not in opponents:
                opponents.append(g["opponent"])
        statuses = {g["status"] for g in gs}
        status = "scheduled" if statuses == {"scheduled"} else (next(iter(statuses)) if len(statuses) == 1 else "mixed")
        for i, g in enumerate(gs):
            g["day_key"], g["day_index"], g["day_size"] = first["id"], i, len(gs)
            g["gather"] = gather.strftime("%H:%M") if i == 0 else None  # כינוס אחד ליום, לא לכל משחק
        days.append({
            "key": first["id"], "date": d, "venue": venue,
            "address": first["address"], "address_verified": first["address_verified"],
            "home": first["home"], "gather": gather.strftime("%H:%M"),
            "start_time": first["time"], "end_time": end.strftime("%H:%M"),
            "teams": [cfg.get("team_full_name", cfg["team_name"])] + opponents,
            "game_ids": [g["id"] for g in gs], "status": status,
        })
    return days


def update_day_state(days, sigs, state, now_iso):
    for d in days:
        raw = "|".join(sigs[i] for i in d["game_ids"]) + json.dumps([d["gather"], d["venue"], d["end_time"]], ensure_ascii=False)
        sig = hashlib.sha1(raw.encode()).hexdigest()
        key = "day:" + d["key"]
        s = state.get(key)
        if s is None:
            s = state[key] = {"sig": sig, "seq": 0, "modified": now_iso, "first_seen": now_iso}
        elif s["sig"] != sig:
            s.update(sig=sig, seq=s["seq"] + 1, modified=now_iso)
        d["seq"], d["modified"] = s["seq"], s["modified"]
        age = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - datetime.fromisoformat(s["modified"].replace("Z", "+00:00"))
        d["recently_changed"] = s["seq"] > 0 and age <= timedelta(days=7)


# ---------------------------------------------------------------- ICS
def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold(line: str) -> str:
    if len(line.encode("utf-8")) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        cb = ch.encode("utf-8")
        limit = 75 if not out else 74  # שורות המשך נפתחות ברווח
        if len(cur) + len(cb) > limit:
            out.append(cur.decode("utf-8"))
            cur = cb
        else:
            cur += cb
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def nav_url(g) -> str:
    q = g["address"] or g["venue"]
    return "https://waze.com/ul?q=" + urllib.parse.quote(q) + "&navigate=yes" if q else ""


def day_event(d, byid, cfg) -> list[str]:
    """אירוע יומן אחד ליום טורניר. מתחיל בכינוס ונגמר בסיום המשחק האחרון."""
    gs = [byid[i] for i in d["game_ids"]]
    day = date.fromisoformat(d["date"])
    start = datetime.combine(day, time.fromisoformat(d["gather"]))
    end = datetime.combine(day, time.fromisoformat(d["end_time"]))
    kind = {True: "בית", False: "חוץ"}.get(d["home"], "")
    times = ", ".join(g["time"] for g in gs)
    statuses = {g["status"] for g in gs}

    if len(gs) > 1:
        summary = f"{cfg['team_name']}: טורניר {kind} ({times})".replace("  ", " ")
    else:
        summary = f"{cfg['team_name']} נגד {gs[0]['opponent']} ({times})"
    if statuses == {"cancelled"}:
        summary = "[בוטל] " + summary
    elif statuses == {"postponed"}:
        summary = "[נדחה] " + summary
    elif statuses != {"scheduled"}:
        summary = "[שינוי] " + summary

    nav = nav_url(d)
    desc = [f"כינוס: {d['gather']}"]
    for g in gs:
        tag = {"postponed": " [נדחה]", "cancelled": " [בוטל]"}.get(g["status"], "")
        rnd = f" (מחזור {g['round']})" if g.get("round") else ""
        desc.append(f"{g['time']} נגד {g['opponent']}{rnd}{tag}")
    if len(d["teams"]) > 2:
        desc.append("הקבוצות בטורניר: " + ", ".join(d["teams"]))
    if d["venue"]:
        desc.append(f"אולם: {d['venue']}")
    if d["address"]:
        desc.append(f"כתובת: {d['address']}")
    if nav:
        desc.append(f"Waze: {nav}")
    for g in gs:
        if g["note"]:
            desc.append(g["note"])
    stamp = utc_stamp(d["modified"])

    lines = [
        "BEGIN:VEVENT",
        f"UID:{d['key']}@maccabi3.schedule",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"SEQUENCE:{d['seq']}",
        f"DTSTART;TZID=Asia/Jerusalem:{start:%Y%m%dT%H%M%S}",
        f"DTEND;TZID=Asia/Jerusalem:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{esc(summary)}",
    ]
    loc = ", ".join(x for x in (d["venue"], d["address"]) if x)
    if loc:
        lines.append(f"LOCATION:{esc(loc)}")
    lines.append(f"DESCRIPTION:{esc(chr(10).join(desc))}")
    lines.append("STATUS:" + ("CANCELLED" if statuses == {"cancelled"} else "TENTATIVE" if "postponed" in statuses else "CONFIRMED"))
    lines.append("END:VEVENT")
    return lines


def holiday_event(h, updated_iso) -> list[str]:
    start = date.fromisoformat(h["start"])
    end = date.fromisoformat(h["end"]) + timedelta(days=1)  # DTEND בכל-היום הוא בלעדי
    suffix = " (לא ודאי)" if h["type"] == "disputed" else ""
    label = {"break": "חופשה: ", "off": "אין לימודים: ", "short_day": "יום לימודים מקוצר: ", "disputed": "ייתכן שאין לימודים: "}[h["type"]]
    desc = [h["note"]] if h.get("note") else []
    if h.get("return_date"):
        desc.append(f"חזרה ללימודים: {h['return_date']}")
    desc.append("לוח חופשות משרד החינוך, נאסף ממקורות פומביים. לאימות מול חוזר המנכ\"ל.")
    stamp = utc_stamp(updated_iso)
    return [
        "BEGIN:VEVENT",
        f"UID:hol-{h['key']}@maccabi3.schedule",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
        f"DTEND;VALUE=DATE:{end:%Y%m%d}",
        f"SUMMARY:{esc(label + h['name'] + suffix)}",
        f"DESCRIPTION:{esc(chr(10).join(desc))}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def calendar(name: str, events: list[list[str]]) -> str:
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Maccabi3 Schedule//HE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(name)}",
        "X-WR-TIMEZONE:Asia/Jerusalem",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    body = head + VTIMEZONE.split("\n") + [ln for ev in events for ln in ev] + ["END:VCALENDAR"]
    return "\r\n".join(fold(ln) for ln in body) + "\r\n"


# ---------------------------------------------------------------- health
def check_health(cfg, status) -> int:
    if not status.get("configured"):
        return 0
    last_ok = status.get("last_ok_at")
    limit = timedelta(hours=cfg.get("stale_hours", 36))
    now = datetime.now(timezone.utc)
    if not last_ok:
        print("הסריקה מעולם לא הצליחה. שגיאה אחרונה: " + str(status.get("error")), file=sys.stderr)
        return 1
    age = now - datetime.fromisoformat(last_ok.replace("Z", "+00:00"))
    if age > limit:
        print(f"הסריקה האחרונה שהצליחה לפני {int(age.total_seconds() // 3600)} שעות. שגיאה: {status.get('error')}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- main
ROOT_DATA = ROOT / "data"


def main() -> int:
    global ROOT_DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT), help="תיקיית פרויקט (לבדיקות)")
    ap.add_argument("--now", help="זמן בנייה ISO UTC (לבדיקות)")
    ap.add_argument("--check-health", action="store_true", help="רק בדיקת בריאות הסריקה; יוצא 1 אם הנתונים ישנים מדי")
    args = ap.parse_args()

    root = Path(args.root)
    ROOT_DATA = root / "data"
    cfg = load(root / "config.json", None)
    if cfg is None:
        raise SystemExit("חסר config.json")
    status = load(root / "state" / "scrape_status.json", {"configured": False})

    if args.check_health:
        return check_health(cfg, status)

    now_iso = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: list[str] = []
    venues = load(ROOT_DATA / "venues.json", {}).get("venues", {})
    games = collect_games(cfg, venues, errors)
    holidays, hol_meta = collect_holidays(cfg)

    if errors:
        print("שגיאות בנתונים. הבנייה נעצרה כדי לא לפרסם נתונים שגויים:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1

    annotate_conflicts(games, holidays)
    days = build_days(games, cfg)
    state_path = root / "state" / "seq.json"
    state = load(state_path, {})
    sigs = update_state(games, state, now_iso)
    update_day_state(days, sigs, state, now_iso)
    write_if_changed(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    configured = bool(status.get("configured"))
    checked_at = (status.get("last_ok_at") if configured else None) or now_iso

    out = root / "docs"
    data = {
        "team": cfg["team_name"],
        "season": cfg.get("season", ""),
        "team_full": cfg.get("team_full_name", cfg["team_name"]),
        "league": cfg.get("league", ""),
        "standings": load_standings(root),
        "checked_at": checked_at,
        "built_at": now_iso,
        "stale_hours": cfg.get("stale_hours", 36),
        "warn_after_hours": cfg.get("warn_after_hours", 6),
        "scrape": {"configured": configured, "ok": status.get("ok"), "error": status.get("error")},
        "games": games,
        "days": days,
        "holidays": holidays,
        "holidays_updated": hol_meta.get("updated"),
        "holidays_note": hol_meta.get("verification_note"),
        "official_holidays_url": cfg.get("official_holidays_url"),
        "source_url": (cfg.get("sources") or {}).get("games_url"),
    }
    write_if_changed(out / "data.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    byid = {g["id"]: g for g in games}
    game_events = [day_event(d, byid, cfg) for d in days]
    hol_updated = (hol_meta.get("updated") or now_iso[:10]) + "T00:00:00Z"
    hol_events = [holiday_event(h, hol_updated) for h in holidays if h["type"] in HOLIDAY_FEED_TYPES]
    write_if_changed(out / "maccabi3.ics", calendar(f"{cfg['team_name']}: משחקים", game_events))
    write_if_changed(out / "maccabi3-with-holidays.ics", calendar(f"{cfg['team_name']}: משחקים וחופשות", game_events + hol_events))

    print(f"נבנו {len(games)} משחקים ב-{len(days)} ימי טורניר, {len(holidays)} פריטי חופשה ({'סריקה פעילה' if configured else 'נתונים ידניים'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

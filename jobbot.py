#!/usr/bin/env python3
"""
jobbot - polls company career APIs (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, Amazon), scores every opening against your resume profile in
config.json, and sends NEW matches to Telegram.

  python jobbot.py            normal run: alert on new matches
  python jobbot.py --dry-run  print all current matches, send nothing, save nothing
  python jobbot.py --check    test every company endpoint and show job counts
  python jobbot.py --test     send a test Telegram message
"""
import argparse, html, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SEEN_FILE = ROOT / "seen.json"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (personal job-alert bot)"
TIMEOUT = 25


# ----------------------------------------------------------------- helpers
def clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>|</p>|</li>", " \n ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def mkjob(jid, company, title, location, url, desc="", comp=None, detail=None):
    return {"id": jid, "company": company, "title": (title or "").strip(),
            "location": location or "", "url": url, "desc": desc or "",
            "comp": comp, "detail": detail}


# ----------------------------------------------------------------- fetchers
def f_greenhouse(c):
    r = S.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs",
              params={"content": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    return [mkjob(f"gh:{c['slug']}:{j['id']}", c["name"], j["title"],
                  (j.get("location") or {}).get("name"), j["absolute_url"],
                  clean(j.get("content")))
            for j in r.json().get("jobs", [])]


def f_lever(c):
    r = S.get(f"https://api.lever.co/v0/postings/{c['slug']}",
              params={"mode": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        loc = ", ".join(cat.get("allLocations") or []) or cat.get("location", "")
        desc = " ".join([j.get("descriptionPlain", "")]
                        + [clean(l.get("content")) for l in j.get("lists", [])]
                        + [j.get("additionalPlain", "")])
        sr = j.get("salaryRange")
        comp = ({"min": sr.get("min"), "max": sr.get("max"),
                 "currency": sr.get("currency"), "interval": sr.get("interval", "")}
                if sr else None)
        out.append(mkjob(f"lv:{c['slug']}:{j['id']}", c["name"], j.get("text"),
                         loc, j.get("hostedUrl"), desc, comp))
    return out


def f_ashby(c):
    r = S.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}",
              params={"includeCompensation": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = None
        for part in ((j.get("compensation") or {}).get("summaryComponents") or []):
            if part.get("compensationType") == "Salary":
                comp = {"min": part.get("minValue"), "max": part.get("maxValue"),
                        "currency": part.get("currencyCode"), "interval": part.get("interval", "")}
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        loc = ", ".join(x for x in locs if x) + (" (Remote)" if j.get("isRemote") else "")
        out.append(mkjob(f"ab:{c['slug']}:{j['id']}", c["name"], j.get("title"), loc,
                         j.get("jobUrl"), j.get("descriptionPlain", ""), comp))
    return out


def f_smartrecruiters(c):
    out, offset = [], 0
    while True:
        r = S.get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings",
                  params={"limit": 100, "offset": offset}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = data.get("content", [])
        for j in items:
            l = j.get("location") or {}
            country = "India" if (l.get("country") or "").lower() == "in" else l.get("country", "")
            loc = ", ".join(x for x in [l.get("city"), country] if x) + (" (Remote)" if l.get("remote") else "")
            out.append(mkjob(f"sr:{c['slug']}:{j['id']}", c["name"], j.get("name"), loc,
                             f"https://jobs.smartrecruiters.com/{c['slug']}/{j['id']}",
                             detail=("sr", c["slug"], j["id"])))
        offset += len(items)
        if not items or offset >= data.get("totalFound", 0) or offset >= 1000:
            return out


def f_workday(c):
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    out, seen = [], set()
    for q in CFG["search_queries"]:
        r = S.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20,
                                         "offset": 0, "searchText": q}, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("jobPostings", []):
            p = j.get("externalPath")
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(mkjob(f"wd:{c['tenant']}:{p}", c["name"], j.get("title"),
                             j.get("locationsText", ""), f"https://{c['host']}/{c['site']}{p}",
                             detail=("wd", base, p)))
        time.sleep(0.3)
    return out


def f_amazon(c):
    out, seen = [], set()
    locs = ["India"] + (CFG["international"]["amazon_countries"] if CFG["international"]["enabled"] else [])
    for q, where in [(q, w) for w in locs for q in CFG["search_queries"]]:
        r = S.get("https://www.amazon.jobs/en/search.json",
                  params={"base_query": q, "loc_query": where,
                          "result_limit": 50, "sort": "recent"}, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            jid = j.get("id_icims") or j.get("id")
            if jid in seen:
                continue
            seen.add(jid)
            desc = clean(" ".join([j.get("description", ""), j.get("basic_qualifications", ""),
                                   j.get("preferred_qualifications", "")]))
            loc = (j.get("normalized_location") or j.get("location") or "") + f", {where}"
            out.append(mkjob(f"amzn:{jid}", c["name"], j.get("title"), loc,
                             "https://www.amazon.jobs" + j.get("job_path", ""), desc))
        time.sleep(0.3)
    return out


def f_workable(c):
    out, token = [], None
    for _ in range(10):
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        r = S.post(f"https://apply.workable.com/api/v3/accounts/{c['slug']}/jobs", json=body, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data.get("results", []):
            l = j.get("location") or {}
            loc = ", ".join(x for x in [l.get("city"), l.get("country")] if x) + (" (Remote)" if j.get("remote") else "")
            out.append(mkjob(f"wk:{c['slug']}:{j['shortcode']}", c["name"], j.get("title"), loc,
                             f"https://apply.workable.com/{c['slug']}/j/{j['shortcode']}",
                             detail=("wk", c["slug"], j["shortcode"])))
        token = data.get("nextPage")
        if not token:
            break
    return out


def f_recruitee(c):
    r = S.get(f"https://{c['slug']}.recruitee.com/api/offers/", timeout=TIMEOUT)
    r.raise_for_status()
    return [mkjob(f"rc:{c['slug']}:{j['id']}", c["name"], j.get("title"),
                  ", ".join(x for x in [j.get("city"), j.get("country")] if x) + (" (Remote)" if j.get("remote") else ""),
                  j.get("careers_url"), clean((j.get("description") or "") + " " + (j.get("requirements") or "")))
            for j in r.json().get("offers", [])]


FETCHERS = {"greenhouse": f_greenhouse, "lever": f_lever, "ashby": f_ashby,
            "smartrecruiters": f_smartrecruiters, "workday": f_workday, "amazon": f_amazon,
            "workable": f_workable, "recruitee": f_recruitee}


def fetch_detail(j):
    """Some ATSs only return descriptions per job; fetch them lazily."""
    kind = j["detail"][0]
    if kind == "sr":
        _, slug, pid = j["detail"]
        r = S.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}", timeout=TIMEOUT)
        r.raise_for_status()
        secs = (r.json().get("jobAd") or {}).get("sections") or {}
        j["desc"] = clean(" ".join((secs.get(k) or {}).get("text", "")
                                   for k in ("jobDescription", "qualifications", "additionalInformation")))
    elif kind == "wk":
        _, slug, code = j["detail"]
        r = S.get(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{code}", timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        j["desc"] = clean(" ".join(d.get(k) or "" for k in ("description", "requirements")))
    elif kind == "wd":
        _, base, p = j["detail"]
        r = S.get(base + p, timeout=TIMEOUT)
        r.raise_for_status()
        info = r.json().get("jobPostingInfo") or {}
        j["desc"] = clean(info.get("jobDescription"))
        j["location"] = ", ".join([info.get("location", "")] + (info.get("additionalLocations") or []))


# ----------------------------------------------------------------- matching
def title_rule(title):
    t = title.lower()
    if re.search(CFG["title_exclude_regex"], t):
        return None
    for rule in CFG["title_rules"]:
        if re.search(rule["regex"], t):
            return rule
    return None


def region(loc):
    """india | remote (open worldwide) | abroad | unknown"""
    l = loc.lower()
    if not l.strip() or re.search(r"\d+\s+locations", l):
        return "unknown"
    if any(k in l for k in CFG["locations_allow"]):
        return "india"
    if re.search(r"\b(remote|anywhere|worldwide|global)\b", l) and not re.search(CFG["locations_block_regex"], l):
        return "remote"
    return "abroad"


EXP_RE = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years?|yrs?)", re.I)


def experience(desc):
    """Return (min_years_asked, snippet) from experience mentions, or (None, None)."""
    best = None
    for m in EXP_RE.finditer(desc):
        ctx = desc[max(0, m.start() - 70): m.end() + 70].lower()
        if "experience" not in ctx and "exp " not in ctx:
            continue
        n = int(m.group(1))
        if n <= 20 and (best is None or n < best[0]):
            best = (n, m.group(0).strip())
    return best or (None, None)


LPA_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:-|–|to)\s*(\d{1,3}(?:\.\d)?)\s*(?:lpa|lakhs?|l\b)", re.I)


def pay_lpa(j):
    """Best guess at the top of the posted annual pay range in LPA, or None."""
    c = j.get("comp")
    if c and (c.get("currency") or "").upper() == "INR" and (c.get("max") or c.get("min")):
        v = float(c.get("max") or c.get("min"))
        if "month" in (c.get("interval") or "").lower():
            v *= 12
        return round(v / 1e5, 1)
    m = LPA_RE.search(j["desc"])
    return float(m.group(2)) if m else None


def skills(text):
    t = text.lower()
    hits = [kw for kw in CFG["skills"]
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", t)]
    return min(sum(CFG["skills"][k] for k in hits), 55), hits


def sponsorship(desc):
    """Return (True, snippet) if the posting offers visa/relocation, (False, _) if it
    rules sponsorship out, (None, None) if it doesn't say."""
    I = CFG["international"]
    neg = re.search(I["no_sponsor_regex"], desc, re.I)
    if neg:
        return False, None
    pos = re.search(I["sponsor_regex"], desc, re.I)
    if not pos:
        return None, None
    s = desc[max(0, pos.start() - 60): pos.end() + 80].strip()
    return True, ("…" + s + "…")


def evaluate(j):
    """Return a match dict if the job suits the resume, else None.
    Tracks: india (≥ min LPA), remote (open worldwide), intl (abroad + visa sponsorship)."""
    rule = title_rule(j["title"])
    if not rule:
        return None
    where = region(j["location"])
    I = CFG["international"]
    track, visa_txt = "india", None
    if where == "remote":
        track = "remote"
    elif where == "abroad":
        if not I["enabled"]:
            return None
        ok, visa_txt = sponsorship(j["desc"])
        if not ok:
            return None
        track = "intl"
    max_years = CFG["max_years_asked"] if track == "india" else I["max_years_asked"]
    yrs, yrs_txt = experience(j["desc"])
    if yrs is not None and yrs > max_years:
        return None
    lpa = pay_lpa(j)
    if lpa is not None and lpa < CFG["min_lpa"]:
        return None
    pts, hits = skills(j["title"] + " " + j["desc"])
    score = rule["points"] + pts
    if re.search(r"\b(ii|2)\b", j["title"].lower()):
        score -= 5  # SDE-2 level: reachable at 1.5 yrs but a stretch
    score = max(0, min(score, 100))
    if score < rule.get("threshold", CFG["threshold"]):
        return None
    return {**j, "score": score, "label": rule["label"], "hits": hits,
            "yrs_txt": yrs_txt, "lpa": lpa, "track": track, "visa_txt": visa_txt}


# ----------------------------------------------------------------- telegram
def tg_send(text, intl=False):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = (intl and os.getenv("TELEGRAM_CHAT_ID_INTL")) or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[no Telegram credentials; printing instead]\n" + re.sub(r"<[^>]+>", "", text) + "\n")
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=TIMEOUT)
    if r.status_code == 429:
        time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
        return tg_send(text, intl)
    if not r.ok:
        print("Telegram error:", r.status_code, r.text)
    time.sleep(1.1)


def fmt(m):
    e = html.escape
    c = m.get("comp")
    if m["lpa"] is not None:
        pay = f"{m['lpa']} LPA (posted, top of range)"
    elif c and (c.get("max") or c.get("min")):
        pay = f"{c.get('min') or ''}–{c.get('max') or ''} {c.get('currency') or ''} {c.get('interval') or ''} (posted)"
    elif m["track"] == "india":
        pay = f"not posted (company on your ≥{CFG['min_lpa']} LPA list)"
    else:
        pay = "not posted"
    head = {"india": "🟢", "remote": "🌍 <b>REMOTE</b> ·", "intl": "✈️ <b>ABROAD + VISA</b> ·"}[m["track"]]
    lines = []
    if m.get("flutter_stack"):
        lines.append("💙 <b>FLUTTER STACK</b>")
    lines += [f"{head} <b>{e(m['title'])}</b> — {e(m['company'])}",
             f"📍 {e(m['location'][:120] or 'not listed')}",
             f"🎯 Match {m['score']}/100 · {e(m['label'])} role",
             f"🧰 {e(', '.join(m['hits'][:10]) or '—')}",
             f"⏳ Experience asked: {e(m['yrs_txt'] or 'not stated')}",
             f"💰 Pay: {e(pay)}"]
    if m["track"] == "intl":
        lines.append(f"🛂 Posting says: <i>{e(m['visa_txt'][:220])}</i>")
    elif m["track"] == "remote":
        lines.append("🛂 Check the posting says India-based candidates are eligible")
    lines.append(f"🔗 <a href=\"{e(m['url'])}\">Open &amp; apply</a>")
    return "\n".join(lines)


# ----------------------------------------------------------------- auto-detect
RESOLVED_FILE = ROOT / "resolved.json"
AUTO_ORDER = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee"]


def _json_load(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def resolve(c, cache):
    """For {"ats": "auto", "slugs": [...]}: find which job board the company uses.
    A candidate only counts if it has at least one India job, so a same-named
    foreign company isn't picked up. Results are cached in resolved.json;
    companies not found are retried once a day."""
    if c["ats"] != "auto":
        return c, None
    hit = cache.get(c["name"])
    if hit and hit.get("ats"):
        return {**c, **hit}, None
    if hit and hit.get("miss_at", "") > (datetime.now(timezone.utc) - timedelta(days=1)).isoformat():
        return None, None
    for slug in c["slugs"]:
        for ats in AUTO_ORDER:
            cand = {**c, "ats": ats, "slug": slug}
            try:
                jobs = FETCHERS[ats](cand)
            except Exception:
                continue
            if (c.get("any_location") and jobs) or any(re.search(r"india|bengaluru|bangalore|chennai|hyderabad|pune|mumbai|gurgaon|gurugram|noida|delhi",
                             j["location"].lower()) for j in jobs):
                cache[c["name"]] = {"ats": ats, "slug": slug}
                return cand, jobs
    cache[c["name"]] = {"miss_at": datetime.now(timezone.utc).isoformat()}
    return None, None


def collect(c, cache, seen, dry):
    """Worker: fetch one company, return (ids_still_open, new_jobs_with_details, error)."""
    try:
        rc, jobs = resolve(c, cache)
        if rc is None:
            return [], [], f"{c['name']}: board not found (tried {', '.join(c.get('slugs', []))})"
        if jobs is None:
            try:
                jobs = FETCHERS[rc["ats"]](rc)
            except requests.HTTPError as ex:
                if rc is not c and ex.response is not None and ex.response.status_code == 404:
                    cache.pop(c["name"], None)  # board moved; re-detect next run
                raise
    except Exception as ex:
        return [], [], f"{c['name']}: {ex}"
    still_open, new = [], []
    for j in jobs:
        rule = title_rule(j["title"])
        if not rule:
            continue
        j["flutter_stack"] = c.get("flutter_stack", False) and rule["label"] in ("Flutter", "Mobile")
        still_open.append(j["id"])
        if j["id"] in seen and not dry:
            continue
        if j["detail"]:
            try:
                fetch_detail(j)
                time.sleep(0.2)
            except Exception:
                still_open.pop()  # don't mark seen; retry next run
                continue
        new.append(j)
    return still_open, new, None


def gather(dry):
    from concurrent.futures import ThreadPoolExecutor
    seen, cache = _json_load(SEEN_FILE), _json_load(RESOLVED_FILE)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda c: collect(c, cache, seen, dry), CFG["companies"]))
    RESOLVED_FILE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return seen, results


def flutter_digest():
    """One bundled Telegram message listing every currently open role at
    your Flutter-stack companies (CRED, Grab, Nubank, Toyota Connected...).
    Lists any title that passed the Flutter/Mobile/Backend/SDE title filter —
    skips the stricter resume-score/visa-text/experience checks from evaluate(),
    since the point here is visibility into everything open, not a strict match."""
    _, results = gather(dry=True)
    jobs = [j for _, new, _ in results for j in new if j.get("flutter_stack")]
    jobs.sort(key=lambda j: j["company"])

    if not jobs:
        tg_send("💙 <b>FLUTTER STACK</b> — no open roles right now at your tracked Flutter companies.")
        return

    header = f"💙 <b>FLUTTER STACK</b> — {len(jobs)} open role(s) at companies known for Flutter apps\n\n"
    lines = [f"• <b>{html.escape(j['title'])}</b> — {html.escape(j['company'])}\n"
             f"  📍 {html.escape(j['location'][:80] or 'not listed')} · "
             f"<a href=\"{html.escape(j['url'])}\">apply</a>" for j in jobs]

    chunk, size = [header], len(header)
    for line in lines:
        if size + len(line) + 1 > 3800:
            tg_send("".join(chunk))
            chunk, size = [], 0
        chunk.append(line + "\n")
        size += len(line) + 1
    if chunk:
        tg_send("".join(chunk))
    print(f"{len(jobs)} Flutter-stack roles posted in group message")


# ----------------------------------------------------------------- main
def run(dry):
    first_run = not SEEN_FILE.exists()
    seen, results = gather(dry)
    now = datetime.now(timezone.utc).isoformat()
    matches, errors = [], []
    for still_open, new, err in results:
        if err:
            errors.append(err)
        for jid in still_open:
            seen[jid] = now
        for j in new:
            m = evaluate(j)
            if m:
                matches.append(m)

    matches.sort(key=lambda m: m["score"], reverse=True)
    for e in errors:
        print("FETCH ERROR", e, file=sys.stderr)

    if dry:
        for m in matches:
            print(re.sub(r"<[^>]+>", "", fmt(m)), "\n")
        print(f"{len(matches)} matches, {len(errors)} fetch errors")
        return

    cap = CFG["first_run_digest"] if first_run else CFG["max_alerts_per_run"]
    if first_run and matches:
        tg_send(f"👋 Job bot is live. {len(matches)} roles open right now match your resume; "
                f"here are the top {min(cap, len(matches))}. From now on you'll only get new ones.")
    for m in matches[:cap]:
        tg_send(fmt(m), intl=m["track"] != "india")
    if len(matches) > cap:
        rest = "\n".join(f"• {html.escape(m['title'])} — {html.escape(m['company'])} "
                         f"({m['score']}) <a href=\"{html.escape(m['url'])}\">link</a>"
                         for m in matches[cap:cap + 30])
        tg_send(f"➕ {len(matches) - cap} more matches:\n{rest}")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}  # forget closed postings
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True))
    print(f"{len(matches)} new matches alerted; {len(errors)} fetch errors")


def check():
    RESOLVED_FILE.unlink(missing_ok=True)  # force fresh detection
    seen, results = gather(dry=True)
    cache = _json_load(RESOLVED_FILE)
    ok = 0
    for c, (still_open, new, err) in zip(CFG["companies"], results):
        if err:
            print(f"FAIL  {c['name']:<22} {err.split(': ', 1)[-1][:90]}")
            continue
        ok += 1
        where = cache.get(c["name"], {})
        src = f"{where.get('ats', c['ats'])}/{where.get('slug', c.get('slug', ''))}"
        rel = sum(1 for j in new if evaluate(j))
        print(f"OK    {c['name']:<22} {src:<38} {len(still_open):>3} relevant titles, {rel:>2} match now"
              + (f"   e.g. {new[0]['title'][:40]} @ {new[0]['location'][:30]}" if c.get("any_location") and new else ""))
    print(f"\n{ok}/{len(CFG['companies'])} companies working")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--flutter-digest", action="store_true")
    a = ap.parse_args()
    if a.test:
        tg_send("✅ Job bot test message — Telegram is wired up.")
    elif a.check:
        check()
    elif a.flutter_digest:
        flutter_digest()
    else:
        run(a.dry_run)

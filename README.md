# Flutter / Mobile / Backend job alert bot

Checks the career pages of ~190 product companies and startups (India + abroad) every 30 minutes, keeps only roles that fit the resume
(Flutter/Dart, BLoC, Firebase, offline-first, ~1.5 yrs experience), and sends new ones to Telegram.

## How it decides a role fits

1. **Title**: Flutter (45 pts), Mobile/Android/iOS (35), Backend (15), generic SDE (15). Senior, Staff, Lead,
   Manager, SDE-3, intern, QA etc. are dropped. SDE-2 / "II" roles stay, with a small penalty.
2. **Location**: India cities, or Remote that isn't tied to US/UK/EU etc.
3. **Experience**: dropped if the posting asks for more than 3 years minimum.
4. **Skills**: points for each resume skill found in the description (flutter, dart, bloc, firebase, dio, hive...).
5. **Pay**: if the posting shows an INR range below 18 LPA it's dropped. Most Indian postings don't show pay,
   so the company list itself is the 18 LPA filter (see below).
6. Alert if score ≥ 45 (≥ 30 for backend titles, since the resume is Flutter-heavy).

Tune all of this in `config.json`.

## Setup (about 15 minutes, free)

**1. Telegram bot**
- In Telegram, message **@BotFather** → `/newbot` → copy the **token**.
- Send any message to your new bot, then open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `"chat":{"id": ...}` — that's your **chat id**.

**2. Test locally**
```bash
pip install -r requirements.txt
python jobbot.py --check          # which company endpoints work
python jobbot.py --dry-run        # see current matches in the terminal
export TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...
python jobbot.py --test           # test message on your phone
```

**3. Run it on GitHub for free, 24/7**
- Create a new **private** GitHub repo and push this folder (including `.github/`).
- Repo → Settings → Secrets and variables → Actions → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Actions tab → *job-alerts* → **Run workflow** once. The first run sends the top 10 current matches;
  after that you only get new postings.

**Make the repo public** if you want 30-minute checks: public repos get unlimited free Action minutes, and
nothing personal is stored in it (no resume, no tokens; those stay in Secrets). With ~135 companies a
private repo would run out of its 2,000 free minutes a month, so if you keep it private change the cron in
`.github/workflows/jobbot.yml` to every 2 hours (`0 */2 * * *`).

## International jobs with visa sponsorship

Besides India roles, the bot sends two more kinds of alert:

- **✈️ ABROAD + VISA**: roles in Europe, the UK, the Middle East, Japan, Singapore, Australia etc. that
  **explicitly mention visa sponsorship or relocation support** in the posting. The alert quotes the exact
  sentence so you can judge it. Postings that say "unable to sponsor", "must have the right to work",
  "citizens only", or say nothing about visas are dropped.
- **🌍 REMOTE**: remote roles open worldwide (not "Remote – US/EU"). No visa needed; check that the
  posting accepts India-based candidates.

It checks every company's non-India jobs (Stripe, Airbnb, Atlassian, Grab, Agoda, Amazon abroad...) plus ~55
companies known for hiring from abroad: Adyen, Spotify, Delivery Hero, N26, Picnic, Revolut, Monzo, Klarna,
Zalando, Careem, Talabat, Tabby, Mercari, PayPay, Rakuten, Canva, Nubank, Deel and more (see `COMPANIES.md`).

Settings are in `config.json` → `"international"`:
- `enabled`: set to `false` to switch it off.
- `max_years_asked`: 4 by default (roles abroad usually ask for a bit more experience).
- `amazon_countries`: which countries to search on amazon.jobs.

**Separate chat (optional):** to get international alerts in a different Telegram chat, add a secret
`TELEGRAM_CHAT_ID_INTL` (for example, a Telegram group with your bot in it). Without it, everything goes
to the same chat.

For these companies `--check` also shows a sample job, so you can confirm it found the right company.

Keyword matching is a filter, not a guarantee. Always confirm sponsorship with the recruiter before
investing time in a process.

## Company list (~190 companies)

Fintech (PhonePe, Razorpay, CRED, Groww, Zerodha, Jupiter, Fi, slice, Jar, Navi, Juspay, Zeta, OneCard...),
consumer and gaming (Swiggy, Zomato/Blinkit, Zepto, Meesho, Urban Company, Rapido, ShareChat, Dream11,
Games24x7, Pocket FM...), SaaS/AI (Postman, BrowserStack, Chargebee, Atlan, Hasura, Sarvam AI, MoEngage...)
and global product companies with India teams (Amazon, Walmart, Salesforce, Adobe, Stripe, Airbnb, Atlassian...).
Full list in `COMPANIES.md`.

They were picked because they're well-regarded product companies that commonly pay 18 LPA+ at the 1–3 year
level. Pay changes, so check levels.fyi / AmbitionBox for any company you care about and delete ones that don't fit.

**You don't need to know each company's job site.** Most entries are `"ats": "auto"` with a few slug guesses.
The bot tries Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Recruitee, and only accepts a board that
has jobs in India (so it won't confuse "slice" the Indian fintech with a US pizza company). What it finds is
saved in `resolved.json`.

**Run `python jobbot.py --check` first.** It shows which companies were found and where. Some will say
"board not found": those companies use their own portal or a system with no public feed (Darwinbox, Keka,
Zoho Recruit, custom sites). For those:
- If you find a board URL on their careers page, set it explicitly using the table below, or
- Delete them from the list and follow them via LinkedIn job alerts instead.

| Job URL looks like | config entry |
|---|---|
| `boards.greenhouse.io/XYZ` or `job-boards.greenhouse.io/XYZ` | `"ats": "greenhouse", "slug": "XYZ"` |
| `jobs.lever.co/XYZ` | `"ats": "lever", "slug": "XYZ"` |
| `jobs.ashbyhq.com/XYZ` | `"ats": "ashby", "slug": "XYZ"` |
| `jobs.smartrecruiters.com/XYZ` | `"ats": "smartrecruiters", "slug": "XYZ"` |
| `apply.workable.com/XYZ` | `"ats": "workable", "slug": "XYZ"` |
| `XYZ.recruitee.com` | `"ats": "recruitee", "slug": "XYZ"` |
| `ABC.wd5.myworkdayjobs.com/SITE` | `"ats": "workday", "host": "ABC.wd5.myworkdayjobs.com", "tenant": "ABC", "site": "SITE"` |

To add a company: `{"name": "Company", "ats": "auto", "slugs": ["guess1", "guess2"], "category": "Fintech"}`.

## Handy tweaks
- Re-scan everything after changing filters: delete `seen.json`.
- Add a skill: add `"keyword": points` under `skills`.
- Only want Flutter/mobile? Delete the Backend and SDE entries under `title_rules`.

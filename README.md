# Karuta Wishlist Evaluator

> **Discord account and Terms of Service warning**
>
> This project connects with a Discord user-account token through
> [`discord.py-self`](https://github.com/dolfies/discord.py-self). Discord
> prohibits automating normal user accounts; use may result in account action
> or termination. A private server does not change those rules. Use this only
> if the applicable platform rules permit it.

This Python client sends `kd` in configured channels, watches for Karuta drop
announcements, and reads the wishlist evaluator's follow-up analysis. The
analysis is detected **by its content** (a `Drop expires <t:...>` line, or
card lines such as `` `♡` `2` · `#85233` · `◈1` ``) no matter which account
posted it — including your own account (self-bot evaluators) or another bot.
The configured `WISHLIST_EVALUATOR_ID` is still honoured as an additional
trigger. **Components V2 messages** — where the text lives in Text Display
components and `content` is empty, which discord.py-self versions without
V2 support read as a completely empty message — are re-read through the raw
REST payload so the analysis is not lost. `MESSAGE_UPDATE` edits are settled
straight from the gateway payload (the freshest copy, before any fetch), and
a raw fetch that misses — `http.get_message` uses `?limit=1&around=`, which
can skip a message that is still the newest in the channel — falls back to a
deterministic `?before=` history window. Content-detected analyses are
acted on after a 0.6 s settle; messages from the configured evaluator bot
keep the 3 s settle, and empty messages are re-fetched from the API in case
their content lands via an edit. Analyses whose expiry timestamp is more
than 30 s in the past (quotes or stale edits) are ignored, while a fresh
expiry accepts a message that predates the announcement (evaluators that
reuse one persistent message per channel). Karuta drop images are accepted
both as attachments and as embed images. **Karuta's current drop layout is
a single message holding every card**, with the number keycaps 1–N Karuta
pre-adds as grab buttons; when that keycap row is visible the bot reacts
immediately (older per-card-message layouts still get a 2 s arrival grace).
When print numbers are present, the **print-number range decides first**
and the wishlist count only ranks cards inside the same range:

1. **Prints 1–9 (SP)** — always grabbed; if several, the one with the
   **highest wishlist count** (print #7 with ♡500 beats print #2 with ♡100).
2. **Prints 10–99 (LP)** — always grabbed; highest wishlist count
   (#99 with ♡40 beats #11 with ♡20).
3. **Prints 100–999 (MP)** — grab with **7+ wishes** (highest count first);
   cards with 1–6 wishes are only a last resort.
4. **Prints 1000+ (HP)** — grab **only** with 7+ wishes, never fewer.

A card from a lower-priority range never beats a qualifying card from a
higher-priority range, no matter its wishlist count. Equal wishlist counts
inside a range go to the lower print number, then the lower slot. If
nothing qualifies, `GRAB_LAST_RESORT` in `evaluator.py` decides whether the
best remaining low-wishlist card is still taken (`True`, the default) or
the drop is skipped (`False`). Cards whose print the evaluator did not
show follow a pure wishlist ladder (7+ preferred, then any positive count).
`HIGH_PRINT_WISHLIST_THRESHOLD` (default 7) sets the 7+ bar.

Evaluator lines should look like:

```text
<:yel_flower:...> <:no_1:...> `♡` `2` · **Character Name** · Series Name
```

Print-number entries such as `` `#1` · `◈2` `` may appear between the wishlist
count and character name. The `no_N` number is treated as the original drop
slot. The evaluator's Discord user ID defaults to `1380936713639166082` and
can be overridden with `WISHLIST_EVALUATOR_ID`; with content-based detection
the ID no longer has to be correct for the bot to see the analysis.

## How the grab works

Grabbing a card in Karuta is an **emoji reaction on the drop message**, not a
button click. Karuta's current layout puts every card of a drop into ONE
message and pre-adds the number keycaps 1–N as the grab buttons, so the bot
resolves the emoji straight from the message it is about to react to:

1. `KARUTA_GRAB_EMOJI` (optional `.env` setting) always wins when set —
   paste the emoji exactly as `<:name:id>` / `<a:name:id>` for a custom
   emoji, or a plain unicode emoji.
2. **Combined drop (the current layout):** when two or more number keycaps
   are already on the drop message, the chosen slot's keycap IS the grab
   button and is used immediately — no waiting, because a custom emoji will
   never arrive. A player's custom reaction on the message is ignored in
   favour of the keycap row.
3. Otherwise (per-card-message layouts) the first **non-number** reaction
   already on the card message is used — Karuta's own grab emoji. Number
   keycaps are skipped because a lone one may be a player's pick.
4. If the message arrived before Karuta added the emoji (its reaction list
   is empty), a fresh REST copy of the message is read, and — still empty —
   the bot waits up to 15 s for the emoji to appear live.
5. Last resort: the slot's number keycap (`1️⃣`–`4️⃣`), for servers where
   cards are picked with numbered reactions.

The previous release reacted with the slot's number keycap but first waited
up to 15 s for a non-number emoji that never appears on combined drops —
pushing the reaction to T+21 s of the ~60 s claim window. That wait is now
skipped whenever the keycap row is visible, so the reaction typically lands
around T+4 s. If the evaluation arrives before all card messages of a
per-card layout, a short 2 s grace lets the rest arrive before falling back
to the closest tracked message; a visible keycap row proves the
single-message layout and skips the grace entirely.

## Reading the logs

While a drop is pending, every message in a watched channel is traced:

```text
[watch:create] id=1004 author=1568054352600506440 bot=True ... flags=32768 v2=True embeds=0 attachments=0 components=0 chars=0 preview=''
Evaluator message 1004 keeps its text in components (Components V2); read 340 chars via the raw API.
Evaluator message 1004 settled (author; author=1568054352600506440 embeds=0 chars=340): '<:yel_flower:1> ...'
Drop decision input in channel 123 - 3 card(s) evaluated: slot 1 (♡1 #85420, Rick); slot 2 (♡10 #44190, Yuki Mitsuya); slot 3 (♡1 #85487, Moeka China).
Combined drop: slot 2 is grabbed with its keycap on the single drop message.
Slot 2 -> Yuki Mitsuya (♡10, #44190; high print (1000+), wishlist 7+).
Reacted 2️⃣ to 1002 (slot 2, emoji from slot keycap on the drop message); awaiting confirmation.
```

When no card meets the rules (for example the same drop with all three
cards at ♡1), the decision lines are followed by:

```text
Skipping drop in channel 123 - no card met the grabbing rules (see the evaluated cards above).
```

- `[watch:...]` shows who sent each message and exactly what the bot could
  read from it (`chars` = text length, `v2=True` = Components V2 message).
  `chars=0` with `v2=True` is the signature of an analysis whose text lives
  in components: the bot reads it via the raw API. `chars=0` with `v2=False`
  from your own account means the message really arrived empty — the
  re-fetch paths cover a late edit.
- `Wishlist analysis detected from author ...` is printed whenever a message
  matches the analysis format, regardless of author — including your own
  account (self-bot evaluators).
- `settled (content; ...)` / `settled (author; ...)` / `settled (components; ...)`
  / `settled (recheck; ...)` tell you why a message was treated as evaluator
  output: content match, configured bot ID, Components V2 flag, or an
  empty-message re-fetch.
- `keeps its text in components ... read N chars via the raw API` confirms
  the V2 fallback recovered a message the library itself cannot parse.
- `[watch:edit-raw]` traces the raw `MESSAGE_UPDATE` payload of every edit in
  a watched channel — if a drop is still missed, this line shows exactly
  what the gateway delivered and from which author.
- `Raw fetch of message ... failed ...; trying a history window.` means the
  one-message lookup missed and the bot re-read the message from a
  `?before=` window instead; the drop is not lost either way.
- `Drop image received in <channel> (N tracked)` counts the card messages of
  the drop; on the current single-message layout N stays 1 and the slots are
  grabbed with their keycaps on that one message.
- `Drop decision input in channel ... - N card(s) evaluated: ...` lists
  **every** card the evaluator reported, with the wish count, print number
  and name the choice is made from — it is the proof that the whole
  evaluation was read before deciding.
- `Combined drop: slot N is grabbed with its keycap on the single drop
  message.` confirms the single-message layout was detected, so no
  card-arrival grace was needed.
- `Slot N -> <card> (♡wishes, #print; <rule>)` names the rule that won the
  card: `single print (1-9)`, `low print (10-99)`,
  `mid print (100-999), wishlist 7+`, `high print (1000+), wishlist 7+`,
  `no print info, wishlist 7+`, or one of the last-resort labels — every
  decision can be checked against the priority rules by reading the log.
- `Skipping drop in channel ... - no card met the grabbing rules` means
  every card on the drop failed the tier rules (for example all high
  prints with fewer than 7 wishes): nothing was grabbed, by design.
- `Reacted <emoji> to <message> (slot N, emoji from <source>)` names the
  exact emoji used and where it came from (`slot keycap on the drop
  message`, `slot keycap in a fresh REST copy`, `reactions on the card
  message`, `reactions in a fresh REST copy`, `live reaction (...)`,
  `KARUTA_GRAB_EMOJI override`, or `slot keycap fallback`). If it ever picks
  the wrong emoji, set `KARUTA_GRAB_EMOJI` in `.env` to the exact emoji
  shown under the card in Discord.
- `Evaluation ready in channel ...; N of M card message(s) tracked; waiting
  up to 2.0s for the rest.` only appears on per-card-message layouts (no
  keycap row detected); the grab fires once the rest arrive (or right after
  the grace).
- `Re-fetch N/M of message ...: author=... edited=... chars=...` shows what
  each empty-message re-fetch actually returned — author, whether the
  message had been edited, the text length and a preview — so a
  still-empty analysis is diagnosable from the log alone.

The client waits a random 18–85 seconds before its first `kd` command. Later
commands are scheduled at least 30 minutes apart, with additional timing
variation. Successful grabs use a 10-minute cooldown; failed or unconfirmed
grabs use a 75-second cooldown. The command schedule and installation ID are
saved in `kd-state.json` under `DATA_DIR`.

## Current grab selection behavior

The grabbing choice now implements the tiered priority rules exactly as
specified: **range first, wishlist count within the range**:

1. **Within a range, wishlist count now ranks the cards.** The previous
   build compared raw print numbers inside 1–999, so #2 with ♡100 beat #7
   with ♡500 and #11 with ♡20 beat #99 with ♡40 — both wrong under the
   rules, both fixed (and covered by regression tests using those exact
   examples).
2. **High prints (1000+) need 7+ wishes.** The previous build grabbed any
   positively-wished high print — that is how the 05:17 drop's ♡1 cards
   were grabbed. Under the rules, a drop whose cards are all high prints
   with ♡1–6 is now **skipped by design**: for the exact Rick / Yuki
   Mitsuya / Moeka China drop (all ♡1), the correct action is *no grab at
   all*. Expect far fewer grabs — only SP/LP cards, mid prints with 7+
   wishes, or high prints with 7+ wishes are ever taken.
3. **Mid prints (100–999) prefer 7+ wishes**; 1–6-wish mid prints (and
   cards without print info) are only grabbed as a last resort when
   nothing else qualifies. Set `GRAB_LAST_RESORT = False` in
   `evaluator.py` to skip those drops instead. `FALLBACK_TO_WISHLIST` is
   gone — the 7+ threshold replaces it.
4. **The log names the rule that won**, e.g.
   `Slot 2 -> Yuki Mitsuya (♡10, #44190; high print (1000+), wishlist 7+)`
   or `Skipping drop ... - no card met the grabbing rules`, so every
   decision can be checked against the rules by reading the log alone.

## Superseded behavior (historical)

> Historical note: round 6's tiebreak fix (point 1 below) was superseded by
> the round 7 tiered rules — under those rules the 05:17 drop is skipped
> entirely rather than grabbing the rarest card.

Diagnosed from the 2026-11-06 05:17 drop (`Rick` / `Yuki Mitsuya` /
`Moeka China`), where the bot read all three evaluation lines but still
grabbed **slot 1 (Rick, print #85,420 — the most common of the three)**
instead of **slot 2 (Yuki Mitsuya, print #44,190 — the rarest)**:

1. **Wishlist-tie tiebreak fixed** (`evaluator.py`). All three cards were
   ♡1 with prints above 999, so the wishlist fallback ran — and its old
   tiebreak (`max(wishlist, -slot)`) preferred the **lowest slot number**,
   ignoring print rarity entirely. Ties on wish count are now broken by the
   **lowest print number**, then by slot. The same evaluation now selects
   slot 2 / Yuki Mitsuya (covered by a regression test using the exact
   production message).
2. **Combined-drop detection** (`bot.py`). The previous build assumed
   "Karuta posts one message per card", so on the real single-message layout
   it logged `1 of 3 card message(s) tracked` and waited 2 s for messages
   that never come. When the number-keycap row 1–N is visible on the tracked
   drop message, the bot now recognises the single-message layout and reacts
   immediately.
3. **Faster grab emoji** (`bot.py`). On a combined drop the grab buttons ARE
   the number keycaps, but the old resolver waited up to 15 s for a
   non-number emoji first (that is exactly where the T+21 s reaction came
   from). With the keycap row visible, the slot's keycap is used at once —
   the reaction now typically lands ~T+4 s of the ~60 s claim window. A
   player's custom reaction can no longer be mistaken for the grab emoji
   either.
4. **Decision transparency** (`bot.py`). Every drop now logs
   `Drop decision input ... N card(s) evaluated: slot 1 (...); slot 2 (...); ...`
   before the choice, so the log itself proves whether the whole evaluation
   was read. The choice line names the rule that picked the card
   (`rare print` / `wishlist fallback, lowest print among ♡ ties`).

Note on grab confirmation: `Grab ok; cooldown 600s` is printed when Karuta
posts a message matching "grabbed/got/took … card" that mentions you within
~30 s of the reaction. The expired drop message is not a reliable indicator
of what was claimed, so the definitive check is your own collection
(`k!c`, newest first).

## Deploy on Wispbyte

Create a server using Wispbyte's Python image. See Wispbyte's
[Discord bot hosting guide](https://wispbyte.com/how-to-host-discord-bot-wispbyte)
and [startup settings guide](https://wispbyte.com/kb/startup-settings). Use
the Python 3.11 image used by this server (or another supported Python 3 image).
The current plan has 512 MB RAM, 1 GB disk, and 35% CPU; member chunking and
member caching are disabled by default to limit memory use.

1. Upload the project files, including `requirements.txt` and the `wheels/`
   directory, in the file manager, or deploy the repository through Wispbyte's
   GitHub integration. Set `REQUIREMENTS_FILE` to `requirements.txt` and leave
   **User Uploaded Files** set to `0` (false), or Wispbyte will skip dependency
   installation. Its startup command installs requirements on each start;
   check logs for pip errors.

   The exact `discord.py-self` wheel is included locally because the PyPI 2.1
   release does not provide the `HeadersContext` API used here. Other
   dependencies are resolved from PyPI; Wispbyte says its free tier supports
   outbound internet and package installation. If installation fails, inspect
   the startup log for the failing package. The included wheel is built from
   the pinned upstream commit and has a checksum in `wheels/SHA256SUMS`.

2. Set the **App py file** variable (`PY_FILE`) to `bot.py`; the default
   startup command points to `main.py`. The generated startup command launches
   the selected file. Keep its dependency-install clauses, but add `-u` to the
   final Python invocation so it ends with
   `/usr/local/bin/python -u /home/container/bot.py`. `Procfile` is not used by
   this Wispbyte startup template.

3. Configure `DISCORD_USER_TOKEN`, `TARGET_CHANNELS`, and `DROP_BOT_ID` using
   a private `.env` file uploaded through Wispbyte's file manager; the listed
   startup variables are not the app's Discord configuration. Matching
   non-empty process environment values override `.env`.

   Also configure these optional values as needed:

   | Variable | Required | Description |
   |---|---|---|
   | `WISHLIST_EVALUATOR_ID` | No | Evaluator ID; defaults to `1380936713639166082` |
   | `KARUTA_GRAB_EMOJI` | No | Hard-code the grab emoji (e.g. `<:kx:123>` or 💜); by default it is read from the chosen card message's own reactions |
   | `DATA_DIR` | Recommended | Writable persistent directory for state and lock files |
   | `DISCORD_TIMEZONE` | Recommended | Your own IANA timezone; set it explicitly if the container reports UTC |
   | `CAPTCHA_KEY` | No | Operator-supplied, single-use CAPTCHA response; if unset or already used, the process waits before exiting when challenged |

   Keep `.env` private and never commit it; `.gitignore` excludes it. Other
   optional settings are listed in `.env.example`. Keep **Additional Python
   Packages** empty; dependencies are installed from `requirements.txt` on
   startup. `PYTHONUNBUFFERED` in `.env` would be too late to configure the
   interpreter; the `-u` startup option enables unbuffered logs.

Use persistent storage for `DATA_DIR` so restarts retain the schedule, and run
only one copy against the same account and data directory. The bot retries a
busy instance lock a few times before exiting. No port or HTTP health check is
needed. Check the worker logs for `Connected as ...` to confirm login. A
CAPTCHA does not prompt for console input: provide a valid `CAPTCHA_KEY` through
the panel or `.env` and restart. Clear CAPTCHA_KEY after you see `Connected as`;
the token is single-use. Keep Auto Update off until .env exists on the server,
since .env is gitignored and will not be cloned.
Hosting network latency can make reactions slower than a local run.
Wispbyte documents shared-IP Discord login limits; see its
[Discord rate-limits guide](https://wispbyte.com/kb/discord-rate-limits) if the
logs show repeated 429 or Cloudflare 1015 responses.

See [DEPLOYMENT.md](DEPLOYMENT.md) for additional deployment notes.

## Run locally

Python 3.10 or newer is required.

1. Copy `.env.example` to `.env`.
2. Set `DISCORD_USER_TOKEN`, `TARGET_CHANNELS`, and `DROP_BOT_ID`.
3. Install dependencies and start the client:

   ```sh
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   python bot.py
   ```

   On Windows, activate the virtual environment with
   `.venv\Scripts\activate` instead.

## Testing

Run the complete unit-test suite with:

```sh
python -m unittest
```

## Credentials and security

- Never commit `.env`, account tokens, or other credentials. `.env` is ignored
  by Git; use `.env.example` as a template.
- If a credential is exposed, revoke or rotate it with the provider immediately.
- Do not share credentials in bug reports. See [SECURITY.md](SECURITY.md) for
  vulnerability reporting guidance.

## License

This project is distributed under the ISC License; see [LICENSE](LICENSE).
Third-party packages and the bundled `discord.py-self` wheel remain subject
to their respective licenses.

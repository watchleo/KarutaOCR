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
both as attachments and as embed images, and **every** card message of a drop
is tracked (Karuta posts one message per card), so slot *N* of the analysis
grabs the *N*-th card message. When print numbers are present, it grabs the
card with the lowest print number up to 999. If every print
number exceeds 999, it falls back to the highest positive wishlist count;
set `FALLBACK_TO_WISHLIST = False` in `evaluator.py` to skip such drops
instead. If the evaluator omits print numbers, it chooses the slot with the
highest positive wishlist count, breaking ties in favor of the lower slot
number. It skips if all counts are zero.

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

Grabbing a card in Karuta is an **emoji reaction on the card message**, not a
button click. Karuta pre-adds the grab emoji to every card message it drops,
so the bot reads the emoji straight from the message it is about to react to:

1. `KARUTA_GRAB_EMOJI` (optional `.env` setting) always wins when set —
   paste the emoji exactly as `<:name:id>` / `<a:name:id>` for a custom
   emoji, or a plain unicode emoji.
2. Otherwise the first **non-number** reaction already on the chosen card
   message is used — that is Karuta's own grab emoji. Number keycaps are
   skipped because they mark slot picks, not the grab.
3. If the message arrived before Karuta added the emoji (its reaction list
   is empty), a fresh REST copy of the message is read, and — still empty —
   the bot waits up to 15 s for the emoji to appear live.
4. Last resort: the slot's number keycap (`1️⃣`–`4️⃣`), for servers where
   cards are picked with numbered reactions.

The previous release reacted with the slot's number keycap and then waited
for **another player** to add that same number first — which never happens —
so every grab silently expired with "`2️⃣ never appeared.`" and the bot gave
up without reacting. That gate is gone: the bot reacts as soon as the
analysis and the card messages are both ready. If the evaluation arrives
before all card messages, a short 2 s grace lets the rest arrive before
falling back to the closest tracked message.

## Reading the logs

While a drop is pending, every message in a watched channel is traced:

```text
[watch:create] id=1004 author=1568054352600506440 bot=True ... flags=32768 v2=True embeds=0 attachments=0 components=0 chars=0 preview=''
Evaluator message 1004 keeps its text in components (Components V2); read 340 chars via the raw API.
Evaluator message 1004 settled (author; author=1568054352600506440 embeds=0 chars=340): '<:yel_flower:1> ...'
Slot 2 -> Shou Daliuwan (♡1, #18481).
Reacted <:kx:987654321> to 1002 (slot 2, emoji from reactions on the card message); awaiting confirmation.
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
  the drop; slot N of the analysis grabs the N-th one.
- `Reacted <emoji> to <message> (slot N, emoji from <source>)` names the
  exact emoji used and where it came from (`reactions on the card message`,
  `reactions in a fresh REST copy`, `live reaction (...)`,
  `KARUTA_GRAB_EMOJI override`, or `slot keycap fallback`). If it ever picks
  the wrong emoji, set `KARUTA_GRAB_EMOJI` in `.env` to the exact emoji
  shown under the card in Discord.
- `Evaluation ready in channel ...; N of M card message(s) tracked; waiting
  up to 2.0s for the rest.` means the analysis beat some card messages to
  the bot; the grab fires once the rest arrive (or right after the grace).
- `Re-fetch N/M of message ...: author=... edited=... chars=...` shows what
  each empty-message re-fetch actually returned — author, whether the
  message had been edited, the text length and a preview — so a
  still-empty analysis is diagnosable from the log alone.

The client waits a random 18–85 seconds before its first `kd` command. Later
commands are scheduled at least 30 minutes apart, with additional timing
variation. Successful grabs use a 10-minute cooldown; failed or unconfirmed
grabs use a 75-second cooldown. The command schedule and installation ID are
saved in `kd-state.json` under `DATA_DIR`.

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

Run the evaluator parser tests with:

```sh
python -m unittest
```

## Credentials and security

- Never commit `.env`, account tokens, or other credentials. `.env` is ignored
  by Git; use `.env.example` as a template.
- If a credential is exposed, revoke or rotate it with the provider immediately.
- Do not share credentials in bug reports. See [SECURITY.md](SECURITY.md) for
  vulnerability reporting guidance.

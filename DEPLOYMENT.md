# Wispbyte deployment

This is a long-running Python client, not a web server. Create a Wispbyte
server using its Python Docker image. See Wispbyte's
[Discord bot hosting guide](https://wispbyte.com/how-to-host-discord-bot-wispbyte)
and [startup settings guide](https://wispbyte.com/kb/startup-settings). The
current server configuration uses Python 3.11 with 512 MB RAM, 1 GB disk, and
35% CPU; the app's member chunking and member cache defaults are disabled for
the limited memory.

## Files and startup

Upload the project files, including `requirements.txt` and the `wheels/`
directory, using Wispbyte's file manager, or deploy the repository through its
GitHub integration. Wispbyte's startup configuration installs the selected
requirements file on every start. Set `REQUIREMENTS_FILE` to
`requirements.txt`, and leave **User Uploaded Files** at `0` (false); setting it
to `1` skips the install commands. Check startup logs for pip errors.

The pinned `discord.py-self` wheel is included locally because the PyPI 2.1
release does not provide the `HeadersContext` API used by this project. The
remaining dependencies are resolved from PyPI. Wispbyte says its free tier
supports outbound internet access, including package installation. If pip still
fails, check the startup log for the specific package and error. The wheel is
built from the pinned upstream commit; its SHA-256 is recorded in
`wheels/SHA256SUMS`.

Wispbyte's default startup points to `/home/container/main.py`. Set the
**App py file** variable (`PY_FILE`) to `bot.py`. Its generated startup command
then launches the correct entry point. For Git deployment, configure
`GIT_ADDRESS` and optionally `BRANCH`. For file-manager uploads, upload all app
files, the full `wheels/` directory, and `requirements.txt`.

The startup command runs Python through a pipe. Keep the installation clauses
from the default command, but add `-u` to its final Python invocation so it
ends with `/usr/local/bin/python -u /home/container/bot.py`.

`Procfile` is not needed by this startup template. The selected Python image
and `PY_FILE` setting control the launch command; no port or HTTP health check
is required.

## Environment variables

The panel provides fields defined by the selected server configuration; its
listed startup variables do not include this app's Discord settings. Upload a
private `.env` file in the file manager, based on `.env.example`, and set the
required values there. The app also accepts matching process environment
variables, which take precedence when non-empty. Never commit `.env` or put a
live token in the startup command.

| Variable | Required | Value |
|---|---|---|
| `DISCORD_USER_TOKEN` | Yes | Token for the account used by the client |
| `TARGET_CHANNELS` | Yes | Comma-separated channel IDs |
| `DROP_BOT_ID` | Yes | ID of the bot that posts drop announcements |
| `WISHLIST_EVALUATOR_ID` | No | Evaluator bot ID; defaults to the value in `.env.example` |
| `CAPTCHA_KEY` | No | Operator-supplied, single-use CAPTCHA response; if unset or already used, the process waits before exiting when challenged |
| `DATA_DIR` | Recommended | Writable persistent directory for state and instance lock; defaults to the application directory |
| `DISCORD_TIMEZONE` | Recommended | Your own IANA timezone; set it explicitly if the container reports UTC |

Other optional settings and their defaults are listed in `.env.example`.
Configure only values you need; keep secrets private.

Keep the **Additional Python Packages** field empty; dependencies are declared
in `requirements.txt`. Setting `PYTHONUNBUFFERED` in `.env` would not change
interpreter buffering because `.env` is loaded after Python starts; use the
`-u` startup option instead.

The process writes `kd-state.json` and `.kd.pid` to `DATA_DIR`. Use a persistent
directory so restarts retain the scheduler state, and run only one copy against
the same account and data directory. A busy instance lock is retried a few
times before the process exits.

## Verify the deployment

Start the worker and check its console logs. A successful login logs
`Connected as ...`. Clear CAPTCHA_KEY after you see `Connected as`; the token
is single-use. Keep Auto Update off until .env exists on the server, since .env
is gitignored and will not be cloned. If the process exits, check the first
configuration, dependency, or login error in the logs before restarting it.
CAPTCHA handling does not read console input: set a valid `CAPTCHA_KEY` in the
panel or private `.env` and restart.

The `CHUNK_GUILDS` default is off and `CACHE_ALL_MEMBERS` should remain off to
limit memory use on small plans. Hosting network latency can make reactions
slower than a local run. Wispbyte documents shared-IP Discord login limits;
see its [Discord rate-limits guide](https://wispbyte.com/kb/discord-rate-limits)
if logs show repeated 429 or Cloudflare 1015 responses.

This project automates a Discord user account. Discord prohibits automating
normal user accounts, and using it may lead to account enforcement. Deploy only
if you have confirmed the relevant platform rules permit your use.

# KarutaOCR

A Discord user bot that OCRs character name screenshots and reacts to the most popular anime character.

## Setup

1. Create a `.env` file in the project root.
2. Put your Discord user account token into `DISCORD_USER_TOKEN`.
3. Add your OCR.Space API key to `OCR_API_KEY`.
4. Add a Hugging Face access token with Inference Providers access to `HF_TOKEN`. The default model is `Qwen/Qwen3-8B`; set `HF_MODEL` to another chat model available to your account if needed.
5. Add a Tavily API key to `TAVILY_API_KEY` and/or an Exa API key to `EXA_API_KEY` for web search evidence. Tavily is tried first; Exa is used if Tavily is unavailable or returns no results. Either provider can be used alone.
6. Optionally add `TARGET_CHANNELS` as a comma-separated list of channel IDs.
7. Set `DROP_BOT_ID` to the user ID of the bot that announces and posts the card drop.
8. Optionally set `TEST_MODE_CONTROL_USER_ID` to the ID of the account allowed to toggle test mode by DM. If unset, commands from the logged-in account itself are accepted.
9. Start the bot:

   ```bash
   npm install
   npm start
   ```

   Example `.env`:

   ```env
   OCR_API_KEY=your_ocr_space_key
   HF_TOKEN=your_hugging_face_access_token
   HF_MODEL=Qwen/Qwen3-8B
   TAVILY_API_KEY=your_tavily_api_key
   EXA_API_KEY=your_exa_api_key
   DISCORD_USER_TOKEN=your_discord_user_token
   TARGET_CHANNELS=123456789012345678
   DROP_BOT_ID=your_karuta_bot_user_id
   TEST_MODE_CONTROL_USER_ID=your_control_account_user_id
   ```

The user bot sends `kd` no more than once every 30 minutes. After sending `kd`, it waits for a matching `dropping` announcement from `DROP_BOT_ID`, including active-server messages such as `I'm dropping 4 cards since this server is currently active!`. It supports the announcement and image arriving in the same message, and ignores unrelated messages. After a successful emoji grab, it waits 10 minutes before grabbing again. It only reacts after OCR extracts names and the AniList popularity lookup completes.

OCR.Space positional data groups each horizontal card column and retains its scanned series. AniList matches must agree with both the character name and an associated media title, reducing false matches when unrelated characters share a name. When AniList cannot confidently match every card, the bot fetches Tavily and/or Exa web results and provides their titles and snippets to the configured Hugging Face chat model as evidence. Hugging Face Inference Providers, Tavily, and Exa have account-dependent availability and usage limits. Cards that remain uncertain are skipped; the bot does not react only when none can be evaluated confidently.

When at least one card is confidently evaluated, uncertain cards are skipped and the best eligible card is selected; the bot does not react if every card is uncertain. A card with a print number below 100 takes priority; otherwise, the highest popularity score wins. Send `.teston` or `.testoff` in a DM from the configured control account to enable or disable test mode. Test mode recognizes personal-drop announcements mentioning any user and attempts every detected drop without the normal grab cooldown; it does not change the scheduled `kd` interval or configured channel and drop-bot checks.

> This uses a Discord user account token rather than a bot token. Automating a user account may violate Discord's Terms of Service; use it only where permitted.

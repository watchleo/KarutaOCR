require("dotenv").config();

const fs = require("fs");
const path = require("path");
const { Client } = require("discord.js-selfbot-v13");
const { GoogleGenAI } = require("@google/genai");
const axios = require("axios");
const FormData = require("form-data");
const stringSimilarity = require("string-similarity");

const OCR_API_KEY = process.env.OCR_API_KEY;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const GEMINI_MODEL = process.env.GEMINI_MODEL || "gemini-3.1-flash-lite";
const DISCORD_USER_TOKEN = process.env.DISCORD_USER_TOKEN || process.env.DISCORD_TOKEN;
const TARGET_CHANNELS = (process.env.TARGET_CHANNEL || process.env.TARGET_CHANNELS || "")
  .split(",")
  .map((id) => id.trim())
  .filter(Boolean)
  .slice(0, 1);
const DROP_BOT_ID = process.env.DROP_BOT_ID || "";
const KD_STATE_FILE = path.join(__dirname, "kd-state.json");

const randomBetween = (min, max) => {
  if (max < min) [min, max] = [max, min];
  return Math.floor(Math.random() * (max - min + 1)) + min;
};

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const KdState = {
  lastCommandAt: 0,
  lastGrabAt: 0,
  lastGrabSuccess: false,
  processing: new Set(),
  pendingDrops: new Map(),
  nextScheduledCheck: null,
};

function loadKdState() {
  try {
    const state = JSON.parse(fs.readFileSync(KD_STATE_FILE, "utf8"));
    if (Number.isFinite(state.lastCommandAt) && state.lastCommandAt > 0) {
      KdState.lastCommandAt = state.lastCommandAt;
      console.log(`Restored last kd timestamp: ${new Date(state.lastCommandAt).toISOString()}`);
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      console.error("Could not restore kd state:", error.message);
    }
  }
}

function saveKdState() {
  try {
    fs.writeFileSync(
      KD_STATE_FILE,
      JSON.stringify({ lastCommandAt: KdState.lastCommandAt }, null, 2) + "\n"
    );
  } catch (error) {
    console.error("Could not save kd state:", error.message);
  }
}

const KdConfig = {
  startupDelayMs: 30 * 1000,
  commandIntervalMs: 30 * 60 * 1000,
  commandJitterMs: 45 * 1000,
  grabCooldownMs: 10 * 60 * 1000,
  failedGrabCooldownMs: 60 * 1000,
  grabCooldownJitterMs: 60 * 1000,
  hardRateLimitMs: 20 * 1000,
  hardRateLimitJitterMs: 5 * 1000,
  announcementTimeoutMs: 30 * 1000,
  preCommandDelayMs: [200, 1200],
  channelDelayMs: [200, 700],
  reactionDelayMs: [300, 1200],
};

function validateConfig() {
  const missing = [];

  if (!OCR_API_KEY) missing.push("OCR_API_KEY");
  if (!DISCORD_USER_TOKEN) missing.push("DISCORD_USER_TOKEN");

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variables: ${missing.join(", ")}. ` +
      "Set your Discord user token in DISCORD_USER_TOKEN for user-bot mode."
    );
  }
}

const client = new Client();
const gemini = GEMINI_API_KEY ? new GoogleGenAI({ apiKey: GEMINI_API_KEY }) : null;
loadKdState();

// ==============================
// OCR FUNCTION
// ==============================
async function ocrSpace(imageUrl) {
  try {
    const formData = new FormData();
    formData.append("url", imageUrl);
    formData.append("apikey", OCR_API_KEY);
    formData.append("language", "eng");
    formData.append("isOverlayRequired", "true");

    const res = await axios.post(
      "https://api.ocr.space/parse/image",
      formData,
      { headers: formData.getHeaders() }
    );

    if (res.data.IsErroredOnProcessing) return null;

    const parsedResult = res.data.ParsedResults[0];
    return {
      text: parsedResult.ParsedText,
      lines: parsedResult.TextOverlay?.Lines || [],
    };
  } catch (err) {
    console.log(err);
    return null;
  }
}

// ==============================
// EXTRACT NAMES
// ==============================
function extractNames(ocrResult) {
  const overlayLines = (ocrResult.lines || [])
    .map((line) => {
      const words = line.Words || [];
      const left = words.length ? Math.min(...words.map((word) => word.Left)) : line.Left;
      const top = words.length ? Math.min(...words.map((word) => word.Top)) : line.Top;
      const right = words.length
        ? Math.max(...words.map((word) => word.Left + word.Width))
        : left + (line.Width || 0);

      return {
        text: (line.LineText || words.map((word) => word.WordText).join(" ")).trim(),
        left,
        top,
        center: (left + right) / 2,
      };
    })
    .filter((line) => line.text && Number.isFinite(line.center) && Number.isFinite(line.top));

  const metadataLines = overlayLines.filter((line) => /\b\d{4,7}\s*[•·.,]?\s*\d{1,2}\b/.test(line.text));
  if (metadataLines.length > 0) {
    return metadataLines
      .sort((a, b) => a.center - b.center)
      .map((metadataLine) => {
        const columnLines = overlayLines
          .slice()
          .sort((a, b) => a.top - b.top)
          .filter((line) => Math.abs(line.center - metadataLine.center) < 120);
        const metadataIndex = columnLines.findIndex((line) => line === metadataLine);
        const precedingLines = columnLines.slice(0, metadataIndex);
        return precedingLines[0]?.text;
      })
      .filter(Boolean)
      .slice(0, 4);
  }

  const lines = ocrResult.text
    .split("\n")
    .map(l => l.trim())
    .filter(l => l && l.length > 2);

  const cleaned = lines
    .filter(line => !/^(anime|character|characters|rating|score|season|episode|title|series)$/i.test(line))
    .filter(line => !/^\d+$/.test(line))
    .filter(line => !/[|\\/]/.test(line));

  const names = [];
  for (let i = 0; i < cleaned.length; i += 2) {
    const candidate = cleaned[i]?.trim();
    if (candidate) names.push(candidate);
  }

  return names.slice(0, 4);
}

// ==============================
// CLEAN NAME
// ==============================
function cleanName(name) {
  return name
    .toLowerCase()
    .replace(/[^\w\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// ==============================
// SMART MATCHER (AniList + similarity)
// ==============================
async function getBestCharacterMatch(rawName) {
  const cleaned = cleanName(rawName);

  const query = `
    query ($search: String) {
      Page(page: 1, perPage: 5) {
        characters(search: $search) {
          name { full }
          favourites
        }
      }
    }
  `;

  try {
    const res = await axios.post("https://graphql.anilist.co", {
      query,
      variables: { search: rawName }
    });

    const chars = res.data.data.Page.characters;

    if (!chars || chars.length === 0) {
      return { name: rawName, score: 0, found: false, source: "anilist" };
    }

    let best = chars[0];
    let bestScore = 0;

    for (let c of chars) {
      const sim = stringSimilarity.compareTwoStrings(
        cleaned,
        cleanName(c.name.full)
      );

      if (sim > bestScore) {
        bestScore = sim;
        best = c;
      }
    }

    return {
      name: best.name.full,
      score: best.favourites || 0,
      found: bestScore >= 0.35,
      source: "anilist"
    };

  } catch (err) {
    console.error(`AniList lookup failed for ${rawName}:`, err.message);
    return { name: rawName, score: 0, found: false, source: "anilist" };
  }
}

// ==============================
// GET POPULARITY FOR ALL
// ==============================
async function getPopularity(names) {
  const results = [];

  for (let i = 0; i < names.length; i++) {
    const name = names[i];
    const match = await getBestCharacterMatch(name);
    results.push({ ...match, index: i });
  }

  if (!results.some((result) => !result.found)) return results;

  if (!gemini) {
    console.log("Gemini fallback is not configured; unresolved names cannot be evaluated.");
    return results;
  }

  return evaluateWithGemini(names, results);
}

async function evaluateWithGemini(names, anilistResults) {
  const prompt = [
    "You are ranking characters for a Discord card grab.",
    "Use Google Search grounding to identify every character and compare current overall popularity.",
    "Characters may come from anime, manga, games, or other media.",
    "Return ONLY valid JSON with this exact shape:",
    '{"characters":[{"index":0,"identity":"...","series":"...","popularityScore":0,"confidence":0,"evidence":"..."}]}',
    "index must match the input index. popularityScore must be an integer from 0 to 100, where 100 is most popular among these cards.",
    "confidence must be a number from 0 to 1. Do not invent an identity; use confidence 0 when uncertain.",
    `OCR names: ${JSON.stringify(names)}`,
    `AniList results: ${JSON.stringify(anilistResults.map(({ index, name, score, found }) => ({ index, name, score, found })))} `
  ].join("\n");

  try {
    console.log(`AniList missed a name; asking Gemini (${GEMINI_MODEL}) with Google Search grounding.`);
    const response = await gemini.models.generateContent({
      model: GEMINI_MODEL,
      contents: prompt,
      config: {
        responseMimeType: "application/json",
        tools: [{ googleSearch: {} }],
        temperature: 0.1,
      },
    });

    const parsed = JSON.parse(response.text.trim());
    const evaluations = parsed.characters;
    if (!Array.isArray(evaluations) || evaluations.length !== names.length) {
      throw new Error("Gemini returned an incomplete character evaluation");
    }

    const byIndex = new Map(evaluations.map((evaluation) => [evaluation.index, evaluation]));
    const evaluated = names.map((inputName, index) => {
      const evaluation = byIndex.get(index);
      if (!evaluation || !evaluation.identity || evaluation.confidence < 0.5) {
        return { index, name: inputName, score: 0, found: false, source: "gemini" };
      }

      return {
        index,
        name: evaluation.identity,
        series: evaluation.series,
        score: Number(evaluation.popularityScore) || 0,
        confidence: evaluation.confidence,
        evidence: evaluation.evidence,
        found: true,
        source: "gemini",
      };
    });

    console.log(`Gemini evaluation:\n${evaluated
      .map((result) => `- ${result.name} (${result.series || "unknown series"}): ${result.score}/100, confidence ${result.confidence || 0}`)
      .join("\n")}`);
    return evaluated;
  } catch (error) {
    console.error("Gemini fallback failed; no reaction will be sent:", error.message);
    return anilistResults;
  }
}

// ==============================
// DROP / CLAIM LOGIC
// ==============================
const reactionEmojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"];

function canSendKdNow() {
  const now = Date.now();
  const elapsedSinceLastCommand = now - KdState.lastCommandAt;

  if (KdState.lastCommandAt === 0) {
    return true;
  }

  const minimumInterval = KdConfig.commandIntervalMs + randomBetween(0, KdConfig.commandJitterMs);
  const minimumHardRate = KdConfig.hardRateLimitMs + randomBetween(0, KdConfig.hardRateLimitJitterMs);

  return elapsedSinceLastCommand >= minimumInterval && elapsedSinceLastCommand >= minimumHardRate;
}

function canGrabNow() {
  const cooldownMs = KdState.lastGrabSuccess
    ? KdConfig.grabCooldownMs + randomBetween(0, KdConfig.grabCooldownJitterMs)
    : KdConfig.failedGrabCooldownMs;

  return Date.now() - KdState.lastGrabAt >= cooldownMs;
}

function markGrabResult(success) {
  KdState.lastGrabAt = Date.now();
  KdState.lastGrabSuccess = Boolean(success);
  console.log(`Grab result recorded: ${success ? "success" : "failed"}; cooldown updated.`);
}

function scheduleNextKdCheck() {
  if (KdState.nextScheduledCheck) {
    clearTimeout(KdState.nextScheduledCheck);
  }

  const nextDelay = randomBetween(
    KdConfig.commandIntervalMs,
    KdConfig.commandIntervalMs + KdConfig.commandJitterMs
  );

  KdState.nextScheduledCheck = setTimeout(() => {
    if (!KdState.processing.size) {
      sendKdDrop().catch((error) => {
        console.error("KD send failed:", error);
      });
    }
    scheduleNextKdCheck();
  }, nextDelay);
}

async function sendKdDrop() {
  if (!TARGET_CHANNELS.length) {
    console.error("TARGET_CHANNELS is required; no kd command was sent.");
    return;
  }

  const channels = TARGET_CHANNELS.length > 1
    ? [...TARGET_CHANNELS].sort(() => Math.random() - 0.5)
    : TARGET_CHANNELS;

  const now = Date.now();
  const eligible = canSendKdNow();

  if (!eligible) return;

  const previousLastCommandAt = KdState.lastCommandAt;
  KdState.lastCommandAt = now;
  let sent = false;

  await delay(randomBetween(KdConfig.preCommandDelayMs[0], KdConfig.preCommandDelayMs[1]));

  for (const [index, channelId] of channels.entries()) {
    const channel = client.channels.cache.get(channelId);
    if (!channel || typeof channel.send !== "function") continue;

    try {
      KdState.pendingDrops.set(channelId, {
        expiresAt: Date.now() + KdConfig.announcementTimeoutMs,
      });

      if (index > 0) {
        await delay(randomBetween(KdConfig.channelDelayMs[0], KdConfig.channelDelayMs[1]));
      }

      await channel.send("kd");
      sent = true;
      saveKdState();
      console.log(`Sent kd in ${channelId}; waiting for the drop announcement.`);
    } catch (error) {
      KdState.pendingDrops.delete(channelId);
      console.error(`Failed to send kd in channel ${channelId}:`, error.message);
    }
  }

  if (!sent) {
    KdState.lastCommandAt = previousLastCommandAt;
  }
}

function isImageMessage(message) {
  const attachment = message.attachments.first();
  return attachment?.contentType?.startsWith("image") ? attachment : null;
}

function isDropAnnouncement(message) {
  if (DROP_BOT_ID && message.author.id !== DROP_BOT_ID) return false;
  const content = message.content || "";
  const isActiveServerDrop = /\b(?:i'm|i am)\s+dropping\s+\d+\s+cards?\b/i.test(content);
  const isMentionedUserDrop = /<@!?\d+>\s+is\s+dropping\s+\d+\s+cards?\b/i.test(content);
  if (!isActiveServerDrop && !isMentionedUserDrop) return false;

  if (DROP_BOT_ID) return true;

  const userId = client.user?.id;
  return Boolean(userId && message.mentions.users.has(userId));
}

function isGrabSuccessMessage(message) {
  const content = message.content || "";
  return /\byou\b.*\b(?:grabbed|got|took)\b.*\bcard\b/i.test(content);
}

function isGrabFailureMessage(message) {
  const content = message.content || "";
  return /\byou\b.*\b(?:lost|missed|failed to claim|couldn't claim|could not claim)\b.*\bcard\b/i.test(content);
}

function isGrabStatusMessage(message) {
  return DROP_BOT_ID
    ? message.author.id === DROP_BOT_ID
    : Boolean(message.author?.bot);
}

async function handleCardDrop(message) {
  if (KdState.processing.has(message.id)) return;
  KdState.processing.add(message.id);

  try {
    const attachment = isImageMessage(message);
    if (!attachment) return;

    if (!canGrabNow()) {
      console.log(`Grab cooldown is active for ${message.id}; no reaction sent.`);
      return;
    }

    console.log(`Running OCR for drop image ${message.id}.`);
    const text = await ocrSpace(attachment.url);
    if (!text) {
      console.log(`OCR returned no text for ${message.id}; no reaction sent.`);
      return;
    }

    console.log(`OCR text for ${message.id}:\n${text.text.trim()}`);
    const names = extractNames(text);
    if (!names.length) {
      console.log(`OCR found no character names for ${message.id}; no reaction sent.`);
      return;
    }

    console.log(`Extracted character names: ${names.join(", ")}`);
    console.log(`OCR found ${names.length} names for ${message.id}; checking AniList popularity.`);
    const results = await getPopularity(names);
    if (!results.length) {
      console.log(`Popularity lookup returned no results for ${message.id}; no reaction sent.`);
      return;
    }

    if (results.some((result) => !result.found)) {
      console.log(`At least one character could not be confidently evaluated; no reaction sent.`);
      return;
    }

    console.log(
      `Popularity evaluation:\n${results
        .map((result) => `- ${result.name}: ${result.score} favourites (slot ${result.index + 1})`)
        .join("\n")}`
    );
    results.sort((a, b) => b.score - a.score);

    const chosen = results[0];
    const slotIndex = Math.min(Math.max(chosen.index, 0), reactionEmojis.length - 1);
    const emoji = reactionEmojis[slotIndex];

    console.log(`Most popular match for ${message.id}: ${chosen.name} (${chosen.score}); reacting ${emoji}.`);
    try {
      const reactionDelay = randomBetween(KdConfig.reactionDelayMs[0], KdConfig.reactionDelayMs[1]);
      console.log(`Waiting ${reactionDelay}ms before reacting to ${message.id}.`);
      await delay(reactionDelay);
      await message.react(emoji);
      markGrabResult(true);
      console.log(`Grab completed; 10-minute success cooldown is now active.`);
    } catch (error) {
      markGrabResult(false);
      console.error(`Grab failed for ${message.id}; 1-minute failure cooldown is now active:`, error.message);
    }
  } catch (error) {
    console.error("Card drop processing failed:", error);
  } finally {
    KdState.processing.delete(message.id);
  }
}

client.on("ready", () => {
  console.log(`Logged in as ${client.user.tag}`);

  setTimeout(() => {
    sendKdDrop().catch((error) => {
      console.error("Initial KD send failed:", error);
    });
  }, KdConfig.startupDelayMs);

  scheduleNextKdCheck();
});

client.on("messageCreate", async (message) => {
  if (!message.author?.bot || message.author.id === client.user?.id) return;

  if (!isGrabStatusMessage(message)) return;

  if (isGrabSuccessMessage(message)) {
    markGrabResult(true);
    console.log(`Successful grab confirmation detected: ${message.content}`);
    return;
  }

  if (isGrabFailureMessage(message)) {
    markGrabResult(false);
    console.log(`Grab failure detected: ${message.content}`);
    return;
  }

  const channelId = message.channel.id;

  if (isDropAnnouncement(message)) {
    if (!TARGET_CHANNELS.includes(channelId)) return;

    if (isImageMessage(message)) {
      console.log(`Drop announcement with image received in ${channelId}; starting OCR.`);
      await handleCardDrop(message);
      return;
    }

    KdState.pendingDrops.set(channelId, {
      expiresAt: Date.now() + KdConfig.announcementTimeoutMs,
      announcementId: message.id,
      announcementBotId: message.author.id,
    });
    console.log(`Drop announcement detected in ${channelId}; waiting for its image.`);
    return;
  }

  const pendingDrop = KdState.pendingDrops.get(channelId);
  if (!pendingDrop) return;

  if (Date.now() > pendingDrop.expiresAt) {
    KdState.pendingDrops.delete(channelId);
    console.log(`Drop announcement timed out in ${channelId}; ignoring the message.`);
    return;
  }

  if (
    pendingDrop.announcementId &&
    pendingDrop.announcementBotId === message.author.id &&
    isImageMessage(message)
  ) {
    KdState.pendingDrops.delete(channelId);
    console.log(`Drop image received in ${channelId}; starting OCR.`);
    await handleCardDrop(message);
  }
});

// ==============================
try {
  validateConfig();
  client.login(DISCORD_USER_TOKEN).catch((error) => {
    const message = String(error || "");
    console.error("Discord login failed.");
    console.error(
      "This user bot expects a Discord user account token in DISCORD_USER_TOKEN. " +
      "If the token is invalid, regenerate it and paste the new token into the environment."
    );
    if (message) {
      console.error(message);
    }
    process.exit(1);
  });
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
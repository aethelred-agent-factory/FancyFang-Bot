import { Agent } from '@voltagent/core';
import { ds_chat, ds_reason } from './deepseek';
import {
  read_trade,
  write_narrative,
  read_market_context,
  read_failure_history,
  read_candidate_features,
  fetch_cryptopanic,
  write_journal_entry,
} from './tools';

// NarratorAgent: annotates closed trades with DeepSeek
export const narratorAgent = new Agent({
  name: 'NarratorAgent',
  model: ds_chat,
  instructions: `
You annotate closed crypto futures trades.
Given a trade record and market context, you return a structured
JSON object with: narrative (plain English post-mortem), tags
(array from the taxonomy), primary_driver, and failure_mode.
Always respond with valid JSON only. No preamble.
`,
  tools: [read_trade, write_narrative, read_market_context],
});

// FailureGuardAgent: reviews candidate features for known failure modes
export const failureGuardAgent = new Agent({
  name: 'FailureGuardAgent',
  model: ds_reason, // reasoning model for deep chain-of-thought
  instructions: `
You review candidate trade setups before entry.
Given the current feature vector and historical failure mode data,
you return one of: APPROVE, SUPPRESS, or REDUCE_SIZE_50.
Include a one-sentence reason. JSON only.
`,
  tools: [read_failure_history, read_candidate_features],
});

// RegimeAgent: determines market regime from macro data
export const regimeAgent = new Agent({
  name: 'RegimeAgent',
  model: ds_chat,
  instructions: `
You evaluate the current market regime using BTC momentum, fear/greed,
and recent scanning results.  Output one of:
BEARISH_REGIME | BULLISH_REGIME | RANGING_REGIME | UNCERTAIN.
JSON only.
`,
  tools: [read_market_context, read_failure_history],
});

// SentimentAgent: parses news/reddit for sentiment
export const sentimentAgent = new Agent({
  name: 'SentimentAgent',
  model: ds_chat,
  instructions: `
You parse headlines from CryptoPanic and reddit and return a sentiment
float between -1.0 (bearish) and +1.0 (bullish).
Return JSON like { "sentiment": 0.12 }.
`,
  tools: [fetch_cryptopanic],
});

// CaretakerAgent: writes journal entries after retraining or regime shifts
export const caretakerAgent = new Agent({
  name: 'CaretakerAgent',
  model: ds_chat,
  instructions: `
You author entries for the AI Caretakers' Journal.  Given previous and
new model metrics, feature importance changes, and any regime alerts,
produce a markdown-formatted log entry suitable for appending.
Return JSON { "entry": "..." }.
`,
  tools: [write_journal_entry],
});

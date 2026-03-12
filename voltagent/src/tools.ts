import { tool } from 'ai';
import { z } from 'zod';

// Each tool wraps an HTTP call to the Python web_bridge.
// The bridge must be running on localhost:8765 for these to succeed.

export const read_trade = tool({
  description: 'Fetch a closed trade record by ID from the Python DB',
  parameters: z.object({ trade_id: z.number() }),
  async execute({ trade_id }) {
    const res = await fetch(`http://localhost:8765/trade/${trade_id}`);
    return res.json();
  },
});

export const write_narrative = tool({
  description: 'Write DeepSeek annotation back to the trade record',
  parameters: z.object({
    trade_id: z.number(),
    narrative: z.string(),
    tags: z.array(z.string()),
    primary_driver: z.string().nullable(),
    failure_mode: z.string().nullable(),
  }),
  async execute(payload) {
    const res = await fetch('http://localhost:8765/trade/annotate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  },
});

export const read_market_context = tool({
  description: 'Get latest market context snapshot',
  parameters: z.object({}),
  async execute() {
    const res = await fetch('http://localhost:8765/market_context/latest');
    return res.json();
  },
});

export const read_failure_history = tool({
  description: 'Get historical failure mode distribution',
  parameters: z.object({}),
  async execute() {
    const res = await fetch('http://localhost:8765/failure_history');
    return res.json();
  },
});

export const read_candidate_features = tool({
  description: "Fetch latest ml_features for a candidate symbol",
  parameters: z.object({ symbol: z.string() }),
  async execute({ symbol }) {
    const res = await fetch(`http://localhost:8765/candidate/${symbol}`);
    return res.json();
  },
});

export const fetch_cryptopanic = tool({
  description: 'Fetch cached CryptoPanic headlines',
  parameters: z.object({}),
  async execute() {
    const res = await fetch('http://localhost:8765/market_context/latest');
    // the Headlines are nested under cryptopanic_headlines
    const ctx = await res.json();
    return ctx.cryptopanic_headlines || [];
  },
});

export const write_journal_entry = tool({
  description: 'Append an entry to the AI_CARETAKERS_JOURNAL.md',
  parameters: z.object({ entry: z.string() }),
  async execute(payload) {
    const res = await fetch('http://localhost:8765/journal/append', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  },
});

import os
import sys
import json
import logging
from typing import Any, Dict
from pathlib import Path

# Add project root to path to allow absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.phemex_common as pc

logger = logging.getLogger("trade_narrator")

class TradeNarrator:
    """
    Uses the DeepSeek API to annotate closed trades and provide real-time
    news-based context for entry decisions.
    """

    def __init__(self, api_key: str | None = None):
        """
        Initializes the TradeNarrator.
        Args:
            api_key: The DeepSeek API key. If not provided, it will be
                     sourced from the environment via phemex_common.
        """
        self.api_key = api_key or pc.DEEPSEEK_API_KEY
        if not self.api_key:
            logger.warning(
                "DeepSeek API key not found. Trade narration will be disabled."
            )
        self.tags_taxonomy = self._load_taxonomy()

    def _load_taxonomy(self) -> Dict[str, Any]:
        """Loads the tag taxonomy from the JSON file."""
        try:
            taxonomy_path = Path(__file__).parent.parent / "tags_taxonomy.json"
            with open(taxonomy_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load tags_taxonomy.json: {e}")
            return {}

    def narrate_closed_trade(self, trade: Dict[str, Any], market_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends trade data to DeepSeek and asks for a structured JSON analysis.

        Args:
            trade: A dictionary representing the closed trade from storage_manager.
            market_ctx: The market context snapshot at the time of trade entry.

        Returns:
            A dictionary containing the structured analysis from DeepSeek.
            Returns an empty dict if the API key is not configured or if the call fails.
        """
        if not self.api_key or not self.tags_taxonomy:
            return {}

        taxonomy_str = json.dumps(self.tags_taxonomy, indent=2)

        # 1. Construct the prompt
        system_prompt = f"""
        You are an expert crypto futures trading analyst. Your task is to perform a post-mortem on a closed trade.
        Analyze the provided data and return a structured JSON object. Do not include any other text or explanation.
        The JSON should contain:
        - "narrative": A concise, 1-2 sentence post-mortem explaining the likely reason for the win or loss.
        - "tags": A JSON array of classification tags from the provided taxonomy.
        - "primary_driver": The single most influential signal or factor that determined the trade's outcome.
        - "failure_mode": If the trade was a loss, the single most likely reason for failure. Null on wins.
        - "late_entry": boolean, true if the entry was clearly delayed relative to the signal.
        - "regime_mismatch": boolean, true if the trade direction was inappropriate for the market regime.
        - "btc_override": boolean, true if a sharp move in BTC likely caused the outcome, overriding the coin's own signals.
        - "confidence": A float from 0.0 to 1.0 indicating your confidence in this analysis.

        Use ONLY the tags provided in this taxonomy:
        {taxonomy_str}
        """

        # Abbreviate for a more compact prompt
        prompt = f"""
        Analyze the following trade and return the JSON analysis.

        **Trade Data:**
        - Symbol: {trade.get('symbol')}
        - Direction: {trade.get('direction')}
        - PnL %: {(trade.get('pnl', 0) / (trade.get('entry', 1) * trade.get('size', 1))) * 100:.2f}%
        - Entry Price: {trade.get('entry')}
        - Exit Price: {trade.get('exit')}
        - Hold Time: {trade.get('hold_time_s')} seconds
        - Exit Reason: {trade.get('reason')}

        **Entry Context:**
        - Entry Score: {trade.get('score')}
        - Signals Fired: {trade.get('signals')}
        - Raw Signal Values: {trade.get('raw_signals')}
        - Market Context: {market_ctx}

        **Your task is to return only the JSON object with your analysis.**
        """

        # 2. Call the DeepSeek API
        try:
            response_text = pc.call_deepseek(prompt, system_prompt=system_prompt, stream=False)
            if response_text:
                # The response might be wrapped in markdown, so let's strip it.
                if response_text.strip().startswith("```json"):
                    response_text = response_text.strip()[7:-3]
                return json.loads(response_text)
        except Exception as e:
            logger.error(f"Error calling DeepSeek API for trade narration: {e}")

        return {}

    def get_news_sentiment(self, headlines: list[str]) -> float:
        """
        Evaluates a list of headlines and returns a sentiment score.

        Args:
            headlines: A list of news headlines.

        Returns:
            A float between -1.0 (very bearish) and 1.0 (very bullish).
        """
        if not self.api_key or not headlines:
            return 0.0

        system_prompt = """
        You are a financial sentiment analyst. Analyze the provided list of crypto news headlines.
        Return a single JSON object with one key, "sentiment_score", which is a float between -1.0 (extremely bearish) and 1.0 (extremely bullish).
        Consider the overall market impact of the news. Ignore minor or irrelevant headlines.
        """
        headlines_str = "\n".join(f"- {h}" for h in headlines)
        prompt = f"Analyze these headlines and provide the sentiment score:\n{headlines_str}"

        try:
            response_text = pc.call_deepseek(prompt, system_prompt=system_prompt, stream=False)
            if response_text:
                if response_text.strip().startswith("```json"):
                    response_text = response_text.strip()[7:-3]
                data = json.loads(response_text)
                return float(data.get("sentiment_score", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"Error parsing sentiment from DeepSeek: {e}")
        return 0.0

    def should_suppress_entry(self, headlines: list[str], setup: dict) -> tuple[bool, str]:
        """
        Asks DeepSeek if any breaking news invalidates a trade setup.

        Args:
            headlines: A list of current news headlines.
            setup: A dictionary describing the proposed trade setup.

        Returns:
            A tuple of (bool, str) indicating if the entry should be suppressed
            and the reason why.
        """
        if not self.api_key or not headlines:
            return False, ""

        system_prompt = """
        You are an expert risk manager for a crypto trading bot. Your job is to prevent trades during high-risk news events.
        Analyze the proposed trade setup against the current news headlines.
        If a headline directly contradicts the trade setup (e.g., bullish news for a SHORT setup) or introduces extreme, unpredictable macro risk (e.g., major exchange collapse, SEC regulation announcement), you must suppress the trade.
        Return a single JSON object with two keys:
        - "suppress": boolean (true or false)
        - "reason": string (a brief explanation if suppress is true, otherwise an empty string)
        Be conservative. It is better to miss a good trade than to take a bad one due to a news event.
        """
        headlines_str = "\n".join(f"- {h}" for h in headlines)
        setup_str = json.dumps(setup, indent=2)
        prompt = f"""
        **Proposed Trade:**
        {setup_str}

        **Current Headlines:**
        {headlines_str}

        Analyze and return the suppression JSON.
        """

        try:
            response_text = pc.call_deepseek(prompt, system_prompt=system_prompt, stream=False)
            if response_text:
                if response_text.strip().startswith("```json"):
                    response_text = response_text.strip()[7:-3]
                data = json.loads(response_text)
                suppress = bool(data.get("suppress", False))
                reason = str(data.get("reason", ""))
                return suppress, reason
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"Error parsing suppression decision from DeepSeek: {e}")

        return False, ""

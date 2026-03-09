import pytest
from unittest.mock import patch, MagicMock
import p_bot

def test_calculate_dynamic_blacklist_duration():
    # Win -> short cooldown
    # p_bot.BASE_COOLDOWN_WIN_S is 300
    assert p_bot._calculate_dynamic_blacklist_duration(10.0, 0) == 300
    
    # Loss -> longer cooldown
    # pnl = -10.0, entropy = 0
    # loss_penalty = 10 * 72 = 720
    # cooldown = 1800 + 720 = 2520
    assert p_bot._calculate_dynamic_blacklist_duration(-10.0, 0) == 2520

def test_get_dynamic_max_positions():
    assert p_bot.get_dynamic_max_positions(150.0) == 5
    assert p_bot.get_dynamic_max_positions(80.0) == 4
    assert p_bot.get_dynamic_max_positions(60.0) == 3
    assert p_bot.get_dynamic_max_positions(40.0) == 2
    assert p_bot.get_dynamic_max_positions(20.0) == 1

def test_effective_score():
    # Base case
    res = {"score": 100, "signals": []}
    assert p_bot._effective_score(res) == 100
    
    # HTF Alignment bonus (+15)
    res = {"score": 100, "signals": ["HTF Alignment"]}
    assert p_bot._effective_score(res) == 115
    
    # Low Liquidity penalty (-20)
    res = {"score": 100, "signals": ["Low Liquidity"]}
    assert p_bot._effective_score(res) == 80
    
    # Both
    res = {"score": 100, "signals": ["HTF Alignment", "Low Liquidity"]}
    assert p_bot._effective_score(res) == 95

def test_pick_candidates():
    long_res = [
        {"inst_id": "BTC", "score": 150},
        {"inst_id": "ETH", "score": 120},
    ]
    short_res = [
        {"inst_id": "BTC", "score": 100},
        {"inst_id": "SOL", "score": 140},
    ]
    
    # BTC gap: 150 - 100 = 50 >= 30 (ok)
    # ETH gap: 120 - 0 = 120 >= 30 (ok)
    # SOL gap: 140 - 0 = 140 >= 30 (ok)
    
    candidates = p_bot.pick_candidates(
        long_res, short_res, 
        min_score=110, 
        min_score_gap=30, 
        direction_filter="BOTH", 
        symbols_in_position=set(), 
        available_slots=2
    )
    
    assert len(candidates) == 2
    # Sorted by score (BTC 150, SOL 140)
    assert candidates[0][0]["inst_id"] == "BTC"
    assert candidates[1][0]["inst_id"] == "SOL"

def test_round_qty():
    # Mock _instrument_cache
    with (patch("p_bot._instrument_cache", {"BTCUSDT": {"step": 0.001}}), 
          patch("p_bot._instrument_loaded", True)):
        assert p_bot._round_qty("BTCUSDT", 0.00567) == "0.005"
        
    with (patch("p_bot._instrument_cache", {"ETHUSDT": {"step": 0.01}}), 
          patch("p_bot._instrument_loaded", True)):
        assert p_bot._round_qty("ETHUSDT", 0.123) == "0.12"

def test_blacklist_logic(tmp_path):
    with patch("p_bot.BLACKLIST_FILE", tmp_path / "test_blacklist.json"):
        p_bot.SYMBOL_BLACKLIST = {}
        assert p_bot.is_blacklisted("BTC") is False
        
        # Manually set entropy penalty for test
        with patch("p_bot._entropy_penalty", 0):
            p_bot.blacklist_symbol("BTC", pnl=-10.0)
            assert p_bot.is_blacklisted("BTC") is True
            
            # Check persistence
            p_bot.save_blacklist()
            p_bot.SYMBOL_BLACKLIST = {}
            p_bot.load_blacklist()
            assert p_bot.is_blacklisted("BTC") is True

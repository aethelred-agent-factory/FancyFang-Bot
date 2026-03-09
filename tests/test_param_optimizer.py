import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
import json
import math
from pathlib import Path
from unittest.mock import patch, MagicMock
import research.param_optimizer as param_optimizer
from research.param_optimizer import ParamSet, BacktestResult

@pytest.fixture
def temp_results_file(tmp_path):
    """Fixture to provide a temporary optimizer results file path."""
    test_file = tmp_path / "test_optimizer_results.json"
    with patch("research.param_optimizer._RESULTS_FILE", test_file):
        yield test_file
        if test_file.exists():
            test_file.unlink()
        if test_file.with_suffix(".tmp").exists():
            test_file.with_suffix(".tmp").unlink()

def test_param_set_as_dict():
    """Test ParamSet to dictionary conversion."""
    p = ParamSet(atr_stop_mult=2.0, score_threshold=150)
    d = p.as_dict()
    assert d["atr_stop_mult"] == 2.0
    assert d["score_threshold"] == 150
    assert d["atr_trail_mult"] == 1.0 # default

def test_backtest_result_metrics_empty():
    """Test metrics with no trades."""
    res = BacktestResult(params=ParamSet())
    res.compute_metrics()
    assert res.trade_count == 0
    assert res.total_pnl == 0.0
    assert res.win_rate == 0.0

def test_backtest_result_metrics_mixed():
    """Test metrics with a mix of winning and losing trades."""
    # 2 wins of 10, 1 loss of 5
    res = BacktestResult(params=ParamSet(), trades=[10.0, 10.0, -5.0])
    res.compute_metrics()
    
    assert res.trade_count == 3
    assert res.total_pnl == 15.0
    assert res.win_rate == 2/3
    assert res.profit_factor == 20.0 / 5.0 # 4.0
    # expectancy = 2/3 * 10 + 1/3 * -5 = 6.66 - 1.66 = 5.0
    assert pytest.approx(res.expectancy) == 5.0
    # Drawdown: Equity curve: [10, 20, 15]. Peaks: [10, 20, 20]. DD: [0, 0, 5]. Max DD = 5.
    assert res.max_drawdown == 5.0
    # Sharpe: Mean = 5, Std = np.std([10, 10, -5]) = 7.07
    # 5 / 7.07 = 0.707
    import numpy as np
    expected_std = np.std([10, 10, -5])
    assert pytest.approx(res.sharpe) == 5.0 / expected_std

def test_backtest_result_metrics_all_wins():
    """Test metrics with only winning trades."""
    res = BacktestResult(params=ParamSet(), trades=[10.0, 10.0])
    res.compute_metrics()
    assert res.win_rate == 1.0
    assert res.profit_factor == 20.0 / 1e-9
    assert res.max_drawdown == 0.0

def test_backtest_result_metrics_all_losses():
    """Test metrics with only losing trades."""
    res = BacktestResult(params=ParamSet(), trades=[-5.0, -5.0])
    res.compute_metrics()
    assert res.win_rate == 0.0
    assert res.profit_factor == 0.0
    assert res.max_drawdown == 5.0

def test_score_composite():
    """Test the composite score ranking logic."""
    # Better Sharpe, better score
    res1 = BacktestResult(params=ParamSet(), trades=[10.0, 10.0, -2.0])
    res1.compute_metrics()
    s1 = res1.score_composite()
    
    res2 = BacktestResult(params=ParamSet(), trades=[5.0, 5.0, -2.0])
    res2.compute_metrics()
    s2 = res2.score_composite()
    
    assert s1 > s2

def test_run_grid_search(temp_results_file):
    """Test the grid search process with a mock backtest function."""
    def mock_backtest(params, candles):
        # Higher score_threshold gives better PnL
        return [float(params.score_threshold) / 100.0]
    
    grid = {
        "atr_stop_mult": [1.0],
        "atr_trail_mult": [1.0],
        "score_threshold": [110, 120, 130],
        "spread_max_pct": [0.1],
        "vol_min": [0.002]
    }
    
    results = param_optimizer.run_grid_search(
        mock_backtest, [], grid=grid, top_n=2, verbose=False
    )
    
    assert len(results) == 2
    # Best should be score_threshold=130
    assert results[0].params.score_threshold == 130
    assert results[1].params.score_threshold == 120
    
    # Check that results were saved to the temporary file
    assert temp_results_file.exists()
    with open(temp_results_file, "r") as f:
        data = json.load(f)
        assert len(data) == 2
        assert data[0]["params"]["score_threshold"] == 130

def test_load_best_params(temp_results_file):
    """Test loading best params from results file."""
    # Setup some dummy data in the results file
    best_p = ParamSet(atr_stop_mult=2.5)
    res = BacktestResult(params=best_p, trades=[1.0])
    res.compute_metrics()
    param_optimizer._save_results([res])
    
    loaded = param_optimizer.load_best_params()
    assert loaded is not None
    assert loaded.atr_stop_mult == 2.5

def test_load_best_params_none(temp_results_file):
    """Test loading best params when file is missing or empty."""
    assert param_optimizer.load_best_params() is None
    
    temp_results_file.write_text("[]")
    assert param_optimizer.load_best_params() is None

def test_run_grid_search_exception(temp_results_file):
    """Test that grid search handles exceptions in the backtest function."""
    def error_backtest(params, candles):
        raise ValueError("Backtest failed")
    
    grid = {"score_threshold": [110]}
    # Should not crash, just record empty trades
    results = param_optimizer.run_grid_search(
        error_backtest, [], grid=grid, top_n=1, verbose=False
    )
    assert results[0].trade_count == 0

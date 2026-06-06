import unittest
import pandas as pd
import numpy as np
from codes.risk_metrics import score


class TestRiskMetrics(unittest.TestCase):

    def setUp(self):
        # Sample data: steady growth
        dates = pd.date_range(start='2023-01-01', periods=12, freq='ME')
        prices = [100, 102, 105, 103, 108, 110, 107, 112, 115, 113, 118, 120]
        self.normal_df = pd.DataFrame({'Date': dates, 'Close': prices})

        # Declining series (negative returns)
        declining_prices = [100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45]
        self.declining_df = pd.DataFrame({'Date': dates, 'Close': declining_prices})

        # Negative prices (e.g., some index or transformed value)
        neg_prices = [-10, -8, -12, -9, -15, -11, -13, -7, -14, -6, -5, -4]
        self.negative_df = pd.DataFrame({'Date': dates, 'Close': neg_prices})

        # SPY sample for beta/alpha
        spy_prices = [400, 405, 410, 408, 415, 420, 418, 425, 430, 428, 435, 440]
        self.spy_df = pd.DataFrame({'Date': dates, 'Close': spy_prices})

    def test_normal_case(self):
        """Test with normal positive growth series."""
        result = score(self.normal_df)
        self.assertIsNotNone(result)
        self.assertGreater(result['annual_return'], 0)
        self.assertLess(result['max_drawdown'], 0)  # should have some drawdown
        self.assertIsNotNone(result['sharpe'])
        self.assertGreater(result['risk_score'], 0)

    def test_declining_series(self):
        """Test with consistently declining prices."""
        result = score(self.declining_df)
        self.assertIsNotNone(result)
        self.assertLess(result['annual_return'], 0)
        self.assertLess(result['max_drawdown'], -0.5)  # large drawdown
        self.assertLess(result['calmar'], 0)  # negative return

    def test_negative_prices(self):
        """Test handling of negative price series (edge case)."""
        result = score(self.negative_df)
        self.assertIsNotNone(result)
        # Max drawdown should be correctly computed (negative value)
        self.assertLess(result['max_drawdown'], 0)
        # Should not crash and return valid metrics
        self.assertIsInstance(result['max_drawdown'], float)

    def test_insufficient_data(self):
        """Test early return for insufficient history."""
        short_df = self.normal_df.iloc[:4]
        result = score(short_df)
        self.assertIn('error', result)
        self.assertEqual(result['risk_score'], 50)

    def test_with_spy(self):
        """Test beta and alpha calculation when SPY provided."""
        result = score(self.normal_df, self.spy_df)
        self.assertIsNotNone(result['beta'])
        self.assertIsNotNone(result['alpha'])

    def test_max_drawdown_calculation(self):
        """Specific test for drawdown logic with known peak-to-trough."""
        # Prices: start 100 -> peak 120 -> trough 90
        test_prices = [100, 110, 120, 115, 105, 90, 95, 100]
        dates = pd.date_range('2023-01-01', periods=len(test_prices), freq='ME')
        df = pd.DataFrame({'Date': dates, 'Close': test_prices})
        
        result = score(df)
        # Expected max DD around -25% (90/120 - 1). Note: function returns rounded percent
        self.assertAlmostEqual(result['max_drawdown'], -25.0, delta=0.1)

    def test_constant_prices(self):
        """Test edge case: flat prices."""
        flat_prices = [100] * 12
        dates = pd.date_range('2023-01-01', periods=12, freq='ME')
        df = pd.DataFrame({'Date': dates, 'Close': flat_prices})
        
        result = score(df)
        self.assertAlmostEqual(result['max_drawdown'], 0.0, delta=0.001)
        self.assertAlmostEqual(result['annual_return'], 0.0, delta=0.001)


if __name__ == '__main__':
    unittest.main()
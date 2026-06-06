from codes.graham import score as graham_score
from codes.altman import score as altman_score
from codes.scorer import enhanced_composite


def test_earnings_normalization_consistency():
    """
    ISSUE: Earnings normalization inconsistency

    Verify:
      - Graham derives EPS from earnings/shares
      - Altman uses EBIT directly
      - Composite scoring accepts updated outputs
    """

    sec = {
        "earnings": [
            {"value": 100_000_000},
            {"value": 90_000_000},
            {"value": 80_000_000},
            {"value": 70_000_000},
            {"value": 60_000_000},
        ],
        "shares": [{"value": 10_000_000}],
        "bvps": [{"value": 20}],
        "cur_ast": [{"value": 500_000_000}],
        "cur_lib": [{"value": 200_000_000}],
        "lt_debt": [{"value": 100_000_000}],
        "tot_lib": [{"value": 300_000_000}],
        "equity": [{"value": 400_000_000}],
        "dividends": [],
        "total_assets": [{"value": 700_000_000}],
        "retained_earnings": [{"value": 150_000_000}],
        "ebit": [{"value": 120_000_000}],
        "revenue": [{"value": 1_000_000_000}],
        "ppe_net": [{"value": 200_000_000}],
    }

    price = 50.0

    graham = graham_score(price, sec)
    altman = altman_score(price, sec)

    # EPS should be earnings / shares
    expected_eps = 100_000_000 / 10_000_000
    assert round(graham["eps"], 2) == round(expected_eps, 2)

    # P/E should be based on derived EPS
    expected_pe = price / expected_eps
    assert round(graham["pe"], 2) == round(expected_pe, 2)

    # Altman X3 should use EBIT / Total Assets
    expected_x3 = 120_000_000 / 700_000_000
    assert round(
        altman["components"]["x3_ebit_ratio"], 4
    ) == round(expected_x3, 4)

    # Composite compatibility
    result = enhanced_composite(
        graham_result=graham,
        quality_result={"total_score": 70, "total_max": 100},
        momentum_result={"total_score": 60, "total_max": 100},
        piotroski_result={"f_score": 7},
        risk_result={"risk_score": 70, "risk_score_max": 100},
        altman_result=altman,
        buffett_result={"total_score": 75, "total_max": 100},
    )

    assert result["composite_score"] > 0
    assert "verdict" in result
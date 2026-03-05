from scripts.exit_reason_mapper import classify_exit_reason, is_reconciled_close_marker


def test_classify_exit_reason_credit_theta_stop():
    assert (
        classify_exit_reason("SPREAD: EXIT | Reason=CREDIT_THETA_STOP: p_otm below threshold")
        == "CREDIT_THETA_STOP"
    )


def test_classify_exit_reason_guarded_skip_codes():
    assert classify_exit_reason("Reason=PREMARKET_ITM_GUARDED_SKIP | DTE=22") == (
        "PREMARKET_ITM_GUARDED_SKIP"
    )
    assert classify_exit_reason("Reason=FRIDAY_FIREWALL_SKIPPED_DTE | DTE=33") == (
        "FRIDAY_FIREWALL_SKIPPED_DTE"
    )


def test_reconciled_marker_variants():
    assert is_reconciled_close_marker("FILL_CLOSE_RECONCILED")
    assert is_reconciled_close_marker("RECONCILED_CLOSE:VASS")

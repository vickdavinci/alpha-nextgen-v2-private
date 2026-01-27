## Summary

<!-- Brief description of changes (1-3 sentences) -->

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring (no functional changes)
- [ ] Documentation update
- [ ] Configuration change

## Related Spec Document

<!-- Link to the relevant spec document if applicable -->
- Spec: `docs/XX-...md`

## Checklist

### Code Quality
- [ ] Code compiles without errors
- [ ] All tests pass (`pytest tests/ -v`)
- [ ] No hardcoded values (all parameters use `config.py`)
- [ ] Type hints included for new functions
- [ ] Docstrings follow Google style

### Documentation
- [ ] Documentation updated (if behavior changed)
- [ ] `config.py` updated (if new parameters added)
- [ ] Spec document updated (if logic changed)

### Validation
- [ ] `python scripts/validate_config.py` passed
- [ ] Linting passed (`black --check .`)

### Authority Rules (for engine changes)
- [ ] Strategy engines only emit `TargetWeight` (no order calls)
- [ ] Only Portfolio Router calls `MarketOrder()` / `Liquidate()`

## Test Plan

<!-- How was this tested? -->

---

*Reminder: TQQQ and SOXL must close by 15:45 ET - they are intraday only.*

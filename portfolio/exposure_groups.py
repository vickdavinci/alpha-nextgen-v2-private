"""
Exposure Groups - Position grouping and limit enforcement.

Groups correlated positions for portfolio-level risk management.
Prevents excessive concentration in any single factor (Nasdaq beta,
S&P beta, commodities).

V6.11 Universe Groups:
- NASDAQ_BETA: TQQQ, QLD, SOXL - 50% net, 75% gross
- SPY_BETA: SSO, SPXL, SH (inverse) - 40% net, 50% gross
- COMMODITIES: UGL, UCO - 25% net, 25% gross

Spec: docs/11-portfolio-router.md (Section 11.5.2)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

import config


class ExposureGroupName(Enum):
    """Enumeration of exposure groups.

    V6.11 Universe Redesign:
    - RATES removed (TMF/SHV retired)
    - COMMODITIES added (UGL/UCO)
    """

    NASDAQ_BETA = "NASDAQ_BETA"
    SPY_BETA = "SPY_BETA"
    COMMODITIES = "COMMODITIES"


# Inverse symbols count as negative exposure
# V6.11: SH replaced PSQ as the inverse hedge
INVERSE_SYMBOLS: Set[str] = {"SH"}


@dataclass
class ExposureGroup:
    """
    Defines an exposure group with its limits.

    Attributes:
        name: Group identifier (NASDAQ_BETA, SPY_BETA, RATES).
        symbols: Set of symbols in this group.
        max_net_long: Maximum net long exposure (e.g., 0.50 = 50%).
        max_net_short: Maximum net short exposure (e.g., 0.30 = 30%).
        max_gross: Maximum gross exposure (e.g., 0.75 = 75%).
        inverse_symbols: Symbols that count as negative exposure.
    """

    name: str
    symbols: Set[str]
    max_net_long: float
    max_net_short: float
    max_gross: float
    inverse_symbols: Set[str] = field(default_factory=set)

    def contains(self, symbol: str) -> bool:
        """Check if symbol belongs to this group."""
        return symbol in self.symbols

    def is_inverse(self, symbol: str) -> bool:
        """Check if symbol is an inverse (counts as short exposure)."""
        return symbol in self.inverse_symbols


@dataclass
class GroupExposure:
    """
    Calculated exposure for an exposure group.

    Attributes:
        group_name: Name of the exposure group.
        long_exposure: Sum of positive (non-inverse) weights.
        short_exposure: Absolute sum of inverse symbol weights.
        net_exposure: long_exposure - short_exposure.
        gross_exposure: long_exposure + short_exposure.
    """

    group_name: str
    long_exposure: float
    short_exposure: float

    @property
    def net_exposure(self) -> float:
        """Net exposure (long - short)."""
        return self.long_exposure - self.short_exposure

    @property
    def gross_exposure(self) -> float:
        """Gross exposure (long + short)."""
        return self.long_exposure + self.short_exposure

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "group_name": self.group_name,
            "long_exposure": self.long_exposure,
            "short_exposure": self.short_exposure,
            "net_exposure": self.net_exposure,
            "gross_exposure": self.gross_exposure,
        }


@dataclass
class ExposureValidationResult:
    """
    Result of validating exposure against limits.

    Attributes:
        group_name: Name of the exposure group.
        is_valid: True if all limits are satisfied.
        net_long_exceeded: True if net long exceeds limit.
        net_short_exceeded: True if net short exceeds limit.
        gross_exceeded: True if gross exceeds limit.
        net_long_scale: Scale factor needed for net long (1.0 if OK).
        gross_scale: Scale factor needed for gross (1.0 if OK).
    """

    group_name: str
    is_valid: bool
    net_long_exceeded: bool = False
    net_short_exceeded: bool = False
    gross_exceeded: bool = False
    net_long_scale: float = 1.0
    gross_scale: float = 1.0

    @property
    def scale_factor(self) -> float:
        """
        Get the most restrictive scale factor.

        Returns the minimum of net_long_scale and gross_scale,
        which is the factor needed to bring all limits into compliance.
        """
        return min(self.net_long_scale, self.gross_scale)


class ExposureCalculator:
    """
    Calculates and validates portfolio exposure by group.

    Loads group definitions from config.py and provides methods to:
    - Calculate current exposure for each group
    - Validate against limits
    - Calculate scale factors when limits are exceeded
    """

    def __init__(self):
        """Initialize with groups from config."""
        self._groups: Dict[str, ExposureGroup] = self._load_groups()
        self._symbol_to_group: Dict[str, str] = config.SYMBOL_GROUPS.copy()

    def _load_groups(self) -> Dict[str, ExposureGroup]:
        """Load exposure groups from config."""
        groups = {}

        for group_name, limits in config.EXPOSURE_LIMITS.items():
            # Find all symbols in this group
            symbols = {symbol for symbol, grp in config.SYMBOL_GROUPS.items() if grp == group_name}

            # Find inverse symbols in this group
            inverse = symbols & INVERSE_SYMBOLS

            groups[group_name] = ExposureGroup(
                name=group_name,
                symbols=symbols,
                max_net_long=limits["max_net_long"],
                max_net_short=limits["max_net_short"],
                max_gross=limits["max_gross"],
                inverse_symbols=inverse,
            )

        return groups

    def get_group(self, group_name: str) -> Optional[ExposureGroup]:
        """Get an exposure group by name."""
        return self._groups.get(group_name)

    def get_group_for_symbol(self, symbol: str) -> Optional[ExposureGroup]:
        """Get the exposure group for a symbol."""
        group_name = self._symbol_to_group.get(symbol)
        if group_name:
            return self._groups.get(group_name)
        return None

    def get_all_groups(self) -> List[ExposureGroup]:
        """Get all exposure groups."""
        return list(self._groups.values())

    def calculate_exposure(
        self,
        weights: Dict[str, float],
        group_name: str,
    ) -> GroupExposure:
        """
        Calculate exposure for a specific group.

        Args:
            weights: Dict of symbol -> weight (e.g., {"QLD": 0.30, "PSQ": 0.10}).
            group_name: Name of the group to calculate.

        Returns:
            GroupExposure with long, short, net, and gross values.
        """
        group = self._groups.get(group_name)
        if not group:
            return GroupExposure(
                group_name=group_name,
                long_exposure=0.0,
                short_exposure=0.0,
            )

        long_exposure = 0.0
        short_exposure = 0.0

        for symbol, weight in weights.items():
            if symbol not in group.symbols:
                continue

            if group.is_inverse(symbol):
                # Inverse symbols contribute to short exposure
                short_exposure += abs(weight)
            else:
                # Regular symbols contribute to long exposure
                long_exposure += weight

        return GroupExposure(
            group_name=group_name,
            long_exposure=long_exposure,
            short_exposure=short_exposure,
        )

    def calculate_all_exposures(
        self,
        weights: Dict[str, float],
    ) -> Dict[str, GroupExposure]:
        """
        Calculate exposure for all groups.

        Args:
            weights: Dict of symbol -> weight.

        Returns:
            Dict of group_name -> GroupExposure.
        """
        return {
            group_name: self.calculate_exposure(weights, group_name) for group_name in self._groups
        }

    def validate_exposure(
        self,
        exposure: GroupExposure,
    ) -> ExposureValidationResult:
        """
        Validate exposure against group limits.

        Args:
            exposure: Calculated group exposure.

        Returns:
            ExposureValidationResult with limit check results.
        """
        group = self._groups.get(exposure.group_name)
        if not group:
            return ExposureValidationResult(
                group_name=exposure.group_name,
                is_valid=True,
            )

        net_long_exceeded = exposure.net_exposure > group.max_net_long
        net_short_exceeded = exposure.net_exposure < -group.max_net_short
        gross_exceeded = exposure.gross_exposure > group.max_gross

        # Calculate scale factors
        net_long_scale = 1.0
        if net_long_exceeded and exposure.long_exposure > 0:
            # Scale to bring net within limit
            net_long_scale = group.max_net_long / exposure.net_exposure

        gross_scale = 1.0
        if gross_exceeded and exposure.gross_exposure > 0:
            gross_scale = group.max_gross / exposure.gross_exposure

        is_valid = not (net_long_exceeded or net_short_exceeded or gross_exceeded)

        return ExposureValidationResult(
            group_name=exposure.group_name,
            is_valid=is_valid,
            net_long_exceeded=net_long_exceeded,
            net_short_exceeded=net_short_exceeded,
            gross_exceeded=gross_exceeded,
            net_long_scale=net_long_scale,
            gross_scale=gross_scale,
        )

    def validate_all(
        self,
        weights: Dict[str, float],
    ) -> Dict[str, ExposureValidationResult]:
        """
        Validate all group exposures.

        Args:
            weights: Dict of symbol -> weight.

        Returns:
            Dict of group_name -> ExposureValidationResult.
        """
        exposures = self.calculate_all_exposures(weights)
        return {
            group_name: self.validate_exposure(exposure)
            for group_name, exposure in exposures.items()
        }

    def scale_weights_for_group(
        self,
        weights: Dict[str, float],
        group_name: str,
        scale_factor: float,
    ) -> Dict[str, float]:
        """
        Scale weights for a specific group.

        Only scales long positions (not inverse/short positions).

        Args:
            weights: Original weights.
            group_name: Group to scale.
            scale_factor: Factor to apply (e.g., 0.667 to reduce by 1/3).

        Returns:
            New weights dict with scaled values.
        """
        group = self._groups.get(group_name)
        if not group:
            return weights.copy()

        result = weights.copy()
        for symbol in group.symbols:
            if symbol in result and not group.is_inverse(symbol):
                result[symbol] = result[symbol] * scale_factor

        return result

    def enforce_limits(
        self,
        weights: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Enforce all exposure limits by scaling positions.

        Iterates through all groups and scales down positions
        that exceed limits.

        Args:
            weights: Original weights.

        Returns:
            Adjusted weights within all limits.
        """
        result = weights.copy()

        for group_name in self._groups:
            exposure = self.calculate_exposure(result, group_name)
            validation = self.validate_exposure(exposure)

            if not validation.is_valid:
                scale = validation.scale_factor
                result = self.scale_weights_for_group(result, group_name, scale)

        return result

    def is_symbol_inverse(self, symbol: str) -> bool:
        """Check if a symbol is an inverse ETF."""
        return symbol in INVERSE_SYMBOLS

    def get_group_symbols(self, group_name: str) -> Set[str]:
        """Get all symbols in a group."""
        group = self._groups.get(group_name)
        return group.symbols if group else set()

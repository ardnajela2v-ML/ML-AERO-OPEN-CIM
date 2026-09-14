"""Explicit scenario assumptions for economic sensitivity analysis."""
import math


def wake_loss_range(base_loss_pct):
    """Uniform ±30% relative scenario, bounded by 100% energy loss.

    This is a sensitivity assumption, not a calibrated confidence interval.
    None omits wake uncertainty; zero retains a constant zero-loss scenario.
    """
    if base_loss_pct is None:
        return None
    try:
        base = float(base_loss_pct)
    except (TypeError, ValueError):
        raise ValueError('La pérdida por estela debe ser un número entre 0 y menos de 100%.') from None
    if not math.isfinite(base) or not 0 <= base < 100:
        raise ValueError('La pérdida por estela debe ser finita, entre 0 y menos de 100%.')
    return [base * 0.7, min(100.0, base * 1.3)]

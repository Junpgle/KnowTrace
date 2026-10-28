"""Reference implementation of the Bayesian Knowledge Tracing update."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BKTParameters:
    p_initial: float = 0.2
    p_learn: float = 0.1
    p_guess: float = 0.2
    p_slip: float = 0.1

    def __post_init__(self) -> None:
        for value in (self.p_initial, self.p_learn, self.p_guess, self.p_slip):
            if not 0 <= value <= 1:
                raise ValueError("BKT probabilities must be in [0, 1]")


def update_mastery(prior: float, correct: bool,
                   params: BKTParameters = BKTParameters()) -> tuple[float, float]:
    """Return (posterior given response, probability after learning transition).

    If an observation has zero likelihood under current parameters, return
    the prior as posterior instead of dividing by zero.
    """
    if not 0 <= prior <= 1:
        raise ValueError("prior must be a probability")
    likelihood_known = (1 - params.p_slip) if correct else params.p_slip
    likelihood_unknown = params.p_guess if correct else (1 - params.p_guess)
    evidence = prior * likelihood_known + (1 - prior) * likelihood_unknown
    posterior = (prior * likelihood_known / evidence) if evidence > 0 else prior
    after = posterior + (1 - posterior) * params.p_learn
    return posterior, min(1.0, max(0.0, after))

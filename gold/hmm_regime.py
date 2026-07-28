"""HMM regime classifier — a probabilistic Gaussian Hidden Markov Model (Phase 6).

Pure Python (no numpy): a 2- or 3-state Gaussian HMM fit on log-returns by Baum-Welch,
with a scaled forward-backward so it stays numerically stable on long series. It upgrades
the rule-based macro read to a PROBABILISTIC one: which regime is active now, the
posterior probability (= confidence), and each state's return/vol profile.

States are labelled after fitting by their mean return: highest mean → "bull", lowest →
"bear", middle → "neutral" (3-state) — so the output feeds the VAULTUM macro_cycle score
with a real confidence. Deterministic given the data + seed.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

_SQRT2PI = math.sqrt(2.0 * math.pi)
_VAR_FLOOR = 1e-8


def _close(bar) -> float:
    if isinstance(bar, dict):
        return float(bar["close"])
    return float(bar[4]) if len(bar) >= 5 else float(bar[-1])


def log_returns(bars: Sequence) -> List[float]:
    closes = [_close(b) for b in bars]
    out = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _gauss(x: float, mu: float, var: float) -> float:
    var = max(var, _VAR_FLOOR)
    return math.exp(-((x - mu) ** 2) / (2 * var)) / (_SQRT2PI * math.sqrt(var))


def _init_params(x: List[float], k: int):
    """Seed states by quantiles of the data (deterministic, no RNG)."""
    xs = sorted(x)
    n = len(xs)
    mus = [xs[min(n - 1, int((i + 0.5) / k * n))] for i in range(k)]
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / n
    vars_ = [max(var, _VAR_FLOOR)] * k
    pi = [1.0 / k] * k
    A = [[(0.8 if i == j else 0.2 / (k - 1)) for j in range(k)] for i in range(k)]
    return pi, A, mus, vars_


def fit_hmm(x: List[float], k: int = 3, iters: int = 25):
    """Baum-Welch with scaled forward-backward. Returns (pi, A, mus, vars, gamma)."""
    n = len(x)
    pi, A, mus, vars_ = _init_params(x, k)
    gamma = [[0.0] * k for _ in range(n)]
    for _ in range(iters):
        # --- E-step: emission matrix ---
        B = [[_gauss(x[t], mus[i], vars_[i]) for i in range(k)] for t in range(n)]
        # scaled forward
        alpha = [[0.0] * k for _ in range(n)]
        c = [0.0] * n
        for i in range(k):
            alpha[0][i] = pi[i] * B[0][i]
        c[0] = sum(alpha[0]) or 1e-300
        alpha[0] = [a / c[0] for a in alpha[0]]
        for t in range(1, n):
            for j in range(k):
                alpha[t][j] = B[t][j] * sum(alpha[t - 1][i] * A[i][j] for i in range(k))
            c[t] = sum(alpha[t]) or 1e-300
            alpha[t] = [a / c[t] for a in alpha[t]]
        # scaled backward
        beta = [[0.0] * k for _ in range(n)]
        beta[n - 1] = [1.0 / c[n - 1]] * k
        for t in range(n - 2, -1, -1):
            for i in range(k):
                beta[t][i] = sum(A[i][j] * B[t + 1][j] * beta[t + 1][j] for j in range(k)) / c[t]
        # gamma (state posteriors) + accumulate state sums
        xi_sum = [[0.0] * k for _ in range(k)]
        gsum = [0.0] * k
        for t in range(n):
            denom = sum(alpha[t][i] * beta[t][i] for i in range(k)) or 1e-300
            for i in range(k):
                gamma[t][i] = alpha[t][i] * beta[t][i] / denom
                gsum[i] += gamma[t][i]
        for t in range(n - 1):
            denom = 0.0
            terms = [[alpha[t][i] * A[i][j] * B[t + 1][j] * beta[t + 1][j]
                      for j in range(k)] for i in range(k)]
            denom = sum(sum(row) for row in terms) or 1e-300
            for i in range(k):
                for j in range(k):
                    xi_sum[i][j] += terms[i][j] / denom
        # --- M-step ---
        pi = [max(gamma[0][i], 1e-6) for i in range(k)]
        s = sum(pi); pi = [p / s for p in pi]
        for i in range(k):
            row = xi_sum[i]
            rs = sum(row) or 1e-300
            A[i] = [v / rs for v in row]
        for i in range(k):
            gi = gsum[i] or 1e-300
            mus[i] = sum(gamma[t][i] * x[t] for t in range(n)) / gi
            vars_[i] = max(sum(gamma[t][i] * (x[t] - mus[i]) ** 2 for t in range(n)) / gi, _VAR_FLOOR)
    return pi, A, mus, vars_, gamma


def regime_hmm(bars: Sequence, k: int = 3) -> dict:
    """Fit the HMM on the bars' log-returns and report the CURRENT regime + posterior."""
    x = log_returns(bars)
    if len(x) < 20:
        return {"available": False, "reason": f"need >=20 returns, got {len(x)}",
                "regime": "neutral", "confidence": 0.1}
    k = 2 if len(x) < 40 else k
    pi, A, mus, vars_, gamma = fit_hmm(x, k=k)
    order = sorted(range(k), key=lambda i: mus[i])       # low mean -> high mean
    if k == 3:
        label = {order[0]: "bear", order[1]: "neutral", order[2]: "bull"}
    else:
        label = {order[0]: "bear", order[1]: "bull"}
    last = gamma[-1]
    cur = max(range(k), key=lambda i: last[i])
    persistence = round(A[cur][cur], 3)     # self-transition prob of the current micro-state

    # Direction from the POSTERIOR-WEIGHTED expected return (robust on unimodal trends),
    # with a volatility deadband so noise around zero reads neutral.
    exp_ret = sum(last[i] * mus[i] for i in range(k))
    avg_vol = math.sqrt(sum(vars_[i] for i in range(k)) / k)
    deadband = 0.15 * avg_vol
    if exp_ret > deadband:
        regime, gold_bias = "bull", "long"
        confidence = round(sum(last[i] for i in range(k) if mus[i] >= 0), 3)
    elif exp_ret < -deadband:
        regime, gold_bias = "bear", "short"
        confidence = round(sum(last[i] for i in range(k) if mus[i] <= 0), 3)
    else:
        regime, gold_bias = "neutral", "neutral"
        confidence = round(last[cur], 3)

    states = []
    for i in range(k):
        states.append({"state": label[i], "mean_return": round(mus[i], 6),
                       "vol": round(math.sqrt(vars_[i]), 6),
                       "posterior_now": round(last[i], 3)})
    return {
        "available": True, "states": k, "regime": regime, "gold_bias": gold_bias,
        "confidence": confidence, "persistence": persistence,
        "state_profiles": sorted(states, key=lambda s: s["mean_return"]),
        "n_returns": len(x),
        "explanation": (f"HMM({k}-state): current regime {regime.upper()} "
                        f"(p={confidence:.0%}, persistence {persistence:.0%}) — "
                        f"{'bullish' if gold_bias=='long' else 'bearish' if gold_bias=='short' else 'range'} gold"),
    }

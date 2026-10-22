"""Numpy-only models: a screen-positive risk model with explanations and bootstrap intervals,
and a two-model (T-learner) estimate of how much an invitation raises attendance."""
import numpy as np

from .data import sigmoid

FEATURES = ["age", "BMI", "prediabetes", "family history", "genomic finding", "visit history"]
CONTEXT_FALLBACK_KM = 8.0  # shortcut: population-average distance when area context isn't authorised


def fit_logistic(X, y, l2=1.0, iters=12):
    """L2-regularised logistic regression by Newton steps; intercept is not penalised."""
    X1 = np.c_[np.ones(len(X)), X]
    reg = np.r_[0.0, np.full(X.shape[1], l2)]
    w = np.zeros(X1.shape[1])
    for _ in range(iters):
        p = sigmoid(X1 @ w)
        grad = X1.T @ (p - y) + reg * w
        hess = (X1 * (p * (1 - p))[:, None]).T @ X1 + np.diag(reg)
        w -= np.linalg.solve(hess, grad)
    return w


def features(p):
    """Only consented categories enter; withheld ones read as 'absent'."""
    c = p["consent"]
    return [(p["age"] - 45) / 10, (p["bmi"] - 27) / 5, float(p["prediabetes"]),
            float(c["family_history"] and p["fam_crc_age"] is not None),
            float(c["genomic"] and p["variant"] is not None), p["past_attendance"]]


def calibration(p, y, bins=10):
    return [{"predicted": round(float(p[i].mean()), 4), "observed": round(float(y[i].mean()), 4), "n": int(len(i))}
            for i in np.array_split(np.argsort(p), bins) if len(i)]


class RiskModel:
    """Probability of a positive screen within 24 months.

    screened_only=True is the bias correction: a person who was never screened can never test
    positive, so training on everyone teaches the model that missed visits mean low risk.
    """

    def __init__(self, adults, rng, screened_only=True, n_boot=20):
        X = np.array([features(p) for p in adults])
        y = np.array([p["_truth"]["positive_hist"] for p in adults], float)
        screened = np.array([p["_truth"]["screened_hist"] for p in adults])
        train = rng.random(len(adults)) < 0.8
        fit = train & screened if screened_only else train
        self.mean = X[fit].mean(0)
        self.w = fit_logistic(X[fit], y[fit])
        idx = np.flatnonzero(fit)
        self.boot = (np.array([fit_logistic(X[s], y[s]) for s in (rng.choice(idx, len(idx)) for _ in range(n_boot))])
                     if n_boot else None)
        test = ~train & screened
        self.calibration = calibration(self.predict(X[test])[0], y[test])

    def predict(self, X):
        """Returns (point, 10th percentile, 90th percentile) arrays."""
        X1 = np.c_[np.ones(len(X)), np.asarray(X)]
        p = sigmoid(X1 @ self.w)
        if self.boot is None:
            return p, p, p
        lo, hi = np.percentile(sigmoid(X1 @ self.boot.T), [10, 90], axis=1)
        return p, lo, hi

    def explain(self, x, top=3):
        """Exact additive contributions (log-odds vs the average person), largest risk-raising first."""
        contrib = self.w[1:] * (np.asarray(x) - self.mean)
        return [{"factor": FEATURES[i], "log_odds": round(float(contrib[i]), 3)}
                for i in np.argsort(-contrib)[:top] if contrib[i] > 0]


def lift_features(p):
    km = p["distance_km"] if p["consent"]["context"] else CONTEXT_FALLBACK_KM
    return [p["past_attendance"], km / 10]


class LiftModel:
    """Invitation lift = P(attend | invited) - P(attend | not invited), learned from randomised past outreach."""

    def __init__(self, people):
        X = np.array([lift_features(p) for p in people])
        t = np.array([p["_truth"]["invited_hist"] for p in people])
        y = np.array([p["_truth"]["attended_hist"] for p in people], float)
        self.w1, self.w0 = fit_logistic(X[t], y[t]), fit_logistic(X[~t], y[~t])

    def predict(self, people):
        X1 = np.c_[np.ones(len(people)), np.array([lift_features(p) for p in people])]
        return np.clip(sigmoid(X1 @ self.w1) - sigmoid(X1 @ self.w0), 0, 1)

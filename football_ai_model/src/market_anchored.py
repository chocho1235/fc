"""
LightGBM model that starts from the bookmaker's no-vig probabilities and only
learns corrections on top of them.

The market is very hard to beat from scratch, so instead of predicting each
match from nothing we feed the market's log-probabilities in as the starting
score (LightGBM's init_score). The trees then only have to learn where the
market is wrong. With strong regularisation and few trees the model stays close
to the market unless the features give a consistent reason to move away.

The market probabilities are read from the feature matrix itself, so
predict_proba(X) works as a drop-in replacement for the old sklearn models.
"""

import os

import lightgbm as lgb
import numpy as np

# Used when a match has no odds at all. Bets are never placed without odds,
# so this only affects the displayed probabilities.
FALLBACK_1X2 = np.array([0.45, 0.27, 0.28])
FALLBACK_BINARY = 0.5


def _params(n_estimators):
    return dict(
        n_estimators=int(os.getenv("LGBM_N_ESTIMATORS", str(n_estimators))),
        learning_rate=float(os.getenv("LGBM_LEARNING_RATE", "0.02")),
        num_leaves=int(os.getenv("LGBM_NUM_LEAVES", "7")),
        min_child_samples=int(os.getenv("LGBM_MIN_CHILD_SAMPLES", "200")),
        subsample=float(os.getenv("LGBM_SUBSAMPLE", "0.8")),
        subsample_freq=1,
        colsample_bytree=float(os.getenv("LGBM_COLSAMPLE_BYTREE", "0.5")),
        reg_lambda=float(os.getenv("LGBM_REG_LAMBDA", "10.0")),
        verbose=-1,
    )


class MarketAnchoredClassifier:
    """1X2 classifier (labels H/D/A) anchored on market probabilities.

    market_columns: indices of market_home_prob, market_draw_prob,
    market_away_prob in the feature matrix.
    """

    def __init__(self, market_columns, n_estimators=150):
        self.market_columns = list(market_columns)
        self.n_estimators = n_estimators
        self.classes_ = np.array(["H", "D", "A"])
        self.model = None

    def _anchor(self, x):
        market = np.asarray(x, dtype=float)[:, self.market_columns]
        totals = market.sum(axis=1)
        market = np.where((totals > 0.5)[:, None], market / np.where(totals > 0, totals, 1)[:, None], FALLBACK_1X2)
        return np.log(np.clip(market, 1e-4, 1.0))

    def fit(self, x, y):
        index = {label: i for i, label in enumerate(self.classes_)}
        y_index = np.array([index[label] for label in y])
        self.model = lgb.LGBMClassifier(objective="multiclass", num_class=3, **_params(self.n_estimators))
        self.model.fit(np.asarray(x, dtype=float), y_index, init_score=self._anchor(x))
        return self

    def predict_proba(self, x):
        scores = self.model.predict(np.asarray(x, dtype=float), raw_score=True) + self._anchor(x)
        scores = np.exp(scores - scores.max(axis=1, keepdims=True))
        return scores / scores.sum(axis=1, keepdims=True)


class MarketAnchoredBinaryClassifier:
    """Binary classifier (labels 0/1) anchored on a market probability column."""

    def __init__(self, market_column, n_estimators=150):
        self.market_column = market_column
        self.n_estimators = n_estimators
        self.classes_ = np.array([0, 1])
        self.model = None

    def _anchor(self, x):
        market = np.asarray(x, dtype=float)[:, self.market_column]
        market = np.where(market > 0, market, FALLBACK_BINARY)
        market = np.clip(market, 1e-4, 1 - 1e-4)
        return np.log(market / (1 - market))

    def fit(self, x, y):
        self.model = lgb.LGBMClassifier(objective="binary", **_params(self.n_estimators))
        self.model.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=int), init_score=self._anchor(x))
        return self

    def predict_proba(self, x):
        scores = self.model.predict(np.asarray(x, dtype=float), raw_score=True) + self._anchor(x)
        positive = 1 / (1 + np.exp(-scores))
        return np.column_stack([1 - positive, positive])

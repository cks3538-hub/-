"""점 분류기: 정규화 좌표계의 기하 특징 → 표준부품코드 확률. (scikit-learn ExtraTrees)"""
from __future__ import annotations

import pickle
from typing import Dict, List, Optional

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier


def train_classifier(library, cfg, exclude: List[str] = (), log=print) -> Dict:
    rng = np.random.default_rng(cfg.seed)
    X, y = [], []
    for a in library.atlases:
        if a.atlas_id in exclude:
            continue
        n = len(a.labels)
        take = min(n, cfg.clf_per_atlas)
        idx = rng.choice(n, size=take, replace=False) if take < n else np.arange(n)
        X.append(a.features[idx])
        y.append(a.labels[idx])
    if not X:
        raise ValueError("학습할 아틀라스가 없습니다.")
    X = np.vstack(X)
    y = np.concatenate(y)
    log(f"  분류기 학습: 표본 {len(y):,}개, 특징 {X.shape[1]}개, 클래스 {len(np.unique(y))}개")
    model = ExtraTreesClassifier(n_estimators=cfg.clf_trees, min_samples_leaf=cfg.clf_min_leaf, max_features="sqrt",
                                 n_jobs=-1, class_weight="balanced_subsample", random_state=cfg.seed)
    model.fit(X, y)
    return {"model": model, "feature_names": library.feature_names, "codes": list(library.codes)}


def predict_proba_full(bundle: Optional[Dict], X: np.ndarray, n_codes: int) -> Optional[np.ndarray]:
    if bundle is None:
        return None
    model = bundle["model"]
    p = model.predict_proba(X)
    out = np.zeros((len(X), n_codes))
    out[:, model.classes_.astype(int)] = p
    return out


def save_classifier(bundle: Dict, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump(bundle, f)


def load_classifier(path: str) -> Dict:
    with open(path, "rb") as f:
        return pickle.load(f)

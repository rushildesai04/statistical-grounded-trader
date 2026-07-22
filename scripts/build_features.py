"""Ticker extraction -> sentiment -> mentions -> point-in-time features + labels."""
from sgt.db.session import init_db
from sgt.features.store import build_features_and_labels
from sgt.processing.mentions import build_mentions

if __name__ == "__main__":
    init_db()
    m = build_mentions()
    print(f"Mentions built: {m}")
    res = build_features_and_labels()
    print(f"Features: {res['features']}, Labels: {res['labels']}, tau={res['tau']:.4f}")

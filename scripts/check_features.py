
import joblib
import sys
from pathlib import Path

try:
    feature_names = joblib.load('models/feature_names.pkl')
    print("Feature Names:")
    for name in feature_names:
        print(name)
except Exception as e:
    print(f"Error loading feature_names: {e}")

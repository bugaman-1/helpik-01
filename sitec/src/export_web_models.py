"""Export the supplied trusted sklearn pipelines to dependency-free inference data.

Run with sitec/models/requirements-model.txt installed. Never accepts uploaded
pickle files. The web application only reads the resulting JSON.
"""
import hashlib
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'site'))
sys.path.insert(0, str(ROOT / 'sitec' / 'src'))
from reliability_features import add_reliability_features
from quiz.services.sitec_inference import probability


def export(bundle):
    pipeline = bundle['model']
    assert list(pipeline.named_steps) == ['features', 'classifier']
    pre = pipeline.named_steps['features']
    classifier = pipeline.named_steps['classifier']
    assert classifier.classes_.tolist() == [0, 1]
    assert [name for name, _, _ in pre.transformers_] == ['answers', 'numeric']
    categorical, numeric = pre.transformers_
    encoder = categorical[1].named_steps['onehot']
    scaler = numeric[1].named_steps['scale']
    assert encoder.drop is None
    assert scaler.with_mean and scaler.with_std
    coefficients = classifier.coef_[0].tolist()
    offset, answers = 0, []
    for code, categories in zip(categorical[2], encoder.categories_):
        size = len(categories)
        assert categories.tolist() == [0, 1, 2, 3]
        answers.append({'code': code, 'coefficients': coefficients[offset:offset + size]})
        offset += size
    numeric_terms = [dict(code=code, mean=float(mean), scale=float(scale), coefficient=weight)
                     for code, mean, scale, weight in zip(numeric[2], scaler.mean_, scaler.scale_, coefficients[offset:])]
    assert offset + len(numeric_terms) == len(coefficients)
    return {'threshold': float(bundle['threshold']), 'intercept': float(classifier.intercept_[0]),
            'answers': answers, 'numeric': numeric_terms, 'feature_columns': bundle['feature_columns']}


def main():
    models = {}
    sources = {}
    originals = {}
    for name in ('baseline', 'reliability'):
        path = ROOT / 'sitec' / 'models' / f'synthetic_{name}.joblib'
        originals[name] = joblib.load(path)
        models[name] = export(originals[name])
        sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    schema = json.loads((ROOT / 'sitec/data/processed/question_schema.json').read_text(encoding='utf-8'))
    # Verify against the original sklearn pipelines on an independent supplied
    # synthetic validation set, plus exact speed boundaries and extreme values.
    data = pd.read_csv(ROOT / 'sitec/data/raw/synthetic_validation_seed20261026.csv')
    edges = []
    for answer in range(4):
        for speed in (0, 1.499999, 1.5, 20, 20.000001, 1000):
            row = {term['code']: answer for term in models['baseline']['answers']}
            row.update(avg_sec_per_answer=speed, duration_sec=speed * 26,
                       back_navigation_count=100, consistent_answers_count=24)
            edges.append(row)
    data = add_reliability_features(pd.concat([data, pd.DataFrame(edges)], ignore_index=True))
    validation = {}
    fixtures = []
    records = data.to_dict('records')
    for name, model in models.items():
        expected = originals[name]['model'].predict_proba(data[model['feature_columns']])[:, 1]
        actual = np.array([probability(model, row) for row in records])
        error = float(np.max(np.abs(expected - actual)))
        assert error < 1e-12, (name, error)
        assert np.array_equal(expected >= model['threshold'], actual >= model['threshold'])
        validation[name] = {'rows': len(data), 'max_absolute_error': error, 'identical_classes': True}
        for i in (0, 1, len(data) - 1):
            fixtures.append({'model': name, 'input': {k: records[i][k] for k in model['feature_columns']},
                             'probability': float(expected[i])})
    version = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()[:16]
    result = {'schema': 'sitec-logistic-v1', 'version': version, 'sources': sources,
              'questionnaire': schema, 'models': models, 'validation': validation, 'fixtures': fixtures}
    target = ROOT / 'site/quiz/ml/sitec-v1.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'version': version, 'validation': validation}, indent=2))


if __name__ == '__main__':
    main()

"""Exact OneHotEncoder + StandardScaler + binary LogisticRegression inference.

Only complete 0..3 records are supported; missing observations are not imputed.
Parameters are exported and checked against both original sitec pipelines.
"""
import math


def probability(model, record):
    terms = [model['intercept']]
    for term in model['answers']:
        value = record[term['code']]
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError('Expected a complete 0..3 answer')
        terms.append(term['coefficients'][value])
    for term in model['numeric']:
        value = float(record[term['code']])
        if not math.isfinite(value):
            raise ValueError('Expected a finite observed feature')
        terms.append((value - term['mean']) / term['scale'] * term['coefficient'])
    score = math.fsum(terms)
    if score >= 0:
        return 1 / (1 + math.exp(-score))
    exp_score = math.exp(score)
    return exp_score / (1 + exp_score)

"""AHP priorities from explicit judgments; never inferred from legacy weights."""
from itertools import combinations
import numpy as np

# R. W. Saaty (1987), Mathematical Modelling 9, 161–176.
# https://doi.org/10.1016/0270-0255(87)90473-8
RANDOM_INDEX = {2: 0.0, 3: .58, 4: .90, 5: 1.12, 6: 1.24,
                7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}
RI_SOURCE = 'https://doi.org/10.1016/0270-0255(87)90473-8'
CR_THRESHOLD = .10


def calculate_ahp(criteria, matrix):
    if (not isinstance(criteria, list) or not 2 <= len(criteria) <= 10
            or any(not isinstance(key, str) or not key.strip() for key in criteria)
            or len(set(criteria)) != len(criteria)):
        raise ValueError('Se requieren de 2 a 10 criterios únicos.')
    n = len(criteria)
    try:
        a = np.asarray(matrix, dtype=float)
    except (ValueError, TypeError):
        raise ValueError('La matriz debe contener todas las comparaciones numéricas.') from None
    if a.shape != (n, n) or not np.isfinite(a).all():
        raise ValueError('La matriz debe ser cuadrada, completa y finita.')
    if np.any(a < 1 / 9 - 1e-12) or np.any(a > 9 + 1e-12):
        raise ValueError('Las comparaciones deben estar entre 1/9 y 9.')
    if not np.allclose(np.diag(a), 1, rtol=0, atol=1e-9):
        raise ValueError('La diagonal debe ser 1.')
    if not np.allclose(a * a.T, 1, rtol=0, atol=1e-8):
        raise ValueError('Las comparaciones deben ser recíprocas: a_ji = 1/a_ij.')
    eigenvalues, eigenvectors = np.linalg.eig(a)
    index = int(np.argmax(eigenvalues.real))
    lam = float(eigenvalues[index].real)
    w = np.abs(eigenvectors[:, index].real)
    w /= w.sum()
    ci = max(0.0, (lam - n) / (n - 1))
    ri = RANDOM_INDEX[n]
    cr = ci / ri if ri else 0.0
    triads = []
    for i, j, k in combinations(range(n), 3):
        deviation = abs(float(np.log(a[i, j]) + np.log(a[j, k]) - np.log(a[i, k])))
        triads.append({'criteria': [criteria[i], criteria[j], criteria[k]],
                       'log_deviation': deviation})
    return {'method': 'ahp_principal_eigenvector', 'criteria': list(criteria),
            'matrix': a.tolist(), 'weights': dict(zip(criteria, w.tolist())),
            'lambda_max': lam, 'ci': ci, 'ri': ri, 'cr': cr,
            'ri_source': RI_SOURCE, 'cr_threshold': CR_THRESHOLD,
            'consistent': bool(cr <= CR_THRESHOLD),
            'review_triads': sorted(triads, key=lambda row: -row['log_deviation'])[:3]}

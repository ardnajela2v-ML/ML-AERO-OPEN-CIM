"""Reproducible energy basis and layout refinement after park TOPSIS."""
from copy import deepcopy

import numpy as np

from core.farm import simulate_wind_farm


def build_energy_context(sites, turbine, *, wake_model, k_wake, ct,
                         availability, electrical_losses, curtailment_loss=0.0,
                         environmental_loss=0.0):
    # Keep the inputs to the deterministic joint distribution, not a second
    # independently fitted Weibull or an extrapolation of hub-height wind.
    return {
        'version': 2,
        'direction_convention': 'meteorological_from_north_clockwise_centered_sectors',
        'wind_method': 'topsis_joint_weibull_8760',
        'wind_height_m': float(turbine.hub_height_m),
        'sites': [{key: deepcopy(site[key]) for key in
                   ('v_mean', 'weibull_K', 'rho_mean', 'sectorial') if key in site}
                  for site in sites],
        'wake_model': wake_model, 'k_wake': k_wake, 'ct': ct,
        'n_sectors': 36, 'availability': availability,
        'electrical_losses': electrical_losses,
        'curtailment_loss': curtailment_loss,
        'environmental_loss': environmental_loss,
        'local_wind_policy': 'uniform_park_resource_as_in_topsis',
    }


def energy_evaluator(context, turbine):
    from core.farm_mcda import _joint_synthetic_wind

    if context.get('version') != 2 or not context.get('sites'):
        raise ValueError('Ejecuta TOPSIS nuevamente para guardar su base energética.')
    if not np.isclose(float(context['wind_height_m']), turbine.hub_height_m):
        raise ValueError('La altura cambió; ejecuta TOPSIS nuevamente para comparar layouts.')
    ws, wd, mean_speed, _, rho = _joint_synthetic_wind(context['sites'])
    survival = ((1.0 - float(context.get('curtailment_loss', 0.0))) *
                (1.0 - float(context.get('environmental_loss', 0.0))))

    def evaluate(points):
        sim = simulate_wind_farm(
            points, turbine, ws, wd, rho_override=rho,
            **{key: context[key] for key in ('wake_model', 'k_wake', 'ct',
               'n_sectors', 'availability', 'electrical_losses')})
        if sim.get('error'):
            return sim
        sim['aep_after_wake_operational_mwh'] = sim['aep_mwh']
        # Operational losses already applied by simulate_wind_farm; apply
        # curtailment/environment once and retain gross/no-wake diagnostics.
        for row in [sim, *sim.get('turbines', [])]:
            for key in ('aep_mwh', 'aep_no_wake_mwh', 'aep_net_pre_wake_mwh',
                        'record_energy_mwh', 'record_energy_no_wake_mwh', 'survival_factor'):
                if key in row:
                    row[key] *= survival
            for key in ('cf_pct', 'flh'):
                if key in row:
                    row[key] *= survival
        sim['aep_p50_mwh'] = sim['aep_mwh']
        sim['aep_per_turbine_mwh'] = sim['aep_mwh'] / len(points)
        sim['mean_power_per_turbine_kw'] = sim['aep_per_turbine_mwh'] * 1000 / 8760
        sim['wind_source'] = context['wind_method']
        return sim

    return evaluate, ws, wd, mean_speed, rho


def _local_wake_refinement(points, evaluate, valid_geometry, *, wake_limit,
                            max_radius_m, passes=2, n_angles=8, n_radii=3,
                            max_evals=250):
    """Bounded coordinate-descent around a fixed layout.

    Nudges the most wake-affected turbines a short distance (<= max_radius_m,
    roughly 0.5-1 rotor diameter) from their current position to cut estela
    and, once compliant, lift AEP — without the row/column spacing rule that
    only ever stood in as a proxy for limiting wake. Each accepted move keeps
    every other turbine fixed, so the seed pattern (aligned/tresbolillo/
    TOPSIS) stays recognisable; it is refined, not replaced by a free search.
    Accept-if-better against a single objective that prioritises meeting
    wake_limit and only then rewards extra AEP, so it usefully serves both
    "rescue an infeasible family" and "improve an already-feasible layout".
    """
    points = [list(map(float, p)) for p in points]
    current_sim = evaluate(points)
    if current_sim.get('error'):
        return points, current_sim

    def score(sim):
        return float(sim['aep_mwh']) - 1e6 * max(0.0, float(sim['wake_loss_pct']) - wake_limit)

    best_score = score(current_sim)
    radii = [max_radius_m * f for f in np.linspace(1.0 / n_radii, 1.0, n_radii)]
    angles = np.linspace(0.0, 2 * np.pi, n_angles, endpoint=False)
    n = len(points)
    evals = 0
    for _ in range(passes):
        if evals >= max_evals:
            break
        moved = False
        order_idx = sorted(range(n), key=lambda i: -current_sim['turbines'][i]['wake_loss_pct'])
        budget = max(1, int(np.ceil(n * 0.6)))
        for i in order_idx[:budget]:
            if evals >= max_evals:
                break
            origin = points[i]
            local_best = None
            for r in radii:
                if evals >= max_evals:
                    break
                for a in angles:
                    if evals >= max_evals:
                        break
                    trial_point = [origin[0] + r * float(np.cos(a)), origin[1] + r * float(np.sin(a))]
                    trial_points = points[:i] + [trial_point] + points[i + 1:]
                    if not valid_geometry(trial_points):
                        continue
                    sim = evaluate(trial_points)
                    evals += 1
                    if sim.get('error'):
                        continue
                    s = score(sim)
                    if s > best_score + 1e-6 and (local_best is None or s > local_best[2]):
                        local_best = (trial_point, sim, s)
            if local_best is not None:
                points[i] = local_best[0]
                current_sim = local_best[1]
                best_score = local_best[2]
                moved = True
        if not moved:
            break
    return points, current_sim


def refine_topsis_layout(alternative, turbine, *, layout_type='auto', n_turbines=0):
    """Compare shortlisted configurations at a fixed count on TOPSIS's basis.

    The raw TOPSIS layout is always shown as-is ('Referencia TOPSIS'), plus a
    'TOPSIS mejorado (auto)' variant that applies bounded local refinement to
    raise AEP / cut estela from that same layout. Alineado and Tresbolillo are
    generated from a fast-score structured grid search (16 distinct finalists
    per family); if none of those finalists meets the wake limit, the
    least-wake finalist is rescued with the same bounded local refinement
    instead of discarding the whole family. None of this is a global-optimum
    claim, only a bounded search around recognisable seed patterns.
    """
    from core.farm import _latlon_to_xy, _xy_to_latlon, optimize_layout_structured
    from shapely.geometry import Point, Polygon

    constraints = alternative.get('layout_constraints') or {}
    context = alternative.get('energy_context') or {}
    if not constraints or not context:
        raise ValueError('Ejecuta TOPSIS del parque nuevamente para comparar configuraciones.')
    specification = alternative.get('turbine_specification') or {}
    for key in ('rated_power_kw', 'rotor_diameter_m', 'hub_height_m'):
        if not np.isclose(float(specification.get(key, -1)), float(getattr(turbine, key))):
            raise ValueError('La turbina cambió respecto a TOPSIS; vuelve a ejecutar el ranking.')
    families = {'aligned': ('Alineado', 0.0), 'staggered': ('Tresbolillo', .5)}
    if layout_type != 'auto' and layout_type not in families:
        raise ValueError('Tipo de layout desconocido.')
    selected_families = families if layout_type == 'auto' else {layout_type: families[layout_type]}
    original_n = int(alternative['n_aerogeneradores'])
    target_n = int(n_turbines) if int(n_turbines) > 0 else original_n
    if target_n > original_n:
        raise ValueError('Para aumentar la cantidad aprobada por TOPSIS, ejecuta el ranking nuevamente.')
    polygon = constraints['polygon']
    lat0 = float(np.mean([p['lat'] for p in polygon]))
    lon0 = float(np.mean([p['lon'] for p in polygon]))
    def xy_shape(vertices):
        x, y = _latlon_to_xy([p['lat'] for p in vertices], [p['lon'] for p in vertices], lat0, lon0)
        return Polygon(zip(x, y))
    land = xy_shape(polygon)
    setback = max(float(constraints['boundary_setback_m']), turbine.rotor_diameter_m / 2)
    allowed = land.buffer(-setback)
    for exclusion in constraints.get('exclusions', []):
        allowed = allowed.difference(xy_shape(exclusion).buffer(setback))
    minimum = float(constraints['min_spacing_d']) * turbine.rotor_diameter_m
    wake_limit = float(constraints['max_wake_loss_pct'])
    evaluate, ws, wd, mean, rho = energy_evaluator(context, turbine)

    def valid_geometry(points):
        points = np.asarray(points, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            return False
        if not all(allowed.buffer(1e-6).covers(Point(*p)) for p in points):
            return False
        distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        return bool(np.all(distances >= minimum - 1e-6))

    baseline_latlon = alternative['layout_latlon']
    x, y = _latlon_to_xy([p[0] for p in baseline_latlon], [p[1] for p in baseline_latlon], lat0, lon0)
    baseline_xy = np.column_stack([x, y]).tolist()
    if len(baseline_xy) != original_n or not valid_geometry(baseline_xy):
        raise ValueError('El layout TOPSIS no cumple la geometría guardada; vuelve a ejecutar TOPSIS.')
    baseline_sim = evaluate(baseline_xy)
    if baseline_sim.get('error'):
        raise ValueError('No se pudo reproducir la simulación TOPSIS.')
    baseline_aep = float(baseline_sim['aep_mwh'])
    reported_aep = float(alternative.get('aep_p50_mwh', alternative.get('aep_mwh', 0)))
    if not np.isclose(baseline_aep, reported_aep, rtol=1e-6, atol=.05):
        raise ValueError('La energía TOPSIS guardada no es reproducible; ejecuta el ranking nuevamente.')
    base_stagger = float(alternative.get('layout_config', {}).get('stagger', 0))
    baseline_eligible = (target_n == original_n and (layout_type == 'auto' or
                          np.isclose(base_stagger, selected_families[layout_type][1])))
    candidates = []
    errors = []
    cache = {}

    def add_candidate(candidate, family, label, simulation=None, eligible=True):
        points = candidate['layout_xy_m']
        if not valid_geometry(points):
            return
        # Coordinate order must remain aligned with per-turbine simulation rows.
        signature = tuple(tuple(round(v, 4) for v in p) for p in points)
        if simulation is None:
            if signature not in cache:
                try:
                    cache[signature] = evaluate(points)
                except ValueError as exc:
                    cache[signature] = {'error': str(exc)}
            simulation = cache[signature]
        if simulation.get('error'):
            return
        aep = float(simulation['aep_mwh'])
        loss = float(simulation['wake_loss_pct'])
        candidates.append({
            **candidate, 'id': f'layout-{len(candidates)}', 'family': family, 'label': label,
            'simulation': simulation, 'n_placed': len(points),
            'aep_p50_mwh': aep, 'aep_per_turbine_mwh': aep / len(points),
            'wake_loss_pct': loss, 'aep_gain_mwh': aep - baseline_aep,
            'wake_reduction_pp': float(baseline_sim['wake_loss_pct']) - loss,
            'feasible': bool(eligible and np.isfinite(aep) and np.isfinite(loss) and loss <= wake_limit + 1e-9),
        })

    add_candidate(dict(layout_xy_m=baseline_xy, layout_latlon=baseline_latlon,
                       wind_at_turbines=[mean] * original_n,
                       layout_config=alternative.get('layout_config', {})),
                  'topsis', 'Referencia TOPSIS', baseline_sim, baseline_eligible)

    # 'TOPSIS mejorado (auto)': same layout, bounded local refinement to raise
    # AEP / cut estela — only meaningful at the exact TOPSIS count (a local
    # nudge can't add or remove turbines), and only in the general comparison
    # (a family-filtered request is scoped to that one family vs. baseline).
    if target_n == original_n and layout_type == 'auto':
        improved_xy, improved_sim = _local_wake_refinement(
            baseline_xy, evaluate, valid_geometry,
            wake_limit=wake_limit, max_radius_m=turbine.rotor_diameter_m)
        improved_lats, improved_lons = _xy_to_latlon(
            [p[0] for p in improved_xy], [p[1] for p in improved_xy], lat0, lon0)
        add_candidate(dict(
            layout_xy_m=improved_xy,
            layout_latlon=[[la, lo] for la, lo in zip(improved_lats, improved_lons)],
            wind_at_turbines=[mean] * original_n,
            layout_config={**alternative.get('layout_config', {}), 'layout_type': 'topsis_improved'},
        ), 'topsis_improved', 'TOPSIS mejorado (auto)', improved_sim, True)

    for family, (label, stagger) in selected_families.items():
        try:
            result = optimize_layout_structured(
                polygon, turbine, target_n, ws=ws, wd=wd, fallback_wind_ms=mean,
                rho=rho, min_spacing_d=constraints['min_spacing_d'],
                s_para_range=tuple(constraints['s_para_range']),
                s_perp_range=tuple(constraints['s_perp_range']),
                stagger_options=(stagger,), exclusion_polygons=constraints.get('exclusions', []),
                boundary_setback_m=setback, wake_model=context['wake_model'],
                k_wake=context['k_wake'], ct=context['ct'], candidate_limit=16)
        except ValueError as exc:
            errors.append({'family': label, 'reason': str(exc)})
            continue
        for candidate in result['candidate_layouts']:
            candidate['layout_config'].update(layout_type=family, dominant_dir_deg=result['layout_config']['dominant_dir_deg'])
            add_candidate(candidate, family, label)
        group = [c for c in candidates if c['family'] == family]
        if group and not any(c['feasible'] for c in group):
            # None of the 16 fast-score grid finalists meets the wake limit.
            # Rescue the least-wake one with a bounded local nudge instead of
            # discarding the whole family: the row/column spacing that grid
            # enforced was only ever a proxy for limiting estela.
            seed = min(group, key=lambda c: (c['wake_loss_pct'], -c['aep_p50_mwh']))
            refined_xy, refined_sim = _local_wake_refinement(
                seed['layout_xy_m'], evaluate, valid_geometry,
                wake_limit=wake_limit, max_radius_m=turbine.rotor_diameter_m)
            r_lats, r_lons = _xy_to_latlon(
                [p[0] for p in refined_xy], [p[1] for p in refined_xy], lat0, lon0)
            add_candidate(dict(
                layout_xy_m=refined_xy,
                layout_latlon=[[la, lo] for la, lo in zip(r_lats, r_lons)],
                wind_at_turbines=seed['wind_at_turbines'],
                layout_config={**seed['layout_config'], 'refined_locally': True},
            ), family, f'{label} (ajustado localmente)', refined_sim, True)

    feasible = [c for c in candidates if c['feasible']]
    order = lambda c: (-c['aep_p50_mwh'], c['wake_loss_pct'])
    by_wake = lambda c: (c['wake_loss_pct'], -c['aep_p50_mwh'])
    # candidates always has at least the TOPSIS baseline; show the closest
    # miss rather than blocking the whole comparison when nothing complies.
    best = min(feasible, key=order) if feasible else min(candidates, key=by_wake)
    least_wake = min(candidates, key=by_wake)
    # Non-dominated energy/wake alternatives, using the same count/assumptions.
    for c in candidates:
        c['pareto'] = c['feasible'] and not any(
            other['aep_p50_mwh'] >= c['aep_p50_mwh'] and other['wake_loss_pct'] <= c['wake_loss_pct']
            and (other['aep_p50_mwh'] > c['aep_p50_mwh'] or other['wake_loss_pct'] < c['wake_loss_pct'])
            for other in feasible)
    winners = [candidates[0]]
    for c in candidates:
        if c['family'] == 'topsis_improved':
            winners.append(c)
    for family in selected_families:
        group = [c for c in candidates if c['family'] == family]
        if not group:
            if not any(e['family'] == selected_families[family][0] for e in errors):
                errors.append({'family': selected_families[family][0],
                               'reason': 'No fue posible generar ninguna configuración con esta cantidad de turbinas.'})
            continue
        feasible_group = [c for c in group if c['feasible']]
        winners.append(min(feasible_group, key=order) if feasible_group else min(group, key=by_wake))
    if least_wake not in winners:
        winners.append(least_wake)
    return {
        **best, 'turbine': turbine.to_dict(), 'turbine_key': alternative['turbina'],
        'installed_mw': target_n * turbine.rated_power_kw / 1000,
        'D_m': turbine.rotor_diameter_m, 'min_sep_m': minimum,
        'origin_lat': lat0, 'origin_lon': lon0, 'area_km2': land.area / 1e6,
        'n_requested': target_n, 'n_candidates': len(candidates),
        'energy_context': context, 'layout_constraints': constraints,
        'comparison': {
            'baseline_aep_mwh': baseline_aep, 'baseline_n': original_n,
            'same_count': target_n == original_n, 'max_wake_loss_pct': wake_limit,
            'selected_id': best['id'], 'max_energy_id': best['id'],
            'min_wake_id': least_wake['id'], 'winners': winners, 'errors': errors,
            'evaluated_count': len(candidates), 'any_feasible': bool(feasible),
            'search_method': ('16 finalistas distintos por familia (rejilla), preseleccionados por energía '
                               'aproximada; el que menos estela tenga por familia se ajusta localmente '
                               '(<=1D) si ninguno cumple el límite; evaluación con la base TOPSIS'),
        },
    }

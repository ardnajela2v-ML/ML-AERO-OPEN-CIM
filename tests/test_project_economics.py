"""Independent cash-flow identities and parity across economic entry points."""
import inspect
import numpy as np
import pytest

from core.cost_model import CostModel
from core.energy import PowerCurve, TechnoEconomicAnalyzer
from core.farm_mcda import _economics, generate_park_layouts
from core.project_economics import project_cashflows


@pytest.mark.parametrize('rate', [0, 0.08])
@pytest.mark.parametrize('years', [5, 25, 40])
def test_panel_and_topsis_match_for_same_inputs(rate, years):
    turbine = PowerCurve.from_builtin('V150_4.5')
    # foundation_usd_per_m3eq=0: farm_mcda._economics() suma un costo de
    # cimentación dependiente de geometría (altura de buje, diámetro de
    # rotor) que el panel de una sola turbina (TechnoEconomicAnalyzer) no
    # modela todavía — alcance limitado al ranking de layouts de parque.
    # Se desactiva aquí para poder seguir comparando ambas rutas en lo demás.
    model = CostModel(**{**CostModel.from_defaults().to_dict(), 'foundation_usd_per_m3eq': 0.0})
    panel = TechnoEconomicAnalyzer(model).analyze(
        turbine, 10, 100_000_000, energy_price_usd_kwh=0.09,
        discount_rate_pct=rate * 100, project_years=years)
    farm = _economics(turbine, 10, 100_000, model, rate, years, 0.09, 0)
    for panel_key, farm_key, precision in [
        ('npv_usd', 'van_usd', 0), ('lcoe_usd_kwh', 'lcoe_usd_kwh', 4),
        ('b_c_ratio', 'benefit_cost_ratio', 4), ('van_capex_ratio', 'roi', 4),
        ('payback_years', 'payback_years', 1),
    ]:
        expected = farm[farm_key]
        assert panel[panel_key] == (round(expected, precision) if expected is not None else None)
    assert panel['irr_pct'] == (round(farm['irr'] * 100, 2) if farm['irr'] is not None else None)
    for actual, expected in zip(panel['cashflow_table'], farm['cashflow_table']):
        assert actual['energy_mwh'] == pytest.approx(expected['energy_mwh'])
        assert actual['cashflow'] == pytest.approx(expected['net_cashflow_usd'])
    assert panel['decommissioning_usd'] == farm['decommissioning_usd']


def test_independent_discounted_formula_and_terminal_timing():
    capex, opex, energy, price, rate, years = 1_000_000, 30000, 2_000_000, 0.1, .08, 25
    actual = project_cashflows(capex, opex, energy, price, rate, years)
    expected_energy = energy * .997 ** np.arange(years)
    discounts = 1.08 ** np.arange(1, years + 1)
    expected_costs = capex + np.sum(opex / discounts) + capex * .03 / discounts[-1]
    expected_npv = np.sum(expected_energy * price / discounts) - expected_costs
    assert actual['van_usd'] == pytest.approx(expected_npv)
    assert actual['pv_energy_kwh'] == pytest.approx(np.sum(expected_energy / discounts))
    assert actual['pv_costs'] == pytest.approx(expected_costs)
    rows = actual['cashflow_table']
    assert rows[0]['energy_mwh'] == energy / 1000
    assert all(row['decommissioning_usd'] == 0 for row in rows[:-1])
    assert rows[-1]['decommissioning_usd'] == capex * .03


def test_price_series_inflation_and_hourly_revenue_override_survive():
    result = project_cashflows(1000, 20, 10_000, .1, 0, 5,
                               annual_inflation_pct=2, price_series=[.2, .3],
                               year1_revenue_override=1234)
    rows = result['cashflow_table']
    assert rows[0]['revenue_usd'] == 1234
    assert rows[1]['revenue_usd'] == pytest.approx(10_000 * .997 * .3)
    assert rows[2]['revenue_usd'] == pytest.approx(10_000 * .997 ** 2 * .1)
    assert rows[-1]['opex_usd'] == pytest.approx(20 * 1.02 ** 4)
    assert result['van_usd'] == pytest.approx(sum(result['cashflows']))


def test_zero_lifecycle_parameters_recover_constant_energy_annuity():
    r = project_cashflows(1000, 20, 10_000, .1, .08, 25,
                          degradation_pct=0, decommissioning_pct=0)
    annuity = (1 - 1.08 ** -25) / .08
    assert r['van_usd'] == pytest.approx(-1000 + (1000 - 20) * annuity)
    assert r['pv_costs'] / r['pv_energy_kwh'] == pytest.approx((1000 + 20 * annuity) / (10000 * annuity))


def test_collection_cable_remains_in_capex_opex_and_terminal_cost():
    turbine = PowerCurve.from_builtin('V150_4.5')
    model = CostModel.from_defaults()
    result = _economics(turbine, 2, 30000, model, .08, 25, .1, 100)
    foundation = model.foundation_cost_usd(turbine.rotor_diameter_m, turbine.hub_height_m, 2)
    expected_capex = (model.capex_breakdown(turbine.rated_power_kw, 2)['total_capex_usd']
                       + 7500 + foundation)
    assert result['capex_usd'] == expected_capex
    assert result['foundation_cost_usd'] == foundation
    assert result['opex_usd_anual'] == expected_capex * model.opex_pct_capex
    assert result['decommissioning_usd'] == expected_capex * .03


def test_foundation_cost_formula_matches_diameter_squared_times_hub_height():
    model = CostModel.from_defaults()
    expected = model.foundation_usd_per_m3eq * 120.0 ** 2 * 90.0 * 5
    assert model.foundation_cost_usd(120.0, 90.0, 5) == pytest.approx(expected)


def test_foundation_cost_is_zero_without_geometry():
    model = CostModel.from_defaults()
    assert model.foundation_cost_usd(None, 90.0, 5) == 0.0
    assert model.foundation_cost_usd(120.0, None, 5) == 0.0
    assert model.foundation_cost_usd(120.0, 90.0, 0) == 0.0


def test_capex_per_installed_kw_varies_with_turbine_geometry_not_constant():
    """Regresión: antes de la corrección de 2026-09-13, capex_usd_per_installed_kw
    era una constante matemática (turbine_usd_per_kw / turbines_pct) sin importar
    la turbina o el número de unidades — mismo problema que el área efectiva.
    El costo de cimentación (∝ diámetro²·altura de buje) rompe esa constancia."""
    model = CostModel.from_defaults()
    small = PowerCurve.from_builtin('V150_4.5')     # hub 105 m, Ø150 m
    large = PowerCurve.from_builtin('E126_7.58')    # hub 135 m, Ø126 m
    econ_small = _economics(small, 20, 100_000, model, .08, 20, .06, 0)
    econ_large = _economics(large, 20, 100_000, model, .08, 20, .06, 0)
    assert econ_small['capex_usd_per_installed_kw'] != pytest.approx(
        econ_large['capex_usd_per_installed_kw'])


@pytest.mark.parametrize('rate', [0, .08])
def test_kriging_matches_panel_with_same_energy_and_costs(rate):
    from core.kriging import turbine_site_economics
    model = CostModel.from_defaults()
    turbine = PowerCurve.from_builtin('V150_4.5')
    wind = np.full(8760, 8.0)
    speeds = np.array([0, 3, 8, 12, 25], dtype=float)
    powers = np.array([0, 0, 2000, 4500, 0], dtype=float)
    site = turbine_site_economics(
        wind, np.full(8760, 1.225), speeds, powers, 150, 4500,
        demand_mwh=100_000, tasa=rate, price_usd_kwh=.09, anios=25,
        cost_model=model, fitted_power_w=powers * 1000)
    panel = TechnoEconomicAnalyzer(model).analyze(
        turbine, site['n_aerogeneradores'], site['aep_mwh'] * 1000,
        energy_price_usd_kwh=.09, discount_rate_pct=rate * 100, project_years=25)
    assert panel['npv_usd'] == round(site['van_usd'])
    assert panel['lcoe_usd_kwh'] == round(site['lcoe_usd_kwh'], 4)
    assert panel['vae_usd'] == round(site['vae_usd'])


def test_leveraged_endpoint_uses_actual_project_flows():
    from api import create_app
    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    client = app.test_client()
    flows = [400, 390, 380, 370, 100]
    body = dict(capex_usd=1000, annual_revenue_usd=500, annual_opex_usd=100,
                project_years=5, debt_ratio_pct=0, equity_discount_rate_pct=8,
                project_cashflows_usd=flows)
    response = client.post('/api/energy/leveraged', json=body)
    assert response.status_code == 200
    result = response.get_json()
    assert [row['equity_cf'] for row in result['cashflow_table']] == flows
    assert result['leveraged_npv_usd'] == round(-1000 + sum(cf / 1.08 ** yr for yr, cf in enumerate(flows, 1)))
    body['project_cashflows_usd'] = [400]
    assert client.post('/api/energy/leveraged', json=body).status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# §7 Paso 4 — inflación, price_escalation, dos corridas (nominal/real),
# economics_warnings en _economics() y firma de generate_park_layouts()
# ─────────────────────────────────────────────────────────────────────────────

def _t1_turbine_model():
    return PowerCurve.from_builtin('V150_4.5'), CostModel.from_defaults()


def _t4_model_nofound():
    """CostModel sin costo de cimentación (foundation=0); usado en T4/T6/T10."""
    return CostModel(**{**CostModel.from_defaults().to_dict(), 'foundation_usd_per_m3eq': 0.0})


def _econ_t4(**kw):
    """Parámetros compartidos T4/T6/T10: n=1, aep=3500, foundation=0, r=0.095,
    years=3, price=0.08, coll=0, deg=0, decomm=3.0."""
    turbine = PowerCurve.from_builtin('V150_4.5')
    model_nofound = _t4_model_nofound()
    return _economics(turbine, 1, 3500, model_nofound, 0.095, 3, 0.08, 0.0,
                      degradation_pct=0.0, decommissioning_pct=3.0, **kw)


def test_economics_T1a_regression_no_inflation():
    """Regresión: valores existentes no cambian + claves nuevas presentes.
    Sin nuevos parámetros → INF001+BASIS001+INF002 por defecto."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0)
    assert result['van_usd'] == pytest.approx(-57_321_534.6658887, rel=1e-9)
    assert result['lcoe_usd_kwh'] == pytest.approx(0.7750339644597397, rel=1e-9)
    assert result['benefit_cost_ratio'] == pytest.approx(0.12902660345951156, rel=1e-9)
    assert 'economics_warnings' in result
    assert any(w.startswith('INF001') for w in result['economics_warnings'])
    assert any(w.startswith('INF002') for w in result['economics_warnings'])
    assert 'lcoe_nom_usd_kwh' in result
    assert 'lcoe_real_usd_kwh' in result


def test_economics_T1b_nominal_with_inflation_golden():
    """Golden: con inf=3.65 y escal=3.65 explícitos y basis='nominal'; warnings=[]."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0,
                        annual_inflation_pct=3.65, price_escalation_pct=3.65,
                        discount_rate_basis='nominal')
    assert result['van_usd'] == pytest.approx(-60_394_640.54437992, rel=1e-9)
    assert result['lcoe_nom_usd_kwh'] == pytest.approx(0.8458056197820268, rel=1e-9)
    assert result['economics_warnings'] == []


def test_economics_T2_real_and_nominal_lcoe_differ_when_inflating():
    """Con inflación>0, LCOE nominal ≠ LCOE real (no son equivalentes en valor)."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0,
                        annual_inflation_pct=3.65, price_escalation_pct=3.65)
    assert result['lcoe_nom_usd_kwh'] != pytest.approx(result['lcoe_real_usd_kwh'], rel=1e-3)


def test_economics_T3_opex_grows_in_nominal_cashflow():
    """OPEX en la tabla de flujos nominales crece año a año con inflación>0."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0,
                        annual_inflation_pct=3.65, price_escalation_pct=3.65)
    table = result['cashflow_table']
    assert table[1]['opex_usd'] > table[0]['opex_usd']


def test_economics_T4_fisher_delta_van_T4_params():
    """|VAN_nom − VAN_real| con parámetros T4/T6. Valor exacto se pega al revisor."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65)
    assert 'van_real_usd' in result
    delta = abs(result['van_usd'] - result['van_real_usd'])
    assert delta < 1e-6


def test_economics_T5_warnings_empty_valid_nominal_params():
    """Sin warnings cuando inf>0, tasa real positiva y basis explícito."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65,
                      discount_rate_basis='nominal')
    assert result['economics_warnings'] == []


def test_economics_T6_warning_when_real_rate_nonpositive():
    """BASIS002 cuando inflación supera la tasa nominal (tasa real ≤ 0)."""
    turbine = PowerCurve.from_builtin('V150_4.5')
    model = _t4_model_nofound()
    result = _economics(turbine, 1, 3500, model, 0.05, 3, 0.08, 0.0,
                        degradation_pct=0.0, decommissioning_pct=3.0,
                        annual_inflation_pct=6.0, price_escalation_pct=6.0,
                        discount_rate_basis='nominal')
    assert any(w.startswith('BASIS002') for w in result['economics_warnings'])


def test_economics_T7_new_keys_present():
    """Claves nuevas obligatorias presentes en el dict de retorno."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65)
    for key in ('lcoe_nom_usd_kwh', 'lcoe_real_usd_kwh', 'van_real_usd',
                'economics_warnings', 'opex_present_value_ratio'):
        assert key in result, f'falta la clave {key!r}'


def test_economics_T8_price_escalates_in_nominal_cashflow():
    """El precio en la tabla nominal crece con price_escalation_pct>0."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65)
    table = result['cashflow_table']
    assert table[1]['price_usd_kwh'] > table[0]['price_usd_kwh']


def test_economics_T9a_nominal_lcoe_above_real_lcoe_when_inflating():
    """Proyecto de capital intensivo: LCOE nominal > LCOE real cuando inf>0."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0,
                        annual_inflation_pct=3.65, price_escalation_pct=3.65)
    assert result['lcoe_nom_usd_kwh'] > result['lcoe_real_usd_kwh']


def test_economics_T9b_opex_present_value_ratio():
    """opex_present_value_ratio = PV_real(opex) / PV_nom(opex). Valor se pega al revisor."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65)
    ratio = result['opex_present_value_ratio']
    assert 0.5 < ratio < 2.0


def test_economics_T10_fisher_golden_lcoe_pair():
    """Golden confirmado: lcoe_nom, lcoe_real y |ΔVAN|<1e-6 con parámetros T4/T6."""
    result = _econ_t4(annual_inflation_pct=3.65, price_escalation_pct=3.65)
    assert result['lcoe_nom_usd_kwh'] == pytest.approx(0.774437828525241, rel=1e-9)
    assert result['lcoe_real_usd_kwh'] == pytest.approx(0.722109693246953, rel=1e-9)
    assert abs(result['van_usd'] - result['van_real_usd']) < 1e-6


def test_economics_T6a_INF001():
    """INF001 cuando annual_inflation_pct no se proporciona (None)."""
    result = _econ_t4(annual_inflation_pct=None)
    assert any(w.startswith('INF001') for w in result['economics_warnings'])


def test_economics_T6b_INF002():
    """INF002 cuando inf=0.0 y basis='nominal' explícitos."""
    result = _econ_t4(annual_inflation_pct=0.0, discount_rate_basis='nominal')
    assert any(w.startswith('INF002') for w in result['economics_warnings'])


def test_economics_T6c_BASIS001():
    """BASIS001 cuando discount_rate_basis no se proporciona (None)."""
    result = _econ_t4(discount_rate_basis=None)
    assert any(w.startswith('BASIS001') for w in result['economics_warnings'])


def test_economics_T11_revenue_year1_override_and_flat():
    """revenue_year1_override se reporta en annual_revenue_year1_usd;
    annual_revenue_year1_flat_usd es siempre aep×price."""
    result = _econ_t4(revenue_year1_override=999_999.0,
                      annual_inflation_pct=3.65, price_escalation_pct=3.65,
                      discount_rate_basis='nominal')
    assert result['annual_revenue_year1_usd'] == pytest.approx(999_999.0)
    assert result['annual_revenue_year1_flat_usd'] == pytest.approx(280_000.0)
    # 0.08 * 3500 * 1000 = 280_000


def test_economics_T11b_layouts_warnings_surfaced():
    """generate_park_layouts propaga economics_warnings de alternativas al resultado."""
    import os
    polygon = [
        {'lat': 16.5, 'lon': -95.0}, {'lat': 16.5, 'lon': -94.9973},
        {'lat': 16.5027, 'lon': -94.9973}, {'lat': 16.5027, 'lon': -95.0},
    ]
    sites = [{'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
              'sectorial': {'frequency_pct': [6.25] * 16}}]
    result = generate_park_layouts(
        sites=sites, polygon=polygon, turbines_folder='aerogeneradores',
        turbine_filenames=['acciona15_p1500_r35.csv'],
        n_range=(1, 1), project_years=20, price_usd_kwh=0.083,
        annual_inflation_pct=None)
    assert 'economics_warnings' in result
    assert any(w.startswith('INF001') for w in result['economics_warnings'])


def test_economics_T12_backward_compatible_no_new_params():
    """La firma original (sin nuevos parámetros) sigue produciendo el VAN correcto.
    Sin nuevos params → warnings de defecto (INF001/BASIS001/INF002)."""
    turbine, model = _t1_turbine_model()
    result = _economics(turbine, 7, 9205, model, 0.095, 25, 0.1, 22788.0)
    assert result['van_usd'] == pytest.approx(-57_321_534.6658887, rel=1e-9)
    assert 'economics_warnings' in result
    assert result['lcoe_nom_usd_kwh'] == pytest.approx(result['lcoe_usd_kwh'], rel=1e-12)

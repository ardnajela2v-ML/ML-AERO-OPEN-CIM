"""Independent cash-flow identities and parity across economic entry points."""
import numpy as np
import pytest

from core.cost_model import CostModel
from core.energy import PowerCurve, TechnoEconomicAnalyzer
from core.farm_mcda import _economics
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

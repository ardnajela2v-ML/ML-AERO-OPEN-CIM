import unittest

from core.farm_mcda import analyze_park_layouts


class EnvironmentalWorkflowTests(unittest.TestCase):
    """Flujo de decisión de ruido/shadow flicker acordado con el usuario:
    el campeón primario es "estela + VAN" (ignora ruido/flicker a
    propósito); el efecto ambiental de ESE campeón se reporta aparte, sin
    bloquear la selección, y si no es viable se ofrece (del mismo pool) la
    mejor alternativa que sí cumple -- sin descartar la evidencia en
    silencio en ningún caso.

    Usa un polígono pequeño (~300m, una sola turbina factible) para que el
    barrido anual de shadow flicker sea rápido y la geometría sea
    determinista (no depende de qué posición exacta elija el buscador).
    """

    TINY_POLYGON = [
        {'lat': 16.5000, 'lon': -95.0000}, {'lat': 16.5000, 'lon': -94.9973},
        {'lat': 16.5027, 'lon': -94.9973}, {'lat': 16.5027, 'lon': -95.0000},
    ]
    SITES = [{
        'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
        'sectorial': {'frequency_pct': [6.25] * 16},
    }]

    def _run(self, **overrides):
        kwargs = dict(
            sites=self.SITES, polygon=self.TINY_POLYGON, turbines_folder='aerogeneradores',
            turbine_filenames=['gamesa21_p2000_r45.csv'], min_spacing_d=3,
            max_wake_loss_pct=99.0, project_years=20, price_usd_kwh=0.083,
            n_range=(1, 1),
        )
        kwargs.update(overrides)
        return analyze_park_layouts(**kwargs)

    def test_environmental_feasibility_is_ok_without_receptors(self):
        result = self._run()
        self.assertEqual(len(result['alternatives']), 1, result['errors'])
        row = result['alternatives'][0]
        self.assertTrue(row['environmental_feasibility']['ok'])
        self.assertIsNone(row['mitigation_alternative'])

    def test_champion_is_kept_despite_noise_violation_and_it_is_reported(self):
        # Receptor muy cerca del polígono con límite nocturno estricto: la
        # única turbina factible (n_range=(1,1)) casi con certeza lo excede.
        receptor = {'lat': 16.5013, 'lon': -95.0015, 'type': 'vivienda', 'period': 'night'}
        result = self._run(
            receptor_points=[receptor], noise_limit_dba={'day': 55.0, 'night': 20.0})
        self.assertEqual(len(result['alternatives']), 1, result['errors'])
        row = result['alternatives'][0]
        # El campeón se sigue eligiendo (no se descarta el layout por
        # ruido): sigue siendo la única turbina factible por estela.
        self.assertEqual(row['n_aerogeneradores'], 1)
        self.assertFalse(row['environmental_feasibility']['ok'])
        self.assertFalse(row['environmental_feasibility']['noise_ok'])
        self.assertIsNotNone(row['environmental_feasibility']['noise_evaluation'])
        # n_range=(1,1) no deja otro N que mitigue -- no hay alternativa.
        self.assertIsNone(row['mitigation_alternative'])

    def test_champion_selection_rule_is_wake_only(self):
        result = self._run()
        row = result['alternatives'][0]
        self.assertEqual(row['champion_selection']['rule'], 'max_n_then_van')
        self.assertIn('environmental_feasibility', row)
        self.assertIn('shadow_flicker_evaluation', row['environmental_feasibility'])

    def test_n_range_by_turbine_overrides_only_that_technology(self):
        # El botón "usar esta alternativa" del panel ambiental fuerza N solo
        # para la tecnología del campeón que se quiere mitigar -- las demás
        # tecnologías de la misma corrida deben seguir explorando su propio
        # rango normalmente (n_range global, aquí None = ventana automática).
        result = self._run(
            n_range=None,
            n_range_by_turbine={'gamesa21_p2000_r45.csv': (1, 1)},
            turbine_filenames=['gamesa21_p2000_r45.csv', 'acciona15_p1500_r35.csv'],
        )
        self.assertEqual(len(result['alternatives']), 2, result['errors'])
        by_turbine = {row['turbina']: row for row in result['alternatives']}
        gamesa = by_turbine['gamesa21_p2000_r45.csv']
        acciona = by_turbine['acciona15_p1500_r35.csv']
        self.assertEqual(gamesa['champion_selection']['n_range_explored'], [1, 1])
        self.assertEqual(gamesa['n_aerogeneradores'], 1)
        # La otra tecnología no quedó forzada a [1,1] por el override ajeno.
        self.assertNotEqual(acciona['champion_selection']['n_range_explored'], [1, 1])


if __name__ == '__main__':
    unittest.main()

import unittest
import pandas as pd
from core.resource_period import period_options, select_period


class ResourcePeriodTests(unittest.TestCase):
    def test_local_calendar_boundaries_gaps_and_reversible_selection(self):
        frame = pd.DataFrame({'wind_speed': [3., None, 7., 9.]}, index=pd.to_datetime([
            '2019-01-01T05:50Z', '2019-01-01T06:00Z',
            '2019-01-01T06:10Z', '2020-01-01T06:00Z']))
        source = {'source_id': 'observed', 'df': frame}
        options = period_options(source, 'Etc/GMT+6')
        self.assertIsNone(options['year'])
        self.assertEqual([row['year'] for row in options['available_years']], [2018, 2019, 2020])
        selected = select_period(source, 2019, 'Etc/GMT+6')
        self.assertEqual(selected['rows'], 2)
        self.assertTrue(pd.isna(selected['df'].iloc[0]['wind_speed']))
        self.assertEqual(selected['source_id'], 'observed')
        pd.testing.assert_frame_equal(select_period(selected, None, 'Etc/GMT+6')['df'], frame)
        pd.testing.assert_frame_equal(source['df'], frame)
        for invalid in (2017, True, '2019', 2019.0):
            with self.assertRaises(ValueError):
                select_period(source, invalid, 'Etc/GMT+6')

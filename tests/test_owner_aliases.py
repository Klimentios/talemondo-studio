import unittest
from local_studio.owner_aliases import resolve_owner
from local_studio.store import assign_performers

class OwnerAliasesTests(unittest.TestCase):
    def test_saved_pair_is_canonicalized_without_changing_performer(self):
        assets = [dict(asset_id='a', kind='image', job_role='Лицо героя', job_owner='Anna'),
                  dict(asset_id='k', kind='image', job_role='Лицо героя', job_owner='ADAM'),
                  dict(asset_id='v', kind='video', job_role='Исходное видео', job_owner='')]
        assign_performers(assets, {'v': {'pairs': [{'owner':'Адам 1', 'performer':'second cat'}]}}, {'anna','adam'})
        self.assertEqual(assets[-1]['job_performers'], [{'owner':'ADAM', 'performer':'second cat'}])

    def test_relative_scale_is_preserved_and_bounded(self):
        assets = [dict(asset_id='a', kind='image', job_role='Лицо героя', job_owner='VEGA'),
                  dict(asset_id='m', kind='image', job_role='Лицо героя', job_owner='ORION'),
                  dict(asset_id='v', kind='video', job_role='Движение и камера', job_owner='')]
        pairs = [{'owner': 'VEGA', 'performer': 'left cat'},
                 {'owner': 'ORION', 'performer': 'right cat', 'relative_scale_percent': 67}]
        assign_performers(assets, {'v': {'pairs': pairs}}, {'vega', 'orion'})
        self.assertEqual(assets[-1]['job_performers'][1]['relative_scale_percent'], 67)
        pairs[1]['relative_scale_percent'] = 10
        with self.assertRaisesRegex(ValueError, '25 до 200'):
            assign_performers(assets, {'v': {'pairs': pairs}}, {'vega', 'orion'})

    def test_ambiguous_and_unknown_names_are_not_assigned_by_order(self):
        self.assertIsNone(resolve_owner('Адам', ['ADAM 1', 'ADAM 2']))
        self.assertIsNone(resolve_owner('Mark', ['Anna', 'ADAM']))
        self.assertEqual(resolve_owner('Анна', ['Anna', 'ADAM']), 'Anna')

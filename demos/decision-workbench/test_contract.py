"""Decision API/fixture regressions; no model downloads, GPU, or credentials."""
from copy import deepcopy
from collections import Counter
import unittest

from server import fixtures, validate_request, validate_task


class FixtureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = fixtures()
        cls.by_id = {fixture['id']: fixture for fixture in cls.fixtures}

    def test_twelve_fixtures_are_six_consistent_ja_en_pairs(self):
        self.assertEqual(len(self.fixtures), 12)
        self.assertEqual(len(self.by_id), 12)
        self.assertEqual(Counter(fixture['language'] for fixture in self.fixtures), {'ja': 6, 'en': 6})
        self.assertEqual({fixture['mode'] for fixture in self.fixtures}, {'classification', 'criteria', 'sufficiency'})
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture['id']):
                self.assertIn(fixture['basis'], ('text', 'knowledge'))
                self.assertTrue(fixture['title'].strip())
                self.assertTrue(fixture['rationale'].strip())
                task = validate_task(fixture)
                self.assertIn(task['expectedOptionId'], {option['id'] for option in task['options']})
                stem, language = fixture['id'].rsplit('-', 1)
                self.assertEqual(language, fixture['language'])
                counterpart = self.by_id[f"{stem}-{'en' if language == 'ja' else 'ja'}"]
                self.assertEqual(fixture['mode'], counterpart['mode'])
                self.assertEqual(fixture['basis'], counterpart['basis'])
                self.assertEqual(fixture['expectedOptionId'], counterpart['expectedOptionId'])
                self.assertEqual([option['id'] for option in fixture['options']], [option['id'] for option in counterpart['options']])

    def test_reference_answers_distinguish_knowledge_from_missing_text(self):
        first = self.fixtures[0]
        self.assertEqual(first['id'], 'chicken-vegan-ja')
        self.assertEqual(first['basis'], 'knowledge')
        self.assertEqual(first['expectedOptionId'], 'violates')
        for language in ('ja', 'en'):
            unknown = self.by_id[f'unknown-stock-vegan-{language}']
            explicit = self.by_id[f'vegetable-stock-vegan-{language}']
            self.assertEqual(unknown['basis'], 'text')
            self.assertEqual(unknown['expectedOptionId'], 'insufficient')
            self.assertEqual(explicit['expectedOptionId'], 'meets')
            for field in ('question', 'criteria', 'options'):
                self.assertEqual(unknown[field], explicit[field], f'Only stock evidence should change: {field}')
            self.assertNotEqual(unknown['text'], explicit['text'])

    def test_same_development_request_can_be_classified_but_lack_required_details(self):
        for language in ('ja', 'en'):
            classification = self.by_id[f'csv-request-type-{language}']
            incomplete = self.by_id[f'csv-request-incomplete-{language}']
            complete = self.by_id[f'csv-request-complete-{language}']
            self.assertEqual(classification['text'], incomplete['text'])
            self.assertEqual(classification['expectedOptionId'], 'feature')
            self.assertEqual(incomplete['expectedOptionId'], 'insufficient')
            self.assertEqual(complete['expectedOptionId'], 'sufficient')
            self.assertTrue(complete['text'].startswith(incomplete['text']))
            for field in ('question', 'criteria', 'options'):
                self.assertEqual(incomplete[field], complete[field])


class RequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.task = deepcopy(fixtures()[0])

    def test_single_and_full_fixture_batches_validate_without_model_import(self):
        result = validate_request({'inputs': [self.task], 'models': ['modernbert']})
        self.assertEqual(len(result['inputs']), 1)
        self.assertEqual(result['models'], ['modernbert'])
        full = validate_request({'inputs': fixtures(), 'models': ['modernbert', 'gliclass', 'llm-adapter']})
        self.assertEqual(len(full['inputs']), 12)
        self.assertEqual(full['models'], ['modernbert', 'gliclass', 'llm-adapter'])
        adapter_only = validate_request({'inputs': [self.task], 'models': ['llm-adapter']})
        self.assertEqual(adapter_only['models'], ['llm-adapter'])
        result['inputs'][0]['options'][0]['label'] = 'external mutation'
        self.assertNotEqual(self.task['options'][0]['label'], 'external mutation')

    def test_batch_limits_and_model_allowlist_reject_malformed_requests(self):
        for request in (
            None, [], {}, {'inputs': [], 'models': ['modernbert']},
            {'inputs': [self.task] * 13, 'models': ['modernbert']},
            {'inputs': [self.task], 'models': []},
            {'inputs': [self.task], 'models': ['unknown']},
            {'inputs': [self.task], 'models': ['modernbert', 'modernbert']},
            {'inputs': [self.task], 'models': ['llm-adapter', 'llm-adapter']},
            {'inputs': [self.task], 'models': ['modernbert', 'gliclass', 'llm-adapter', 'other']},
            {'inputs': [self.task], 'models': 'modernbert'},
            {'inputs': [self.task], 'models': [None]},
        ):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    validate_request(request)

    def test_task_fields_reject_missing_oversized_and_wrong_type_values(self):
        mutations = [
            ('mode', 'unrecognized'), ('language', 'fr'), ('text', ''), ('text', '   '),
            ('text', 7), ('text', 'x' * 12001), ('question', ''), ('question', 'x' * 1501),
            ('criteria', []), ('criteria', 'x' * 4001),
        ]
        for key, value in mutations:
            with self.subTest(field=key, value_type=type(value).__name__):
                changed = deepcopy(self.task)
                changed[key] = value
                with self.assertRaises(ValueError):
                    validate_task(changed)
        for non_object in (None, [], 'text'):
            with self.assertRaises(ValueError):
                validate_task(non_object)

    def test_choices_need_two_to_four_unique_well_formed_ids(self):
        valid_options = self.task['options']
        malformed_sets = [None, [], valid_options[:1], valid_options * 2,
                          [valid_options[0], valid_options[0]], [valid_options[0], None]]
        for field, value in [('id', '../escape'), ('id', 'a' * 41), ('label', ''), ('label', 'x' * 161), ('description', 'x' * 1001)]:
            options = deepcopy(valid_options)
            options[1][field] = value
            malformed_sets.append(options)
        for options in malformed_sets:
            changed = deepcopy(self.task)
            changed['options'] = options
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    validate_task(changed)

    def test_expected_answer_must_match_current_options_or_be_absent(self):
        for invalid in ('old-option', 1, False, []):
            changed = deepcopy(self.task)
            changed['expectedOptionId'] = invalid
            with self.assertRaises(ValueError):
                validate_task(changed)
        changed = deepcopy(self.task)
        changed['expectedOptionId'] = None
        self.assertIsNone(validate_task(changed)['expectedOptionId'])
        del changed['expectedOptionId']
        self.assertIsNone(validate_task(changed)['expectedOptionId'])

    def test_fixture_reference_answer_is_cleared_when_task_or_choices_change(self):
        original = {**deepcopy(self.task), 'fixtureId': self.task['id']}
        self.assertEqual(validate_task(original)['expectedOptionId'], self.task['expectedOptionId'])
        for field, replacement in [('text', self.task['text'] + ' Changed.'), ('question', 'Different question?'), ('criteria', 'Different criterion'), ('mode', 'classification'), ('language', 'en')]:
            changed = deepcopy(original)
            changed[field] = replacement
            with self.subTest(field=field):
                self.assertIsNone(validate_task(changed)['expectedOptionId'])
        edited_options = deepcopy(original)
        edited_options['options'][0]['description'] += ' Changed.'
        self.assertIsNone(validate_task(edited_options)['expectedOptionId'])
        removed_answer = deepcopy(original)
        removed_answer['options'] = [option for option in original['options'] if option['id'] != original['expectedOptionId']]
        self.assertIsNone(validate_task(removed_answer)['expectedOptionId'])
        with self.assertRaises(ValueError):
            validate_task({**original, 'fixtureId': 'missing-fixture'})


if __name__ == '__main__':
    unittest.main()

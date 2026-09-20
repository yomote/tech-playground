"""Same-state independent questions: one bundled request versus sequential calls.

SDKs remain optional. Provider input never contains human references. All clocks
are observed client wall time, not internal model execution or per-question time.
"""
import asyncio
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil
from time import perf_counter

import adapter
import jev

COUNTS = (1, 4, 8, 16)
MODELS = ('jev', 'llm-adapter')
MODES = ('batch', 'sequential', 'compare', 'sweep')
_ID = re.compile(r'[A-Za-z0-9_-]{1,40}')
_SEMANTIC_FIELDS = ('id', 'title', 'instructions', 'criteria')


def preset():
    return json.loads(Path(__file__).with_name('triage-fixture.json').read_text(encoding='utf-8'))


def _text(value, maximum, field):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{field}: provide a nonempty string up to {maximum} characters')
    return value


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('IDs must contain 1–40 ASCII letters, digits, underscores or hyphens')
    return value


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError('Request must be an object')
    text = _text(data.get('text'), 12000, 'text')
    count = data.get('questionCount')
    if type(count) is not int or count not in COUNTS:
        raise ValueError('questionCount must be 1, 4, 8, or 16')
    mode = data.get('mode')
    if mode not in MODES:
        raise ValueError('Choose batch, sequential, compare, or sweep')
    models = data.get('models')
    if (not isinstance(models, list) or not 1 <= len(models) <= 2
            or any(not isinstance(model, str) or model not in MODELS for model in models)
            or len(set(models)) != len(models)):
        raise ValueError('Select unique models: jev and/or llm-adapter')
    source = data.get('questions')
    if not isinstance(source, list) or len(source) != 16:
        raise ValueError('Provide the 16 editable questions; questionCount selects a prefix')
    questions = []
    for question in source:
        if not isinstance(question, dict):
            raise ValueError('Each question must be an object')
        criteria = question.get('criteria')
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 4:
            raise ValueError('Provide 2 to 4 named choices per question')
        questions.append({
            'id': _identifier(question.get('id')),
            'title': _text(question.get('title'), 160, 'title'),
            'instructions': _text(question.get('instructions'), 1500, 'instructions'),
            'criteria': {_identifier(key): _text(value, 1000, 'choice description') for key, value in criteria.items()},
            'expectedOptionId': None, 'basis': 'judgment', 'rationale': '',
        })
    if len({question['id'] for question in questions}) != len(questions):
        raise ValueError('Question IDs must be unique')
    fixture = preset()
    unchanged = text == fixture['text'] and all(
        all(question[field] == original[field] for field in _SEMANTIC_FIELDS)
        for question, original in zip(questions, fixture['questions'])
    )
    if unchanged:
        for question, original in zip(questions, fixture['questions']):
            for field in ('expectedOptionId', 'basis', 'rationale'):
                question[field] = original[field]
    return dict(text=text, questions=questions, questionCount=count, models=list(models), mode=mode)


def make_payload(text, questions):
    return {'state': text, 'questions': {
        question['id']: {'instructions': question['instructions'], 'criteria': deepcopy(question['criteria'])}
        for question in questions
    }}


def parse_answers(response, questions, model):
    answers = response.get('answers') if isinstance(response, dict) else None
    if not isinstance(answers, dict) or set(answers) != {question['id'] for question in questions}:
        raise ValueError('Response must contain exactly the requested question IDs')
    parsed = []
    for question in questions:
        answer = answers[question['id']]
        choice = adapter.parse_choice_response({'answers': {'decision': answer}}, list(question['criteria']))
        result = dict(questionId=question['id'], status='ok', **choice)
        if model == 'jev':
            confidence = answer.get('confidence')
            if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError('Invalid Jev confidence')
            result['confidence'] = confidence
        expected = question.get('expectedOptionId')
        result['matchesExpected'] = choice['selectedOptionId'] == expected if expected is not None else None
        parsed.append(result)
    return parsed


def infer_call(model, payload):
    """One bounded SDK invocation. Only validated public metadata escapes errors."""
    result = dict(status='error', elapsedMs=0.0, answers={}, attempted=False)
    started = perf_counter()
    try:
        if model == 'jev':
            config = jev._configuration()
            problem = jev._problem(config)
            provider, requested = 'typesafe', config['model']
            timeout, call = jev._TIMEOUT_SECONDS, jev._request
        elif model == 'llm-adapter':
            config = adapter._configuration()
            problem = adapter._configuration_problem(config)
            if not problem and adapter._missing_dependencies(config['DEMO_LLM_PROVIDER']):
                problem = 'Install requirements-adapter.txt in this demo .venv.'
            if not problem and config['DEMO_LLM_PROVIDER'] == 'codex' and shutil.which('codex') is None:
                problem = 'Install Codex CLI and sign in with codex login.'
            provider, requested = config['DEMO_LLM_PROVIDER'], config['DEMO_LLM_MODEL']
            timeout, call = adapter._TIMEOUT_SECONDS, adapter._request
        else:
            raise ValueError('Unknown model')
        if problem:
            result['error'] = problem
            return result
        result.update(attempted=True, requestedModel=requested)

        async def bounded():
            return await asyncio.wait_for(call(config, payload), timeout=timeout)

        response = asyncio.run(bounded())
        if model == 'jev':
            identity = response.get('model')
            if not isinstance(identity, str) or not adapter._MODEL_PATTERN.fullmatch(identity) or config['api_key'] in identity:
                raise ValueError('Invalid model identity')
        else:
            identity = adapter._reported_model(response, requested, config['DEMO_LLM_API_KEY'])
        # Validate all answers before discarding untrusted raw fields (including debug).
        questions = [dict(id=key, **question) for key, question in payload['questions'].items()]
        parsed = parse_answers(response, questions, model)
        safe_answers = {}
        for answer in parsed:
            safe = dict(type='choice', choice=answer['selectedOptionId'],
                        probabilities={score['optionId']: score['score'] for score in answer['scores']})
            if model == 'jev':
                safe['confidence'] = answer['confidence']
            safe_answers[answer['questionId']] = safe
        result.update(status='ok', answers=safe_answers, providerModel=identity,
                      modelName=f'Jev / {identity}' if model == 'jev' else f'System One Adapter / {provider} / {identity}',
                      usage=adapter._safe_usage(response))
    except TimeoutError:
        result['error'] = 'Request timed out. No automatic retry or fallback was made.'
    except Exception:
        result['error'] = 'Request or response validation failed. Check provider setup and access. No retry or fallback was made.'
    finally:
        result['elapsedMs'] = round((perf_counter() - started) * 1000, 2)
    return result


def make_plan(request):
    plan = []
    for model in request['models']:
        counts = COUNTS if request['mode'] == 'sweep' else (request['questionCount'],)
        strategies = ('batch', 'sequential') if request['mode'] == 'compare' else (
            'sequential' if request['mode'] == 'sequential' else 'batch',)
        for count in counts:
            for strategy in strategies:
                plan.append(dict(model=model, strategy=strategy, questionCount=count))
    return plan


def execute(request, on_phase, on_progress):
    plan = make_plan(request)
    total = sum(1 if item['strategy'] == 'batch' else item['questionCount'] for item in plan)
    completed = 0
    for item in plan:
        phase = dict(**item, modelName=item['model'], status='error', elapsedMs=0.0,
                     requestCount=0, completedCalls=0, answers=[], calls=[])
        questions = request['questions'][:item['questionCount']]
        groups = [questions] if item['strategy'] == 'batch' else [[question] for question in questions]
        started = perf_counter()
        for group in groups:
            label = f"{item['model']} · {item['strategy']} · {item['questionCount']} questions"
            on_progress(dict(completedCalls=completed, totalCalls=total, current=label))
            call_started = perf_counter()
            try:
                call = infer_call(item['model'], make_payload(request['text'], group))
            except Exception:
                call = dict(status='error', elapsedMs=round((perf_counter() - call_started) * 1000, 2),
                            error='Decision call failed. No retry or fallback was made.')
            call_info = dict(questionIds=[question['id'] for question in group],
                             elapsedMs=call['elapsedMs'], status=call['status'])
            if call.get('attempted', True):
                phase['requestCount'] += 1
            if call['status'] == 'ok':
                try:
                    answers = parse_answers({'answers': call['answers']}, group, item['model'])
                    phase['answers'].extend(answers)
                    phase['completedCalls'] += 1
                    for field in ('modelName', 'providerModel', 'requestedModel'):
                        if field in call:
                            phase[field] = call[field]
                    call_info['usage'] = call.get('usage', {})
                except Exception:
                    call_info.update(status='error', error='Response validation failed; no answers were accepted for this call.')
            else:
                call_info['error'] = call.get('error', 'Decision call failed.')
            if call_info['status'] == 'error':
                phase['answers'].extend(dict(questionId=question['id'], status='error', selectedOptionId=None,
                                             scores=[], matchesExpected=None, error=call_info['error']) for question in group)
            phase['calls'].append(call_info)
            completed += 1
            on_progress(dict(completedCalls=completed, totalCalls=total, current=label))
        phase['elapsedMs'] = round((perf_counter() - started) * 1000, 2)
        successes = sum(answer['status'] == 'ok' for answer in phase['answers'])
        phase['status'] = 'ok' if successes == len(questions) else 'partial' if successes else 'error'
        if successes < len(questions):
            phase['error'] = 'Some decision calls failed. See individual call errors; failed answers have no correctness score.'
        on_phase(phase)
    on_progress(dict(completedCalls=completed, totalCalls=total, current='Finished'))

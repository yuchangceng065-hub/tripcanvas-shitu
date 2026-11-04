"""Strict application action schema for providers supporting constrained JSON."""
ACTIONS = ('route_day', 'validate_plan', 'compare_plans', 'propose_plan', 'need_input', 'give_up')


def decision_format(candidate_ids, anchor_ids=()):
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    visits = {'type': 'string', 'enum': list(dict.fromkeys(candidate_ids))}
    days = {'type': 'array', 'items': {'type': 'array', 'items': visits}}
    route_ids = {'type': 'array', 'items': {'type': 'string', 'enum': list(dict.fromkeys([*candidate_ids, *anchor_ids]))}}
    args = {'anyOf': [
        obj({'placeIds': route_ids, 'mode': {'type': 'string', 'enum': ['walk', 'drive']}}),
        obj({'days': days, 'summary': {'type': 'string'}}),
        obj({'plans': {'type': 'array', 'items': days}}),
        obj({'question': {'type': 'string'}}),
        obj({'reason': {'type': 'string'}}),
    ]}
    return {'type': 'json_schema', 'json_schema': {'name': 'hangzhou_agent_action', 'strict': True,
        'schema': obj({'action': {'type': 'string', 'enum': list(ACTIONS)}, 'args': args})}}

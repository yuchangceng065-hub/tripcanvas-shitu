"""Strict application action schema for providers supporting constrained JSON."""
ACTIONS = ('route_day', 'validate_plan', 'compare_plans', 'propose_plan', 'search_places', 'add_candidate', 'need_input', 'give_up')


def decision_format(candidate_ids, anchor_ids=(), *, repair=False):
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    visits = {'type': 'string', 'enum': list(dict.fromkeys(candidate_ids))}
    days = {'type': 'array', 'items': {'type': 'array', 'items': visits}}
    route_ids = {'type': 'array', 'items': {'type': 'string', 'enum': list(dict.fromkeys([*candidate_ids, *anchor_ids]))}}
    variants = [
        obj({'placeIds': route_ids, 'mode': {'type': 'string', 'enum': ['walk', 'drive']}}),
        obj({'days': days, 'summary': {'type': 'string'}}),
        obj({'plans': {'type': 'array', 'items': days}}),
        obj({'question': {'type': 'string'}}),
        obj({'reason': {'type': 'string'}}),
        obj({'query': {'type': 'string'}}),
        obj({'placeId': {'type': 'string'}}),
    ]
    if repair:
        variants.append(obj({'seedDays': days}))
    actions = [*ACTIONS, 'repair_plan'] if repair else list(ACTIONS)
    return {'type': 'json_schema', 'json_schema': {'name': 'hangzhou_agent_action', 'strict': True,
        'schema': obj({'action': {'type': 'string', 'enum': actions}, 'args': {'anyOf': variants}})}}

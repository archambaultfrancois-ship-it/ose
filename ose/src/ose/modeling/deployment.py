"""Derive execution profiles without modifying the deployment source or its paths."""
from copy import deepcopy


def deployment_profile(deployment, profile=None, *, routing=None, development_tokens=False):
    if profile not in (None, 'local', 'cluster'):
        raise ValueError('deployment profile must be local or cluster')
    if routing not in (None, 'mixed', 'broker'):
        raise ValueError('routing must be mixed or broker')
    if profile is None and (routing is not None or development_tokens):
        raise ValueError('routing and development tokens require an explicit deployment profile')
    result = deepcopy(deployment)
    if profile == 'local':
        if result.get('execution') == 'distributed':
            result.pop('subscriptions', None)
        result.pop('execution', None)
        for component in result['components'].values():
            component.pop('placement', None)
    elif profile == 'cluster':
        result['execution'] = 'distributed'
        if routing == 'broker':
            result['subscriptions'] = {key: {'routing': 'broker', 'transport': 'tcp'}
                                       for key in result.get('subscriptions', {})}
        if not development_tokens:
            for client in result.get('parameter_clients', {}).values():
                client.pop('token', None)
    return result

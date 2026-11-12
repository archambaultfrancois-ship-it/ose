"""Keep a two-deployment editor fixture as Vibration uses a single source."""
import copy
import json


def split_deployments(root):
    source = root / 'deployment.json'
    cluster = json.loads(source.read_text())
    local = copy.deepcopy(cluster)
    local.pop('execution', None)
    local.pop('subscriptions', None)
    for component in local['components'].values():
        component.pop('placement', None)
    for client in cluster['parameter_clients'].values():
        client.pop('token', None)
    for profile, document in (('local', local), ('cluster', cluster)):
        (root / f'deployment.{profile}.json').write_text(json.dumps(document, indent=2) + '\n')
    source.unlink()

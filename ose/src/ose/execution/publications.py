"""Topic contracts are independent of the producers currently supplying them."""

def topic_contracts(plan):
    return {**plan.get('external_topics', {}), **{
        f'{name}.{port}': descriptor for name, node in plan['components'].items()
        for port, descriptor in node['outputs'].items()}}


class Producers:
    def __init__(self, plan, codec):
        self.plan, self.codec = plan, codec
        self.topics = dict(topic_contracts(plan))
        self.members = {}

    def declare(self, owner, topic, root, catalogue):
        if not isinstance(topic, str) or not 0 < len(topic) <= 200 or not isinstance(root, str):
            raise ValueError('invalid topic or root')
        if any(topic.startswith(name + '.record.') for name in self.plan['components']):
            raise ValueError('recording points are not application outputs')
        if (owner, topic) in self.members:
            raise ValueError('publisher already declared; release it first')
        if root not in self.codec.api.message_names(self.codec.api.prepare(catalogue)):
            raise ValueError('unknown root')
        contract = self.topics.get(topic)
        consumers = [contract] if contract else []
        consumers += [self.plan['components'][sub['to'].split('.')[0]]['inputs'][sub['to'].split('.')[1]]
                      for sub in self.plan['subscriptions'] if sub['from'] == topic]
        for consumer in consumers:
            if consumer['root'] != root:
                raise ValueError('topic root differs from its contract')
            self.codec.compare(catalogue, consumer['catalogue'], root)
        descriptor = dict(root=root, catalogue=catalogue, catalogue_id=self.codec.identity(catalogue))
        self.topics.setdefault(topic, descriptor)
        self.members[owner, topic] = descriptor
        return descriptor

    def release(self, owner, topic=None):
        for key in list(self.members):
            if key[0] == owner and (topic is None or key[1] == topic):
                self.members.pop(key)

    def contracts(self, topic):
        return [desc for (_, name), desc in self.members.items() if name == topic]

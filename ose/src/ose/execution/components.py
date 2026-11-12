"""Versioned component supervision, acknowledged at callback boundaries."""
import asyncio


def disabled_components(plan, names):
    names = set(names or ())
    unknown = names - plan['components'].keys()
    if unknown:
        raise ValueError('unknown components: ' + ', '.join(sorted(unknown)))
    return names


class ComponentStates:
    def __init__(self, plan, disabled=()):
        disabled = disabled_components(plan, disabled)
        self.desired = {name: dict(enabled=name not in disabled, version=0) for name in plan['components']}
        self.observed = {name: None for name in plan['components']}
        self.versions = {name: None for name in plan['components']}
        self.changed = asyncio.Event()
        self.transition_lock = asyncio.Lock()

    def snapshot(self):
        return {name: dict(**state, active=self.observed[name], pending=self.versions[name] != state['version'])
                for name, state in self.desired.items()}

    def disconnected(self, name):
        self.observed[name] = None
        self.versions[name] = None
        self.changed.set()

    async def poll(self, name, after=None):
        # A different component can wake this shared event. Return the current
        # state instead of restarting the wait and exceeding the RPC deadline.
        if after == self.desired[name]['version']:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), 10)
            except asyncio.TimeoutError:
                pass
        return dict(self.desired[name])

    def acknowledge(self, name, version, enabled):
        state = self.desired[name]
        if type(version) is not int or type(enabled) is not bool:
            raise ValueError('invalid component acknowledgement')
        if state == dict(version=version, enabled=enabled):
            self.observed[name] = enabled
            self.versions[name] = version
            self.changed.set()
            return True
        return False

    async def set(self, names, enabled):
        async with self.transition_lock:
            return await self._set(names, enabled)

    async def _set(self, names, enabled):
        if not isinstance(names, list) or not names or any(name not in self.desired for name in names):
            raise ValueError('select known components')
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        for name in names:
            state = self.desired[name]
            if state['enabled'] != enabled:
                state.update(enabled=enabled, version=state['version'] + 1)
        self.changed.set()
        async def wait():
            def complete():
                return all(self.versions[name] == self.desired[name]['version'] for name in names)
            while not complete():
                self.changed.clear()
                if complete():
                    break
                await self.changed.wait()
        try:
            await asyncio.wait_for(wait(), 20)
        except asyncio.TimeoutError as error:
            raise ValueError('component transition timed out; inspect component status') from error
        return self.snapshot()


class ComponentRuntime:
    """Shared callback-boundary gate for local adapters and distributed workers."""
    def setup_component_control(self):
        self.component_lock = asyncio.Lock()
        self.component_started = asyncio.Event()
        self.component_active = False
        self.component_version = None

    async def apply_component_state(self, enabled):
        async with self.component_lock:
            self.scheduler.enable(enabled, self._input_now())
            if enabled and not self.component_started.is_set():
                await self.initialize_component()
                self.component_started.set()
            if not enabled:
                for queue in getattr(self, 'network_queues', {}).values():
                    while not queue.empty():
                        queue.get_nowait()
            self.component_active = enabled
            self.activity.set()

    async def initialize_component_state(self):
        state, _ = await self.supervision_request('component_poll')
        await self.apply_component_state(state['enabled'])
        self.component_version = state['version']
        await self.supervision_request('component_ack', version=state['version'], enabled=state['enabled'])

    async def component_control(self):
        while not self.closing:
            state, _ = await self.supervision_request('component_poll', after=self.component_version)
            if state['version'] != self.component_version:
                await self.apply_component_state(state['enabled'])
                self.component_version = state['version']
                await self.supervision_request('component_ack', version=state['version'], enabled=state['enabled'])

    async def component_exit(self):
        await self.component_started.wait()
        return await self.process.wait()


async def command(options):
    import json
    from pathlib import Path
    from ose.transport.secure import connect, contexts
    if options.operation != 'status' and not options.components:
        raise ValueError('select at least one component')
    endpoint = {}
    if options.host is None and options.port is None and Path(options.endpoint).is_file():
        endpoint = json.loads(Path(options.endpoint).read_text())
    host, port = options.host or endpoint.get('host', '127.0.0.1'), options.port or endpoint.get('port')
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('select a valid endpoint or --host/--port')
    context = None
    if options.distributed:
        _, context = contexts(options.tls_ca, options.tls_cert, options.tls_key, insecure=options.insecure, hosts=[host])
    elif options.insecure or any((options.tls_ca, options.tls_cert, options.tls_key)):
        raise ValueError('TLS options require --distributed')
    client = await connect(host, port, 1024 * 1024, context, 'coordinator' if options.distributed else None)
    try:
        if options.operation == 'status':
            result, _ = await client.request('component_status')
            if options.components:
                if any(name not in result for name in options.components):
                    raise ValueError('unknown component')
                result = {name: result[name] for name in options.components}
        else:
            result, _ = await client.request('component_set', components=options.components,
                                             enabled=options.operation == 'enable', timeout=25)
        print(json.dumps(result, indent=2), flush=True)
    finally:
        await client.close()

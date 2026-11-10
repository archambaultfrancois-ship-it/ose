"""Shared Python component context services and synchronous callback loading.

No snapshot dispatch or CH/1 session is imported by this module.
"""
import importlib.util
import inspect
from pathlib import Path
import sys
from ose.execution.component_io import RecordResult


class ComponentContext:
    """Typed bindings, parameters, recording, measurements and logs."""
    def __init__(self, session, name, input_types, output_types, codec, parameter_types=None):
        self.session, self.name, self.codec = session, name, codec
        self.config = session.config
        self.parameters = self.config.get("parameters", {})
        self.input_types, self.output_types = input_types, output_types
        message_inputs = {port for port, desc in self.config["inputs"].items() if desc.get("kind") != "timer"}
        if set(input_types) != message_inputs or set(output_types) != set(self.config["outputs"]):
            raise ValueError("generated ports differ from INIT; rebuild the container")
        for direction, types in (("inputs", input_types), ("outputs", output_types)):
            for port, cls in types.items():
                if codec.api.message_name(cls) != self.config[direction][port]["root"]:
                    raise ValueError("generated types differ from INIT; rebuild the container")
        self._parameter_writes = True
        self.parameter_types = parameter_types or {}
        if set(self.parameter_types) != set(self.config.get('parameter_bindings', {})):
            raise ValueError('generated parameter bindings differ from INIT; rebuild the container')
        self._parameter_codecs = {}
        for alias, cls in self.parameter_types.items():
            binding = self.config['parameter_bindings'][alias]
            if codec.api.message_name(cls) != binding['root']:
                raise ValueError('generated parameter root differs from INIT')
            self._parameter_codecs[alias] = codec.api.prepare(binding['catalogue'], [cls])
        self._active = False
        self._recording_types = {}
        self._encoders = {port: codec.api.prepare(self.config["outputs"][port]["catalogue"], [cls])
                          for port, cls in output_types.items()}

    @property
    def time_ns(self):
        return self.session.time_ns

    @property
    def tick_index(self):
        return self.session.tick_index

    def now(self):
        return self.time_ns / 1_000_000_000

    def declare_recording_point(self, name, message_type, description=""):
        if self.session.is_ready or not self._parameter_writes:
            raise RuntimeError("recording declarations require on_start")
        root = self.codec.api.message_name(message_type)
        catalogue = self.codec.api.description([message_type])
        encoder = self.codec.api.prepare(catalogue, [message_type])
        self.session.declare_recording_point(name, root, catalogue, description)
        self._recording_types[name] = (message_type, encoder)

    def recording_enabled(self, name):
        return self.session.recording_enabled(name)

    def log(self, level, message, logger=''):
        self.session.log(level, message, logger)

    def logf(self, level, format, *arguments, logger=''):
        self.session.logf(level, format, *arguments, logger=logger)

    def declare_measurement(self, name):
        if self.session.is_ready or not self._parameter_writes:
            raise RuntimeError('measurement declarations require on_start')
        self.session.declare_measurement(name)

    def measure(self, name, value):
        if not self._active:
            raise RuntimeError('measurement emission requires an active input callback')
        self.session.measure(name, value)

    def record(self, name, message):
        if not self._active:
            raise RuntimeError("recording requires an active input callback")
        message_type, encoder = self._recording_types[name]
        if not isinstance(message, message_type):
            raise TypeError(f"recording {name} expects {message_type.__name__}")
        if not self.recording_enabled(name):
            return RecordResult.DISABLED
        return self.session.record(name, self.codec.api.encode(encoder, message))

    def parameter(self, alias):
        binding = self.config.get('parameter_bindings', {}).get(alias)
        if binding is None or 'read' not in binding['access']:
            raise ValueError('parameter read access denied')
        catalogue, payload = self.session.parameter_values[alias]
        value = self.codec.api.decode(self._parameter_codecs[alias], payload)
        if not isinstance(value, self.parameter_types[alias]):
            raise ValueError('wrong parameter message root')
        return value

    def set_parameter(self, alias, value):
        if not self._parameter_writes:
            raise RuntimeError("parameter writes require an active initialization or input callback")
        if alias not in self.parameter_types or not isinstance(value, self.parameter_types[alias]):
            raise TypeError('wrong parameter message type')
        payload = self.codec.api.encode(self._parameter_codecs[alias], value)
        self.session.set_parameters(alias, payload)


def _load_handler(path):
    path = Path(path).resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_ose_component_handler", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load Python handler: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _callback(module, name, nargs, *, required=False):
    callback = getattr(module, name, None)
    if callback is None and not required:
        return None
    if not callable(callback) or inspect.iscoroutinefunction(callback):
        raise ValueError(f"handler must define synchronous {name}")
    try:
        inspect.signature(callback).bind(*([None] * nargs))
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid callback signature: {name} expects {nargs} arguments") from error
    return callback


def _call(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("component callbacks must be synchronous")

import math
from messages import Analysis, Feature, Spectrum
from common import start, observe, burn


def on_start(ctx):
    start(ctx, 'temperature', 'deviation', 'samples', 'cpu_probe')


def on_input(ctx, input):
    frame = input.message
    values = frame.channels[0].samples
    mean = sum(values) / len(values)
    deviation = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    sequence = frame.header.sequence
    probe = burn(ctx.parameters_workload().cpu_iterations, mean)
    features = [Feature(name='minimum', value=min(values), unit='C'),
                Feature(name='maximum', value=max(values), unit='C')]
    if sequence % 2:
        features.append(Feature(name='peak-to-peak', value=max(values) - min(values), unit=None))
    result = Analysis(header=frame.header, temperature=mean, deviation=deviation,
        sample_count=sum(len(channel.samples) for channel in frame.channels), features=features,
        spectrum=Spectrum(bins=[abs(math.sin(mean + n)) for n in range(4 + sequence % 61)], resolution=.5)
            if sequence % 3 == 0 else None, diagnostic=frame.diagnostic)
    ctx.publish_analysis(result, id=input.app_id)
    observe(ctx, temperature=mean, deviation=deviation, samples=result.sample_count, cpu_probe=probe)

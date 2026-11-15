/* Complete CH/2 analyzer: decoding, parameters and publishing use the C SDK.
 * analyzer.py is a functional reference; floating-point rounding may differ.
 */
#include "component.h"
#include <inttypes.h>
#include <math.h>
#include <stdint.h>

static uint64_t received;
static int on_start(OseContext *ctx) {
    received = 0;
    const char *names[] = {"messages", "temperature", "deviation", "samples", "cpu_probe"};
    for (size_t i = 0; i < sizeof(names) / sizeof(*names); ++i)
        if (!ose_declare_measurement(ctx, names[i])) return 0;
    return ose_logf(ctx, OSE_LOG_INFO, "lifecycle", "%s ready", ctx->name);
}

static int on_input(OseContext *ctx, const Input_input *input) {
    const SensorFrame *frame = input->message;
    const Workload *workload = parameters_workload(ctx);
    if (!frame || !workload || !frame->channels_count || !frame->channels[0].samples_count)
        return ose_fail(ctx, "analysis requires a nonempty first channel and workload parameters");
    const Channel *channel = &frame->channels[0];
    double sum = 0, squares = 0;
    double minimum = channel->samples[0], maximum = minimum;
    for (uint32_t k = 0; k < channel->samples_count; ++k) {
        double value = channel->samples[k];
        if (!isfinite(value)) return ose_fail(ctx, "analysis requires finite samples");
        sum += value;
        if (value < minimum) minimum = value;
        if (value > maximum) maximum = value;
    }
    double mean = sum / channel->samples_count;
    for (uint32_t k = 0; k < channel->samples_count; ++k) {
        double delta = (double)channel->samples[k] - mean;
        squares += delta * delta;
    }
    double deviation = sqrt(squares / channel->samples_count);
    double probe = mean;
    uint32_t iterations = workload->cpu_iterations > 200000 ? 200000 : workload->cpu_iterations;
    for (uint32_t n = 0; n < iterations; ++n) {
        probe = sin(probe + (double)n * .001) * .5;
    }
    uint64_t sample_count = 0;
    for (uint32_t j = 0; j < frame->channels_count; ++j)
        sample_count += frame->channels[j].samples_count;
    if (sample_count > UINT32_MAX) return ose_fail(ctx, "analysis sample count exceeds uint32");

    Feature features[3] = {
        {.name="minimum", .value=minimum, .has_unit=true, .unit="C"},
        {.name="maximum", .value=maximum, .has_unit=true, .unit="C"},
        {.name="peak-to-peak", .value=maximum - minimum},
    };
    float bins[64];
    Analysis result = {0};
    result.header = frame->header;
    result.temperature = mean;
    result.deviation = deviation;
    result.sample_count = (uint32_t)sample_count;
    result.features = features;
    result.features_count = 2 + (frame->header.sequence % 2 != 0);
    if (frame->header.sequence % 3 == 0) {
        result.has_spectrum = true;
        result.spectrum.bins_count = 4 + frame->header.sequence % 61;
        result.spectrum.bins = bins;
        result.spectrum.resolution = .5;
        for (uint32_t n = 0; n < result.spectrum.bins_count; ++n)
            bins[n] = (float)fabs(sin(mean + n));
    }
    /* The decoded diagnostic is borrowed until this callback returns. The SDK
     * encodes synchronously, so no deep copy or ownership transfer is needed. */
    result.has_diagnostic = frame->has_diagnostic;
    result.diagnostic = frame->diagnostic;
    int published = input->meta.has_app_id
        ? publish_analysis_with_id(ctx, &result, input->meta.app_id)
        : publish_analysis(ctx, &result);
    if (!published) return 0;
    ++received;
    if (!(ose_measure(ctx, "messages", (double)received) &&
          ose_measure(ctx, "temperature", mean) &&
          ose_measure(ctx, "deviation", deviation) &&
          ose_measure(ctx, "samples", (double)sample_count) &&
          ose_measure(ctx, "cpu_probe", probe))) return 0;
    uint32_t every = workload->log_every < 1 ? 1 : workload->log_every > 10000 ? 10000 : workload->log_every;
    if (received % every == 0)
        return ose_logf(ctx, OSE_LOG_INFO, "workload",
            "%s: messages=%" PRIu64 " {'temperature': %.17g, 'deviation': %.17g, 'samples': %" PRIu64 ", 'cpu_probe': %.17g}",
            ctx->name, received, mean, deviation, sample_count, probe);
    return 1;
}

const ComponentCallbacks component_callbacks = {.on_start=on_start, .on_input=on_input};

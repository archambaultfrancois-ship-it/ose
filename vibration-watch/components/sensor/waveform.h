#ifndef VW_SENSOR_WAVEFORM_H
#define VW_SENSOR_WAVEFORM_H
#include <math.h>
#include <stdint.h>

static inline float vibration_sample(uint64_t index, double rate, double frequency,
                                     double amplitude, double noise, uint32_t *state) {
    *state ^= *state << 13;
    *state ^= *state >> 17;
    *state ^= *state << 5;
    double jitter = ((double)*state / UINT32_MAX * 2.0 - 1.0) * noise;
    return (float)(amplitude * sin(6.2831853071795864769 * frequency * (double)index / rate) + jitter);
}
#endif

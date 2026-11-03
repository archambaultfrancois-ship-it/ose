#include "../../components/sensor/waveform.h"
#include <assert.h>
int main(void) {
    uint32_t a = 42, b = 42;
    double squares = 0;
    for (uint64_t i = 0; i < 4096; ++i) {
        float x = vibration_sample(i, 4096, 64, 2, .01, &a);
        assert(x == vibration_sample(i, 4096, 64, 2, .01, &b));
        assert(fabs(x) <= 2.01);
        squares += (double)x * x;
    }
    assert(fabs(sqrt(squares / 4096) - sqrt(2)) < .01);
    return 0;
}

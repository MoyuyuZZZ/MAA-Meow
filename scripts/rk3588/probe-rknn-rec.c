#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "rknn_api.h"

/*
 * Standalone OCR rec benchmark. Input: packed uint8 NHWC BGR text-line canvases (N x 48x320x3).
 * Options select warm-up count, call interval, FP16 output (argmax on the native FP16 buffer),
 * line limit, per-call timing CSV with NPU devfreq samples, and a separate per-layer perf mode.
 * Result file: per line and timestep argmax class (int32) and probability (float32), then the
 * float32 output of the first FULL_LINES lines, for VM-side comparison.
 */

#define FULL_LINES 8
#define MAX_COUNT 1000
#define NPU_FREQ "/sys/class/devfreq/fdab0000.npu/cur_freq"

typedef int (*init_fn)(rknn_context *, void *, uint32_t, uint32_t, rknn_init_extend *);
typedef int (*destroy_fn)(rknn_context);
typedef int (*query_fn)(rknn_context, rknn_query_cmd, void *, uint32_t);
typedef int (*inputs_set_fn)(rknn_context, uint32_t, rknn_input[]);
typedef int (*run_fn)(rknn_context, rknn_run_extend *);
typedef int (*outputs_get_fn)(rknn_context, uint32_t, rknn_output[], rknn_output_extend *);
typedef int (*outputs_release_fn)(rknn_context, uint32_t, rknn_output[]);

static struct {
    init_fn init;
    destroy_fn destroy;
    query_fn query;
    inputs_set_fn inputs_set;
    run_fn run;
    outputs_get_fn outputs_get;
    outputs_release_fn outputs_release;
} api;

typedef struct {
    int fp16;
    uint32_t steps, classes;
    int32_t *best_class; /* steps */
    float *best_prob;    /* steps */
    float *full;         /* steps * classes, optional */
} decode_target;

typedef struct {
    long freq_pre, freq_post;
    double set, run, get;
} sample;

static int freq_fd = -1;

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec / 1e6;
}

static long npu_freq(void) {
    char buffer[32];
    if (freq_fd < 0)
        return -1;
    ssize_t n = pread(freq_fd, buffer, sizeof(buffer) - 1, 0);
    if (n <= 0)
        return -1;
    buffer[n] = '\0';
    return strtol(buffer, NULL, 10);
}

static void *read_file(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    if (file == NULL)
        return NULL;
    void *data = NULL;
    long length = -1;
    if (fseek(file, 0, SEEK_END) == 0 && (length = ftell(file)) > 0 && fseek(file, 0, SEEK_SET) == 0 &&
        (data = malloc((size_t)length)) != NULL && fread(data, 1, (size_t)length, file) != (size_t)length) {
        free(data);
        data = NULL;
    }
    fclose(file);
    *size = data != NULL ? (size_t)length : 0;
    return data;
}

static int compare_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void print_stats(const char *label, double *values, size_t count) {
    if (count == 0)
        return;
    qsort(values, count, sizeof(double), compare_double);
    double sum = 0;
    for (size_t i = 0; i < count; ++i)
        sum += values[i];
    printf("%s n=%zu min_ms=%.3f median_ms=%.3f mean_ms=%.3f p90_ms=%.3f max_ms=%.3f\n", label, count, values[0],
           values[count / 2], sum / (double)count, values[count * 9 / 10], values[count - 1]);
}

/* One inference; argmax over each timestep of the float or native FP16 output buffer. */
static int infer(rknn_context ctx, uint8_t *canvas, uint32_t bytes, decode_target *out, sample *s) {
    rknn_input input = {.index = 0, .buf = canvas, .size = bytes, .type = RKNN_TENSOR_UINT8, .fmt = RKNN_TENSOR_NHWC};
    s->freq_pre = npu_freq();
    double t0 = now_ms();
    if (api.inputs_set(ctx, 1, &input) < 0)
        return 23;
    double t1 = now_ms();
    if (api.run(ctx, NULL) < 0)
        return 23;
    double t2 = now_ms();
    rknn_output output = {.want_float = out->fp16 ? 0 : 1, .index = 0};
    if (api.outputs_get(ctx, 1, &output, NULL) < 0)
        return 24;
    const size_t count = (size_t)out->steps * out->classes;
    int status = output.size == count * (out->fp16 ? sizeof(__fp16) : sizeof(float)) ? 0 : 24;
    for (uint32_t t = 0; status == 0 && t < out->steps; ++t) {
        uint32_t arg = 0;
        float best;
        if (out->fp16) {
            const __fp16 *row = (const __fp16 *)output.buf + (size_t)t * out->classes;
            __fp16 value = row[0];
            for (uint32_t c = 1; c < out->classes; ++c)
                if (row[c] > value) {
                    value = row[c];
                    arg = c;
                }
            best = (float)value;
        } else {
            const float *row = (const float *)output.buf + (size_t)t * out->classes;
            for (uint32_t c = 1; c < out->classes; ++c)
                if (row[c] > row[arg])
                    arg = c;
            best = row[arg];
        }
        out->best_class[t] = (int32_t)arg;
        out->best_prob[t] = best;
    }
    double t3 = now_ms();
    if (status == 0 && out->full != NULL) {
        for (size_t i = 0; i < count; ++i)
            out->full[i] = out->fp16 ? (float)((const __fp16 *)output.buf)[i] : ((const float *)output.buf)[i];
    }
    api.outputs_release(ctx, 1, &output);
    s->freq_post = npu_freq();
    s->set = t1 - t0;
    s->run = t2 - t1;
    s->get = t3 - t2;
    return status;
}

static void summarize(const char *phase, const sample *samples, size_t count, double *scratch) {
    char label[96];
    const char *names[] = {"inputs_set", "run", "outputs_get+argmax", "total"};
    for (int k = 0; k < 4; ++k) {
        for (size_t i = 0; i < count; ++i) {
            const sample *s = &samples[i];
            scratch[i] = k == 0 ? s->set : k == 1 ? s->run : k == 2 ? s->get : s->set + s->run + s->get;
        }
        snprintf(label, sizeof(label), "%s stage=%s", phase, names[k]);
        print_stats(label, scratch, count);
    }
    long freqs[16];
    size_t nfreq = 0;
    for (size_t i = 0; i < count; ++i) {
        size_t f = 0;
        while (f < nfreq && freqs[f] != samples[i].freq_pre)
            ++f;
        if (f == nfreq && nfreq < 16)
            freqs[nfreq++] = samples[i].freq_pre;
    }
    for (size_t f = 0; f < nfreq; ++f) {
        size_t n = 0;
        for (size_t i = 0; i < count; ++i)
            if (samples[i].freq_pre == freqs[f])
                scratch[n++] = samples[i].run;
        snprintf(label, sizeof(label), "%s stage=run freq_mhz=%ld", phase, freqs[f] / 1000000);
        print_stats(label, scratch, n);
    }
}

static void write_csv(FILE *csv, const char *phase, const sample *samples, size_t count) {
    for (size_t i = 0; csv != NULL && i < count; ++i)
        fprintf(csv, "%s,%zu,%ld,%ld,%.4f,%.4f,%.4f\n", phase, i, samples[i].freq_pre, samples[i].freq_post,
                samples[i].set, samples[i].run, samples[i].get);
}

static void usage(const char *name) {
    fprintf(stderr,
            "Usage: %s [-w warmup] [-i interval_ms] [-n max_lines] [-f] [-p perf_detail.txt] [-t timing.csv]\n"
            "          [-o result.bin] /abs/librknnrt.so rec.rknn canvases.u8\n",
            name);
}

int main(int argc, char **argv) {
    long warmup = 30, interval_ms = 0, max_lines = MAX_COUNT;
    int fp16 = 0;
    const char *perf_path = NULL, *csv_path = NULL, *result_path = NULL;
    int opt;
    while ((opt = getopt(argc, argv, "w:i:n:fp:t:o:")) != -1) {
        switch (opt) {
        case 'w': warmup = strtol(optarg, NULL, 10); break;
        case 'i': interval_ms = strtol(optarg, NULL, 10); break;
        case 'n': max_lines = strtol(optarg, NULL, 10); break;
        case 'f': fp16 = 1; break;
        case 'p': perf_path = optarg; break;
        case 't': csv_path = optarg; break;
        case 'o': result_path = optarg; break;
        default: usage(argv[0]); return 2;
        }
    }
    if (argc - optind != 3 || argv[optind][0] != '/' || warmup < 0 || warmup > MAX_COUNT || interval_ms < 0 ||
        interval_ms > 5000 || max_lines < 1 || max_lines > MAX_COUNT) {
        usage(argv[0]);
        return 2;
    }
    const char *runtime_path = argv[optind], *model_path = argv[optind + 1], *canvas_path = argv[optind + 2];
    printf("identity uid=%lu gid=%lu warmup=%ld interval_ms=%ld fp16_output=%d perf=%d\n", (unsigned long)getuid(),
           (unsigned long)getgid(), warmup, interval_ms, fp16, perf_path != NULL);
    freq_fd = open(NPU_FREQ, O_RDONLY | O_CLOEXEC);
    printf("npu_freq_readable=%d idle_freq_mhz=%ld\n", freq_fd >= 0, npu_freq() / 1000000);

    void *runtime = dlopen(runtime_path, RTLD_NOW | RTLD_LOCAL);
    if (runtime == NULL) {
        fprintf(stderr, "runtime_load=FAIL error=%s\n", dlerror());
        return 10;
    }
    struct {
        const char *name;
        void **slot;
    } symbols[] = {
        {"rknn_init", (void **)&api.init},
        {"rknn_destroy", (void **)&api.destroy},
        {"rknn_query", (void **)&api.query},
        {"rknn_inputs_set", (void **)&api.inputs_set},
        {"rknn_run", (void **)&api.run},
        {"rknn_outputs_get", (void **)&api.outputs_get},
        {"rknn_outputs_release", (void **)&api.outputs_release},
    };
    for (size_t i = 0; i < sizeof(symbols) / sizeof(symbols[0]); ++i) {
        if ((*symbols[i].slot = dlsym(runtime, symbols[i].name)) == NULL) {
            fprintf(stderr, "runtime_symbol=%s FAIL\n", symbols[i].name);
            dlclose(runtime);
            return 11;
        }
    }

    int status = 0, ctx_ready = 0;
    rknn_context ctx = 0;
    size_t model_size = 0, canvas_size = 0, lines = 0, full_lines = 0;
    uint32_t steps = 0, classes = 0, line_bytes = 0;
    void *model = read_file(model_path, &model_size);
    uint8_t *canvases = read_file(canvas_path, &canvas_size);
    int32_t *best_class = NULL;
    float *best_prob = NULL, *full = NULL;
    sample *samples = NULL;
    double *scratch = NULL;
    FILE *csv = NULL, *file = NULL;
    if (model == NULL || canvases == NULL) {
        fputs("file_read=FAIL\n", stderr);
        status = 20;
        goto cleanup;
    }
    double t_init = now_ms();
    if (api.init(&ctx, model, (uint32_t)model_size, perf_path != NULL ? RKNN_FLAG_COLLECT_PERF_MASK : 0, NULL) < 0) {
        fputs("rknn_init=FAIL\n", stderr);
        status = 21;
        goto cleanup;
    }
    ctx_ready = 1;
    printf("rknn_init=PASS ms=%.3f model_bytes=%zu\n", now_ms() - t_init, model_size);
    rknn_sdk_version sdk;
    rknn_input_output_num io;
    rknn_tensor_attr in_attr, out_attr;
    memset(&sdk, 0, sizeof(sdk));
    memset(&io, 0, sizeof(io));
    memset(&in_attr, 0, sizeof(in_attr));
    memset(&out_attr, 0, sizeof(out_attr));
    if (api.query(ctx, RKNN_QUERY_SDK_VERSION, &sdk, sizeof(sdk)) == RKNN_SUCC)
        printf("sdk api_version=%s drv_version=%s\n", sdk.api_version, sdk.drv_version);
    if (api.query(ctx, RKNN_QUERY_IN_OUT_NUM, &io, sizeof(io)) != RKNN_SUCC ||
        api.query(ctx, RKNN_QUERY_INPUT_ATTR, &in_attr, sizeof(in_attr)) != RKNN_SUCC ||
        api.query(ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr, sizeof(out_attr)) != RKNN_SUCC || io.n_input != 1 ||
        io.n_output != 1 || out_attr.n_dims != 3 || (fp16 && out_attr.type != RKNN_TENSOR_FLOAT16)) {
        fputs("rknn_query=FAIL or unexpected I/O layout\n", stderr);
        status = 22;
        goto cleanup;
    }
    line_bytes = in_attr.n_elems;
    steps = out_attr.dims[1];
    classes = out_attr.dims[2];
    lines = canvas_size / line_bytes;
    if (lines == 0 || canvas_size % line_bytes != 0) {
        fputs("canvas_size=UNEXPECTED\n", stderr);
        status = 22;
        goto cleanup;
    }
    if (lines > (size_t)max_lines)
        lines = (size_t)max_lines;
    full_lines = lines < FULL_LINES ? lines : FULL_LINES;
    printf("input dims=%ux%ux%ux%u; output dims=%ux%ux%u type=%d; lines=%zu\n", in_attr.dims[0], in_attr.dims[1],
           in_attr.dims[2], in_attr.dims[3], out_attr.dims[0], steps, classes, out_attr.type, lines);
    const size_t count = (size_t)steps * classes, most = lines > (size_t)warmup ? lines : (size_t)warmup;
    best_class = malloc(lines * steps * sizeof(int32_t));
    best_prob = malloc(lines * steps * sizeof(float));
    full = malloc(full_lines * count * sizeof(float));
    samples = malloc((most + 1) * sizeof(sample));
    scratch = malloc((most + 1) * sizeof(double));
    if (!best_class || !best_prob || !full || !samples || !scratch) {
        status = 20;
        goto cleanup;
    }
    if (csv_path != NULL && (csv = fopen(csv_path, "w")) != NULL)
        fputs("phase,index,freq_pre_hz,freq_post_hz,set_ms,run_ms,get_ms\n", csv);

    decode_target target = {.fp16 = fp16, .steps = steps, .classes = classes, .best_class = best_class,
                            .best_prob = best_prob, .full = NULL};
    if ((status = infer(ctx, canvases, line_bytes, &target, &samples[0])) != 0)
        goto cleanup;
    printf("cold freq_pre_mhz=%ld set_ms=%.3f run_ms=%.3f get_ms=%.3f\n", samples[0].freq_pre / 1000000,
           samples[0].set, samples[0].run, samples[0].get);
    write_csv(csv, "cold", samples, 1);
    for (long r = 0; r < warmup; ++r)
        if ((status = infer(ctx, canvases, line_bytes, &target, &samples[r])) != 0)
            goto cleanup;
    write_csv(csv, "warmup", samples, (size_t)warmup);
    summarize("warmup", samples, (size_t)warmup, scratch);
    for (size_t i = 0; i < lines; ++i) {
        if (interval_ms > 0)
            usleep((useconds_t)interval_ms * 1000);
        target.best_class = best_class + i * steps;
        target.best_prob = best_prob + i * steps;
        target.full = i < full_lines ? full + i * count : NULL;
        if ((status = infer(ctx, canvases + i * line_bytes, line_bytes, &target, &samples[i])) != 0) {
            fprintf(stderr, "inference=FAIL line=%zu status=%d\n", i, status);
            goto cleanup;
        }
    }
    write_csv(csv, "lines", samples, lines);
    summarize("lines", samples, lines, scratch);
    if (perf_path != NULL) {
        rknn_perf_run perf_run;
        rknn_perf_detail perf_detail;
        memset(&perf_run, 0, sizeof(perf_run));
        memset(&perf_detail, 0, sizeof(perf_detail));
        if (api.query(ctx, RKNN_QUERY_PERF_RUN, &perf_run, sizeof(perf_run)) == RKNN_SUCC)
            printf("perf_run last_us=%lld\n", (long long)perf_run.run_duration);
        if (api.query(ctx, RKNN_QUERY_PERF_DETAIL, &perf_detail, sizeof(perf_detail)) == RKNN_SUCC &&
            perf_detail.perf_data != NULL && (file = fopen(perf_path, "w")) != NULL) {
            fwrite(perf_detail.perf_data, 1, perf_detail.data_len, file);
            fclose(file);
            file = NULL;
            printf("perf_detail bytes=%llu\n", (unsigned long long)perf_detail.data_len);
        } else {
            fputs("perf_detail=UNAVAILABLE\n", stderr);
        }
    }
    if (result_path != NULL) {
        file = fopen(result_path, "wb");
        if (file == NULL || fwrite(best_class, sizeof(int32_t), lines * steps, file) != lines * steps ||
            fwrite(best_prob, sizeof(float), lines * steps, file) != lines * steps ||
            fwrite(full, sizeof(float), full_lines * count, file) != full_lines * count) {
            fputs("result_write=FAIL\n", stderr);
            status = 20;
            goto cleanup;
        }
        printf("result lines=%zu steps=%u classes=%u full_lines=%zu\n", lines, steps, classes, full_lines);
    }

cleanup:
    if (file != NULL && fclose(file) != 0 && status == 0)
        status = 20;
    if (csv != NULL && fclose(csv) != 0 && status == 0)
        status = 20;
    if (ctx_ready && api.destroy(ctx) != RKNN_SUCC && status == 0)
        status = 13;
    if (freq_fd >= 0)
        close(freq_fd);
    free(best_class);
    free(best_prob);
    free(full);
    free(samples);
    free(scratch);
    free(canvases);
    free(model);
    if (dlclose(runtime) != 0 && status == 0)
        status = 13;
    if (status == 0)
        puts("REC_FLOW_PASS");
    return status;
}

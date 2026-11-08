#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "rknn_api.h"

/*
 * Standalone OCR rec check. Input: packed uint8 NHWC BGR text-line canvases (N x 48x320x3).
 * Output file: per line and timestep the argmax class (int32) and its probability (float32),
 * followed by the full float32 output of the first FULL_LINES lines for VM-side comparison.
 */

#define FULL_LINES 8
#define MAX_WARM 200

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

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec / 1e6;
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

static void print_stats(const char *phase, const char *stage, double *values, size_t count) {
    qsort(values, count, sizeof(double), compare_double);
    double sum = 0;
    for (size_t i = 0; i < count; ++i)
        sum += values[i];
    printf("%s stage=%s n=%zu min_ms=%.3f median_ms=%.3f mean_ms=%.3f p90_ms=%.3f max_ms=%.3f\n", phase, stage, count,
           values[0], values[count / 2], sum / (double)count, values[count * 9 / 10], values[count - 1]);
}

/* One inference; copies the float output into scores (t_steps * classes). */
static int infer(rknn_context ctx, uint8_t *canvas, uint32_t bytes, float *scores, uint32_t count, double t[3]) {
    rknn_input input = {.index = 0, .buf = canvas, .size = bytes, .type = RKNN_TENSOR_UINT8, .fmt = RKNN_TENSOR_NHWC};
    double t0 = now_ms();
    if (api.inputs_set(ctx, 1, &input) < 0)
        return 23;
    double t1 = now_ms();
    if (api.run(ctx, NULL) < 0)
        return 23;
    double t2 = now_ms();
    rknn_output output = {.want_float = 1, .index = 0};
    if (api.outputs_get(ctx, 1, &output, NULL) < 0)
        return 24;
    int status = output.size == count * sizeof(float) ? 0 : 24;
    if (status == 0)
        memcpy(scores, output.buf, output.size);
    api.outputs_release(ctx, 1, &output);
    double t3 = now_ms();
    t[0] = t1 - t0;
    t[1] = t2 - t1;
    t[2] = t3 - t2;
    return status;
}

int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "Usage: %s /abs/librknnrt.so rec.rknn warm_runs canvases.u8 result.bin\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long warm = strtol(argv[3], &end, 10);
    if (argv[1][0] != '/' || *end != '\0' || warm < 1 || warm > MAX_WARM) {
        fprintf(stderr, "Expected an absolute runtime path and 1..%d warm runs.\n", MAX_WARM);
        return 2;
    }
    printf("identity uid=%lu gid=%lu\n", (unsigned long)getuid(), (unsigned long)getgid());
    void *runtime = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
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

    int status = 0;
    rknn_context ctx = 0;
    int ctx_ready = 0;
    size_t model_size = 0, canvas_size = 0;
    void *model = read_file(argv[2], &model_size);
    uint8_t *canvases = read_file(argv[4], &canvas_size);
    float *scores = NULL, *full = NULL;
    int32_t *best_class = NULL;
    float *best_prob = NULL;
    double *timing = NULL;
    FILE *result = NULL;
    if (model == NULL || canvases == NULL) {
        fputs("file_read=FAIL\n", stderr);
        status = 20;
        goto cleanup;
    }
    double t_init = now_ms();
    if (api.init(&ctx, model, (uint32_t)model_size, 0, NULL) < 0) {
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
        io.n_output != 1 || out_attr.n_dims != 3) {
        fputs("rknn_query=FAIL or unexpected I/O layout\n", stderr);
        status = 22;
        goto cleanup;
    }
    const uint32_t line_bytes = in_attr.n_elems, steps = out_attr.dims[1], classes = out_attr.dims[2];
    const size_t lines = canvas_size / line_bytes;
    printf("input dims=%ux%ux%ux%u fmt=%d type=%d; output dims=%ux%ux%u type=%d; lines=%zu\n", in_attr.dims[0],
           in_attr.dims[1], in_attr.dims[2], in_attr.dims[3], in_attr.fmt, in_attr.type, out_attr.dims[0], steps,
           classes, out_attr.type, lines);
    if (lines == 0 || canvas_size % line_bytes != 0) {
        fputs("canvas_size=UNEXPECTED\n", stderr);
        status = 22;
        goto cleanup;
    }
    const uint32_t count = steps * classes;
    const size_t full_lines = lines < FULL_LINES ? lines : FULL_LINES;
    scores = malloc(count * sizeof(float));
    full = malloc(full_lines * count * sizeof(float));
    best_class = malloc(lines * steps * sizeof(int32_t));
    best_prob = malloc(lines * steps * sizeof(float));
    const size_t stride = lines > (size_t)warm ? lines : (size_t)warm;
    timing = malloc(stride * 4 * sizeof(double));
    if (!scores || !full || !best_class || !best_prob || !timing) {
        status = 20;
        goto cleanup;
    }
    double *set = timing, *run = timing + stride, *get = timing + 2 * stride, *total = timing + 3 * stride;
    for (size_t i = 0; i < lines; ++i) {
        double t[3];
        if ((status = infer(ctx, canvases + i * line_bytes, line_bytes, scores, count, t)) != 0) {
            fprintf(stderr, "inference=FAIL line=%zu status=%d\n", i, status);
            goto cleanup;
        }
        if (i == 0)
            printf("first cold=1 set_ms=%.3f run_ms=%.3f get_ms=%.3f\n", t[0], t[1], t[2]);
        double t_decode = now_ms();
        for (uint32_t s = 0; s < steps; ++s) {
            const float *row = scores + (size_t)s * classes;
            uint32_t arg = 0;
            for (uint32_t c = 1; c < classes; ++c)
                if (row[c] > row[arg])
                    arg = c;
            best_class[i * steps + s] = (int32_t)arg;
            best_prob[i * steps + s] = row[arg];
        }
        set[i] = t[0];
        run[i] = t[1];
        get[i] = t[2] + (now_ms() - t_decode);
        total[i] = set[i] + run[i] + get[i];
        if (i < full_lines)
            memcpy(full + i * count, scores, count * sizeof(float));
    }
    print_stats("lines", "inputs_set", set, lines);
    print_stats("lines", "run", run, lines);
    print_stats("lines", "outputs_get+argmax", get, lines);
    print_stats("lines", "total", total, lines);
    for (long r = 0; r < warm; ++r) {
        double t[3];
        if ((status = infer(ctx, canvases, line_bytes, scores, count, t)) != 0)
            goto cleanup;
        set[r] = t[0];
        run[r] = t[1];
        get[r] = t[2];
        total[r] = t[0] + t[1] + t[2];
    }
    print_stats("warm_line0", "run", run, (size_t)warm);
    print_stats("warm_line0", "total", total, (size_t)warm);
    result = fopen(argv[5], "wb");
    if (result == NULL || fwrite(best_class, sizeof(int32_t), lines * steps, result) != lines * steps ||
        fwrite(best_prob, sizeof(float), lines * steps, result) != lines * steps ||
        fwrite(full, sizeof(float), full_lines * count, result) != full_lines * count) {
        fputs("result_write=FAIL\n", stderr);
        status = 20;
        goto cleanup;
    }
    printf("result lines=%zu steps=%u classes=%u full_lines=%zu\n", lines, steps, classes, full_lines);

cleanup:
    if (result != NULL && fclose(result) != 0 && status == 0)
        status = 20;
    if (ctx_ready && api.destroy(ctx) != RKNN_SUCC && status == 0)
        status = 13;
    free(scores);
    free(full);
    free(best_class);
    free(best_prob);
    free(timing);
    free(canvases);
    free(model);
    if (dlclose(runtime) != 0 && status == 0)
        status = 13;
    if (status == 0)
        puts("REC_FLOW_PASS");
    return status;
}

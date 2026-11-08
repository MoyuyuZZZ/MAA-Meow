#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "rknn_api.h"

/* Standalone single-model check: official MobileNet V1, pre-decoded NHWC uint8 RGB inputs. */

#define INPUT_BYTES (224u * 224u * 3u)
#define CLASS_COUNT 1001u
#define MAX_INPUTS 4
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

typedef struct {
    double set, run, get;
} stage_ms;

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e3 + (double)ts.tv_nsec / 1e6;
}

static const char *base_name(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash != NULL ? slash + 1 : path;
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

static void print_attr(const char *kind, const rknn_tensor_attr *attr) {
    printf("%s%u name=%s n_dims=%u dims=", kind, attr->index, attr->name, attr->n_dims);
    for (uint32_t i = 0; i < attr->n_dims && i < RKNN_MAX_DIMS; ++i)
        printf(i == 0 ? "%u" : "x%u", attr->dims[i]);
    printf(" n_elems=%u size=%u fmt=%d type=%d qnt_type=%d zp=%d scale=%f\n", attr->n_elems, attr->size,
           attr->fmt, attr->type, attr->qnt_type, attr->zp, attr->scale);
}

static int infer(rknn_context ctx, uint8_t *rgb, float *scores, stage_ms *time) {
    rknn_input input = {.index = 0, .buf = rgb, .size = INPUT_BYTES, .type = RKNN_TENSOR_UINT8,
                        .fmt = RKNN_TENSOR_NHWC};
    double t0 = now_ms();
    int ret = api.inputs_set(ctx, 1, &input);
    double t1 = now_ms();
    if (ret < 0) {
        fprintf(stderr, "rknn_inputs_set=FAIL ret=%d\n", ret);
        return 23;
    }
    ret = api.run(ctx, NULL);
    double t2 = now_ms();
    if (ret < 0) {
        fprintf(stderr, "rknn_run=FAIL ret=%d\n", ret);
        return 23;
    }
    rknn_output output = {.want_float = 1, .index = 0};
    ret = api.outputs_get(ctx, 1, &output, NULL);
    if (ret < 0) {
        fprintf(stderr, "rknn_outputs_get=FAIL ret=%d\n", ret);
        return 24;
    }
    int status = 0;
    if (output.size != CLASS_COUNT * sizeof(float)) {
        fprintf(stderr, "rknn_outputs_get=FAIL unexpected_size=%u\n", output.size);
        status = 24;
    } else {
        memcpy(scores, output.buf, output.size);
    }
    api.outputs_release(ctx, 1, &output);
    double t3 = now_ms();
    time->set = t1 - t0;
    time->run = t2 - t1;
    time->get = t3 - t2;
    return status;
}

static void top5(const float *scores, uint32_t best[5]) {
    for (int k = 0; k < 5; ++k) {
        best[k] = UINT32_MAX;
        for (uint32_t i = 0; i < CLASS_COUNT; ++i) {
            int used = 0;
            for (int j = 0; j < k; ++j)
                used |= best[j] == i;
            if (!used && (best[k] == UINT32_MAX || scores[i] > scores[best[k]]))
                best[k] = i;
        }
    }
}

static int compare_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void print_stats(const char *input, const char *stage, double *values, int count) {
    qsort(values, (size_t)count, sizeof(double), compare_double);
    double sum = 0;
    for (int i = 0; i < count; ++i)
        sum += values[i];
    printf("warm input=%s stage=%s runs=%d min_ms=%.3f median_ms=%.3f mean_ms=%.3f max_ms=%.3f\n", input, stage,
           count, values[0], values[count / 2], sum / count, values[count - 1]);
}

int main(int argc, char **argv) {
    if (argc < 5 || argc > 4 + MAX_INPUTS) {
        fprintf(stderr, "Usage: %s /abs/librknnrt.so model.rknn warm_runs input.rgb [input.rgb...]\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    long warm = strtol(argv[3], &end, 10);
    if (argv[1][0] != '/' || *end != '\0' || warm < 1 || warm > MAX_WARM) {
        fprintf(stderr, "Expected an absolute runtime path and 1..%d warm runs.\n", MAX_WARM);
        return 2;
    }
    printf("identity uid=%lu gid=%lu\n", (unsigned long)getuid(), (unsigned long)getgid());
    FILE *version = fopen("/sys/module/rknpu/version", "r");
    if (version != NULL) {
        char buffer[128];
        if (fgets(buffer, sizeof(buffer), version) != NULL)
            printf("sysfs_driver_version=%s", buffer);
        fclose(version);
    }

    double t_load = now_ms();
    void *runtime = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (runtime == NULL) {
        fprintf(stderr, "runtime_load=FAIL error=%s\n", dlerror());
        return 10;
    }
    printf("runtime_load=PASS ms=%.3f\n", now_ms() - t_load);
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
        *symbols[i].slot = dlsym(runtime, symbols[i].name);
        if (*symbols[i].slot == NULL) {
            fprintf(stderr, "runtime_symbol=%s FAIL\n", symbols[i].name);
            dlclose(runtime);
            return 11;
        }
    }

    int status = 0;
    size_t model_size = 0;
    void *model = read_file(argv[2], &model_size);
    int input_count = argc - 4;
    uint8_t *inputs[MAX_INPUTS] = {0};
    for (int i = 0; i < input_count && model != NULL; ++i) {
        size_t size = 0;
        inputs[i] = read_file(argv[4 + i], &size);
        if (inputs[i] == NULL || size != INPUT_BYTES) {
            fprintf(stderr, "input_read=FAIL file=%s bytes=%zu\n", argv[4 + i], size);
            status = 20;
        }
    }
    if (model == NULL) {
        fprintf(stderr, "model_read=FAIL errno=%d\n", errno);
        status = 20;
    }
    float *first = calloc(CLASS_COUNT, sizeof(float));
    float *scores = calloc(CLASS_COUNT, sizeof(float));
    if (status == 0 && (first == NULL || scores == NULL))
        status = 20;
    if (status != 0)
        goto unload;
    printf("model_read=PASS bytes=%zu\n", model_size);

    rknn_context ctx = 0;
    double t_init = now_ms();
    int ret = api.init(&ctx, model, (uint32_t)model_size, 0, NULL);
    double init_ms = now_ms() - t_init;
    if (ret < 0) {
        fprintf(stderr, "rknn_init=FAIL ret=%d ms=%.3f\n", ret, init_ms);
        status = 21;
        goto unload;
    }
    printf("rknn_init=PASS ms=%.3f\n", init_ms);

    rknn_sdk_version sdk;
    memset(&sdk, 0, sizeof(sdk));
    if (api.query(ctx, RKNN_QUERY_SDK_VERSION, &sdk, sizeof(sdk)) == RKNN_SUCC)
        printf("sdk api_version=%s drv_version=%s\n", sdk.api_version, sdk.drv_version);
    rknn_input_output_num io;
    rknn_tensor_attr in_attr, out_attr;
    memset(&io, 0, sizeof(io));
    memset(&in_attr, 0, sizeof(in_attr));
    memset(&out_attr, 0, sizeof(out_attr));
    if (api.query(ctx, RKNN_QUERY_IN_OUT_NUM, &io, sizeof(io)) != RKNN_SUCC ||
        api.query(ctx, RKNN_QUERY_INPUT_ATTR, &in_attr, sizeof(in_attr)) != RKNN_SUCC ||
        api.query(ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr, sizeof(out_attr)) != RKNN_SUCC) {
        fputs("rknn_query=FAIL\n", stderr);
        status = 22;
        goto destroy;
    }
    printf("io n_input=%u n_output=%u\n", io.n_input, io.n_output);
    print_attr("input", &in_attr);
    print_attr("output", &out_attr);
    if (io.n_input != 1 || io.n_output != 1 || in_attr.n_elems != INPUT_BYTES || out_attr.n_elems != CLASS_COUNT) {
        fputs("model_shape=UNEXPECTED\n", stderr);
        status = 22;
        goto destroy;
    }

    for (int i = 0; i < input_count && status == 0; ++i) {
        const char *name = base_name(argv[4 + i]);
        stage_ms t;
        if ((status = infer(ctx, inputs[i], first, &t)) != 0)
            break;
        printf("first input=%s cold=%d set_ms=%.3f run_ms=%.3f get_ms=%.3f total_ms=%.3f\n", name, i == 0, t.set,
               t.run, t.get, t.set + t.run + t.get);
        uint32_t best[5];
        top5(first, best);
        for (int k = 0; k < 5; ++k)
            printf("top5 input=%s rank=%d class=%u score=%.6f\n", name, k + 1, best[k], first[best[k]]);
        double set[MAX_WARM], run[MAX_WARM], get[MAX_WARM], total[MAX_WARM];
        float max_diff = 0;
        int top1_changes = 0;
        for (long r = 0; r < warm; ++r) {
            if ((status = infer(ctx, inputs[i], scores, &t)) != 0)
                break;
            set[r] = t.set;
            run[r] = t.run;
            get[r] = t.get;
            total[r] = t.set + t.run + t.get;
            uint32_t now_best[5];
            top5(scores, now_best);
            top1_changes += now_best[0] != best[0];
            for (uint32_t c = 0; c < CLASS_COUNT; ++c)
                max_diff = fmaxf(max_diff, fabsf(scores[c] - first[c]));
        }
        if (status != 0)
            break;
        print_stats(name, "inputs_set", set, (int)warm);
        print_stats(name, "run", run, (int)warm);
        print_stats(name, "outputs_get", get, (int)warm);
        print_stats(name, "total", total, (int)warm);
        printf("consistency input=%s max_abs_diff=%g top1_changes=%d\n", name, max_diff, top1_changes);
        if (top1_changes != 0)
            status = 25;
    }

destroy:;
    double t_destroy = now_ms();
    if (api.destroy(ctx) != RKNN_SUCC && status == 0)
        status = 13;
    printf("rknn_destroy ms=%.3f\n", now_ms() - t_destroy);
unload:
    free(first);
    free(scores);
    for (int i = 0; i < MAX_INPUTS; ++i)
        free(inputs[i]);
    free(model);
    if (dlclose(runtime) != 0 && status == 0)
        status = 13;
    if (status == 0) {
        puts("INFERENCE_FLOW_PASS");
        puts("Top-5 correctness is judged by the VM-side checker against the pinned expectations.");
    }
    return status;
}

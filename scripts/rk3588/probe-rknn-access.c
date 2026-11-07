#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* Standalone loader/access check. Never calls RKNN APIs or submits NPU work. */
int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s /absolute/path/librknnrt.so /dev/dri/renderDxxx\n", argv[0]);
        return 2;
    }
    if (argv[1][0] != '/' || strncmp(argv[2], "/dev/dri/", 9) != 0) {
        fprintf(stderr, "Expected an absolute runtime path and a DRM device path.\n");
        return 2;
    }
    printf("identity uid=%lu gid=%lu\n", (unsigned long)getuid(), (unsigned long)getgid());
    FILE *version = fopen("/sys/module/rknpu/version", "r");
    if (version != NULL) {
        char buffer[128];
        if (fgets(buffer, sizeof(buffer), version) != NULL)
            printf("driver_version=%s", buffer);
        fclose(version);
    } else {
        printf("driver_version=unreadable errno=%d\n", errno);
    }
    void *runtime = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (runtime == NULL) {
        fprintf(stderr, "runtime_load=FAIL error=%s\n", dlerror());
        return 10;
    }
    puts("runtime_load=PASS");
    const char *symbols[] = {"rknn_init", "rknn_query", "rknn_run", "rknn_destroy"};
    for (size_t i = 0; i < sizeof(symbols) / sizeof(symbols[0]); ++i) {
        dlerror();
        void *symbol = dlsym(runtime, symbols[i]);
        const char *error = dlerror();
        if (error != NULL || symbol == NULL) {
            fprintf(stderr, "runtime_symbol=%s FAIL\n", symbols[i]);
            dlclose(runtime);
            return 11;
        }
        printf("runtime_symbol=%s PASS\n", symbols[i]);
    }
    int descriptor = open(argv[2], O_RDWR | O_CLOEXEC);
    if (descriptor < 0) {
        fprintf(stderr, "device_open=FAIL errno=%d error=%s\n", errno, strerror(errno));
        dlclose(runtime);
        return 12;
    }
    puts("device_open=PASS");
    int closed = close(descriptor);
    int unloaded = dlclose(runtime);
    if (closed != 0 || unloaded != 0) {
        fputs("resource_cleanup=FAIL\n", stderr);
        return 13;
    }
    puts("ACCESS_AND_LOADER_PASS");
    puts("No model loaded; no RKNN function or device ioctl called by this probe.");
    puts("Inference, driver/runtime compatibility and app access remain unverified.");
    return 0;
}

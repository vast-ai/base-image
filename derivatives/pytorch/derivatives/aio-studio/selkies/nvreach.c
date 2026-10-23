/*
 * nvreach: report an unopenable /dev/nvidiaN as absent to access(), for the Selkies
 * process only (LD_PRELOAD). See docs/adr/0050-desktop-streaming-moves-to-selkies-2.md.
 *
 * pixelflux's multi-GPU NVENC filter treats a GPU as reachable when its /dev/nvidiaN
 * exists. On a subset-GPU rental every host GPU's node exists and the device cgroup
 * refuses the unallocated ones (open() fails with EPERM), so the filter never installs
 * and NVENC fails. This answers access() the way an open() would. The upstream fix is
 * merged (https://github.com/selkies-project/pixelflux/pull/44, commit 7f8369e); remove this
 * once the pinned Selkies .deb bundles a pixelflux release that contains that commit.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#ifndef NVREACH_PREFIX
#define NVREACH_PREFIX "/dev/nvidia"
#endif

static int is_gpu_node(const char *path)
{
    static const char prefix[] = NVREACH_PREFIX;
    const char *p;

    if (!path || strncmp(path, prefix, sizeof(prefix) - 1) != 0)
        return 0;
    p = path + sizeof(prefix) - 1;
    if (!*p)
        return 0;
    for (; *p; p++)
        if (*p < '0' || *p > '9')
            return 0;
    return 1;
}

int access(const char *path, int mode)
{
    static int (*real_access)(const char *, int);
    int fd;

    if (!real_access)
        real_access = (int (*)(const char *, int))dlsym(RTLD_NEXT, "access");
    if (mode != F_OK || !is_gpu_node(path))
        return real_access(path, mode);
    fd = open(path, O_RDWR | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) {
        errno = ENOENT;
        return -1;
    }
    close(fd);
    return 0;
}

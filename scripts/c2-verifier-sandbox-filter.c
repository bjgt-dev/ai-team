/* E4 verifier sandbox write filter (LD_PRELOAD).
 * Kernel-level isolation on this host: netns only (mount() is EPERM for
 * unprivileged userns, proven 2026-11-10). This filter is the write-
 * isolation layer: libc-level open-family calls outside the writable
 * workdir are denied with EROFS. Reads are allowed everywhere.
 * Known limit (disclosed, not hidden): raw syscalls (ctypes syscall(),
 * static binaries, or a child that drops LD_PRELOAD) bypass this layer.
 * Backstop: the verifier diff-checks deliverables/forbidden paths after
 * the run and rejects on any modification. No network: the netns blocks
 * connect() at kernel level regardless of this filter.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <utime.h>
#include <unistd.h>

static char g_writable[PATH_MAX];
static size_t g_wlen;

static int (*real_open)(const char *, int, ...);
static int (*real_open64)(const char *, int, ...);
static int (*real_openat)(int, const char *, int, ...);
static int (*real_openat64)(int, const char *, int, ...);
static int (*real_creat)(const char *, mode_t);
static int (*real_creat64)(const char *, mode_t);
static int (*real_unlink)(const char *);
static int (*real_unlinkat)(int, const char *, int);
static int (*real_mkdir)(const char *, mode_t);
static int (*real_mkdirat)(int, const char *, mode_t);
static int (*real_rmdir)(const char *);
static int (*real_rename)(const char *, const char *);
static int (*real_renameat)(int, const char *, int, const char *);
static int (*real_symlink)(const char *, const char *);
static int (*real_symlinkat)(const char *, int, const char *);
static int (*real_link)(const char *, const char *);
static int (*real_linkat)(int, const char *, int, const char *, int);
static int (*real_truncate)(const char *, off_t);
static int (*real_truncate64)(const char *, off_t);
static int (*real_chmod)(const char *, mode_t);
static int (*real_fchmodat)(int, const char *, mode_t, int);
static int (*real_chown)(const char *, uid_t, gid_t);
static int (*real_lchown)(const char *, uid_t, gid_t);
static int (*real_fchownat)(int, const char *, uid_t, gid_t, int);
static int (*real_utime)(const char *, const struct utimbuf *);
static int (*real_utimensat)(int, const char *, const struct timespec *, int);
static int (*real_mkfifo)(const char *, mode_t);
static int (*real_mkfifoat)(int, const char *, mode_t);
static int (*real_mknod)(const char *, mode_t, dev_t);
static int (*real_mknodat)(int, const char *, mode_t, dev_t);
static int (*real_open_2)(const char *, int);
static int (*real_openat_2)(int, const char *, int);

__attribute__((constructor)) static void sbx_init(void) {
    const char *w = getenv("SANDBOX_WRITABLE");
    g_writable[0] = 0; g_wlen = 0;
    if (w && realpath(w, g_writable)) g_wlen = strlen(g_writable);
    real_open = dlsym(RTLD_NEXT, "open");
    real_open64 = dlsym(RTLD_NEXT, "open64");
    real_openat = dlsym(RTLD_NEXT, "openat");
    real_openat64 = dlsym(RTLD_NEXT, "openat64");
    real_creat = dlsym(RTLD_NEXT, "creat");
    real_creat64 = dlsym(RTLD_NEXT, "creat64");
    real_unlink = dlsym(RTLD_NEXT, "unlink");
    real_unlinkat = dlsym(RTLD_NEXT, "unlinkat");
    real_mkdir = dlsym(RTLD_NEXT, "mkdir");
    real_mkdirat = dlsym(RTLD_NEXT, "mkdirat");
    real_rmdir = dlsym(RTLD_NEXT, "rmdir");
    real_rename = dlsym(RTLD_NEXT, "rename");
    real_renameat = dlsym(RTLD_NEXT, "renameat");
    real_symlink = dlsym(RTLD_NEXT, "symlink");
    real_symlinkat = dlsym(RTLD_NEXT, "symlinkat");
    real_link = dlsym(RTLD_NEXT, "link");
    real_linkat = dlsym(RTLD_NEXT, "linkat");
    real_truncate = dlsym(RTLD_NEXT, "truncate");
    real_truncate64 = dlsym(RTLD_NEXT, "truncate64");
    real_chmod = dlsym(RTLD_NEXT, "chmod");
    real_fchmodat = dlsym(RTLD_NEXT, "fchmodat");
    real_chown = dlsym(RTLD_NEXT, "chown");
    real_lchown = dlsym(RTLD_NEXT, "lchown");
    real_fchownat = dlsym(RTLD_NEXT, "fchownat");
    real_utime = dlsym(RTLD_NEXT, "utime");
    real_utimensat = dlsym(RTLD_NEXT, "utimensat");
    real_mkfifo = dlsym(RTLD_NEXT, "mkfifo");
    real_mkfifoat = dlsym(RTLD_NEXT, "mkfifoat");
    real_mknod = dlsym(RTLD_NEXT, "mknod");
    real_mknodat = dlsym(RTLD_NEXT, "mknodat");
    real_open_2 = dlsym(RTLD_NEXT, "__open_2");
    real_openat_2 = dlsym(RTLD_NEXT, "__openat_2");
}

/* Resolve path (possibly relative, possibly via dirfd) to a canonical
 * absolute path. For a not-yet-existing leaf, resolve the parent. */
static void sbx_resolve(const char *path, int dirfd, char *out, size_t outsz) {
    char tmp[PATH_MAX];
    if (path[0] == '/') {
        snprintf(tmp, sizeof(tmp), "%s", path);
    } else if (dirfd != AT_FDCWD) {
        char fdpath[64], base[PATH_MAX];
        ssize_t n;
        snprintf(fdpath, sizeof(fdpath), "/proc/self/fd/%d", dirfd);
        n = readlink(fdpath, base, sizeof(base) - 1);
        if (n > 0) { base[n] = 0; snprintf(tmp, sizeof(tmp), "%s/%s", base, path); }
        else if (getcwd(tmp, sizeof(tmp))) { strncat(tmp, "/", sizeof(tmp)-strlen(tmp)-1); strncat(tmp, path, sizeof(tmp)-strlen(tmp)-1); }
        else snprintf(tmp, sizeof(tmp), "%s", path);
    } else {
        if (getcwd(tmp, sizeof(tmp))) { strncat(tmp, "/", sizeof(tmp)-strlen(tmp)-1); strncat(tmp, path, sizeof(tmp)-strlen(tmp)-1); }
        else snprintf(tmp, sizeof(tmp), "%s", path);
    }
    if (realpath(tmp, out)) return;
    /* leaf does not exist: resolve parent, append basename */
    char *slash = strrchr(tmp, '/');
    char parent[PATH_MAX], base[PATH_MAX];
    if (!slash || slash == tmp) { snprintf(parent, sizeof(parent), "/"); snprintf(base, sizeof(base), "%s", slash ? slash + 1 : tmp); }
    else { snprintf(parent, sizeof(parent), "%.*s", (int)(slash - tmp), tmp); snprintf(base, sizeof(base), "%s", slash + 1); }
    char rp[PATH_MAX];
    if (realpath(parent, rp)) snprintf(out, outsz, "%s/%s", rp, base);
    else snprintf(out, outsz, "%s", tmp);
}

static bool sbx_write_allowed(const char *canon) {
    if (g_wlen == 0) return false; /* fail closed when unset */
    if (strncmp(canon, g_writable, g_wlen) == 0 &&
        (canon[g_wlen] == 0 || canon[g_wlen] == '/')) return true;
    if (strncmp(canon, "/dev/shm/", 9) == 0) return true;
    if (strcmp(canon, "/dev/null") == 0 || strcmp(canon, "/dev/zero") == 0 ||
        strcmp(canon, "/dev/full") == 0 || strcmp(canon, "/dev/stdout") == 0 ||
        strcmp(canon, "/dev/stderr") == 0 || strcmp(canon, "/dev/tty") == 0) return true;
    if (strncmp(canon, "/dev/pts/", 9) == 0) return true;
    if (strncmp(canon, "/proc/self/fd/", 14) == 0) return true;
    if (strncmp(canon, "/dev/fd/", 8) == 0) return true;
    return false;
}

static int sbx_deny(const char *op, const char *canon) {
    fprintf(stderr, "SANDBOX_DENY %s %s (read-only outside workdir)\n", op, canon);
    errno = EROFS;
    return -1;
}

static int sbx_check(const char *op, const char *path, int dirfd) {
    char canon[PATH_MAX];
    sbx_resolve(path, dirfd, canon, sizeof(canon));
    if (sbx_write_allowed(canon)) return 0;
    return sbx_deny(op, canon);
}

int open(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("open", path, AT_FDCWD)) return -1;
    return real_open(path, flags, mode);
}
int open64(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("open64", path, AT_FDCWD)) return -1;
    if (!real_open64) return real_open(path, flags, mode);
    return real_open64(path, flags, mode);
}
int __open_2(const char *path, int flags) {
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("__open_2", path, AT_FDCWD)) return -1;
    if (!real_open_2) return real_open(path, flags, 0);
    return real_open_2(path, flags);
}
int openat(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("openat", path, dirfd)) return -1;
    return real_openat(dirfd, path, flags, mode);
}
int openat64(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("openat64", path, dirfd)) return -1;
    if (!real_openat64) return real_openat(dirfd, path, flags, mode);
    return real_openat64(dirfd, path, flags, mode);
}
int __openat_2(int dirfd, const char *path, int flags) {
    if ((flags & O_ACCMODE) != O_RDONLY || (flags & (O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE)))
        if (sbx_check("__openat_2", path, dirfd)) return -1;
    if (!real_openat_2) return real_openat(dirfd, path, flags, 0);
    return real_openat_2(dirfd, path, flags);
}
int creat(const char *path, mode_t mode) {
    if (sbx_check("creat", path, AT_FDCWD)) return -1;
    return real_creat(path, mode);
}
int creat64(const char *path, mode_t mode) {
    if (sbx_check("creat64", path, AT_FDCWD)) return -1;
    if (!real_creat64) return real_creat(path, mode);
    return real_creat64(path, mode);
}
int unlink(const char *path) {
    if (sbx_check("unlink", path, AT_FDCWD)) return -1;
    return real_unlink(path);
}
int unlinkat(int dirfd, const char *path, int flags) {
    (void)flags;
    if (sbx_check("unlinkat", path, dirfd)) return -1;
    return real_unlinkat(dirfd, path, flags);
}
int mkdir(const char *path, mode_t mode) {
    if (sbx_check("mkdir", path, AT_FDCWD)) return -1;
    return real_mkdir(path, mode);
}
int mkdirat(int dirfd, const char *path, mode_t mode) {
    if (sbx_check("mkdirat", path, dirfd)) return -1;
    return real_mkdirat(dirfd, path, mode);
}
int rmdir(const char *path) {
    if (sbx_check("rmdir", path, AT_FDCWD)) return -1;
    return real_rmdir(path);
}
int rename(const char *oldp, const char *newp) {
    if (sbx_check("rename-old", oldp, AT_FDCWD)) return -1;
    if (sbx_check("rename-new", newp, AT_FDCWD)) return -1;
    return real_rename(oldp, newp);
}
int renameat(int oldd, const char *oldp, int newd, const char *newp) {
    if (sbx_check("renameat-old", oldp, oldd)) return -1;
    if (sbx_check("renameat-new", newp, newd)) return -1;
    return real_renameat(oldd, oldp, newd, newp);
}
int symlink(const char *target, const char *linkpath) {
    (void)target;
    if (sbx_check("symlink", linkpath, AT_FDCWD)) return -1;
    return real_symlink(target, linkpath);
}
int symlinkat(const char *target, int dirfd, const char *linkpath) {
    (void)target;
    if (sbx_check("symlinkat", linkpath, dirfd)) return -1;
    return real_symlinkat(target, dirfd, linkpath);
}
int link(const char *oldp, const char *newp) {
    (void)oldp;
    if (sbx_check("link", newp, AT_FDCWD)) return -1;
    return real_link(oldp, newp);
}
int linkat(int oldd, const char *oldp, int newd, const char *newp, int flags) {
    (void)oldd; (void)oldp;
    if (sbx_check("linkat", newp, newd)) return -1;
    return real_linkat(oldd, oldp, newd, newp, flags);
}
int truncate(const char *path, off_t len) {
    if (sbx_check("truncate", path, AT_FDCWD)) return -1;
    return real_truncate(path, len);
}
int truncate64(const char *path, off_t len) {
    if (sbx_check("truncate64", path, AT_FDCWD)) return -1;
    if (!real_truncate64) return real_truncate(path, len);
    return real_truncate64(path, len);
}
int chmod(const char *path, mode_t mode) {
    if (sbx_check("chmod", path, AT_FDCWD)) return -1;
    return real_chmod(path, mode);
}
int fchmodat(int dirfd, const char *path, mode_t mode, int flags) {
    if (sbx_check("fchmodat", path, dirfd)) return -1;
    return real_fchmodat(dirfd, path, mode, flags);
}
int chown(const char *path, uid_t o, gid_t g) {
    if (sbx_check("chown", path, AT_FDCWD)) return -1;
    return real_chown(path, o, g);
}
int lchown(const char *path, uid_t o, gid_t g) {
    if (sbx_check("lchown", path, AT_FDCWD)) return -1;
    return real_lchown(path, o, g);
}
int fchownat(int dirfd, const char *path, uid_t o, gid_t g, int flags) {
    if (sbx_check("fchownat", path, dirfd)) return -1;
    return real_fchownat(dirfd, path, o, g, flags);
}
int utime(const char *path, const struct utimbuf *t) {
    if (sbx_check("utime", path, AT_FDCWD)) return -1;
    return real_utime(path, t);
}
int utimensat(int dirfd, const char *path, const struct timespec *t, int flags) {
    if (path && sbx_check("utimensat", path, dirfd)) return -1;
    return real_utimensat(dirfd, path, t, flags);
}
int mkfifo(const char *path, mode_t mode) {
    if (sbx_check("mkfifo", path, AT_FDCWD)) return -1;
    return real_mkfifo(path, mode);
}
int mkfifoat(int dirfd, const char *path, mode_t mode) {
    if (sbx_check("mkfifoat", path, dirfd)) return -1;
    return real_mkfifoat(dirfd, path, mode);
}
int mknod(const char *path, mode_t mode, dev_t dev) {
    if (sbx_check("mknod", path, AT_FDCWD)) return -1;
    return real_mknod(path, mode, dev);
}
int mknodat(int dirfd, const char *path, mode_t mode, dev_t dev) {
    if (sbx_check("mknodat", path, dirfd)) return -1;
    return real_mknodat(dirfd, path, mode, dev);
}

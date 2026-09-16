/* Native SIGSEGV handler standing in for a third-party crash handler
 * (torch / abseil / gRPC style): installs via sigaction with SA_SIGINFO,
 * saves whatever was there, writes a marker, then chains to the previous
 * handler so the process still dies. Loaded from Python via ctypes.
 *
 * Build: gcc -shared -fPIC -O0 -o libforeign.so foreign_handler.c
 */

#define _GNU_SOURCE
#include <signal.h>
#include <string.h>
#include <unistd.h>

static struct sigaction prev_segv;
static volatile sig_atomic_t ran = 0;

static void
foreign_handler(int signo, siginfo_t* info, void* ctx)
{
    ran = 1;
    static const char msg[] = "FOREIGN_NATIVE_HANDLER_RAN\n";
    (void)write(2, msg, sizeof(msg) - 1);

    /* Chain to whatever was installed before us, the way real crash handlers do. */
    if (prev_segv.sa_flags & SA_SIGINFO) {
        if (prev_segv.sa_sigaction != NULL) {
            prev_segv.sa_sigaction(signo, info, ctx);
            return;
        }
    } else if (prev_segv.sa_handler == SIG_DFL) {
        struct sigaction dfl;
        memset(&dfl, 0, sizeof(dfl));
        dfl.sa_handler = SIG_DFL;
        sigemptyset(&dfl.sa_mask);
        sigaction(signo, &dfl, NULL);
        return; /* let the faulting instruction re-execute into SIG_DFL */
    } else if (prev_segv.sa_handler != SIG_IGN && prev_segv.sa_handler != NULL) {
        prev_segv.sa_handler(signo);
        return;
    }

    /* Nothing sane to chain to: fall back to default disposition. */
    struct sigaction dfl;
    memset(&dfl, 0, sizeof(dfl));
    dfl.sa_handler = SIG_DFL;
    sigemptyset(&dfl.sa_mask);
    sigaction(signo, &dfl, NULL);
}

int
install_foreign_handler(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = foreign_handler;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&sa.sa_mask);
    return sigaction(SIGSEGV, &sa, &prev_segv);
}

/* Non-zero if the previous disposition was a real handler (i.e. we chained onto
 * something, such as ddtrace's), 0 if it was SIG_DFL/SIG_IGN. */
int
prev_was_handler(void)
{
    if (prev_segv.sa_flags & SA_SIGINFO)
        return prev_segv.sa_sigaction != NULL ? 1 : 0;
    return (prev_segv.sa_handler != SIG_DFL && prev_segv.sa_handler != SIG_IGN &&
            prev_segv.sa_handler != NULL)
             ? 1
             : 0;
}

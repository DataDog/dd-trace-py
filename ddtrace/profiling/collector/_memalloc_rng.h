#ifndef _MEMALLOC_RNG_H
#define _MEMALLOC_RNG_H

/* Fork-safe fast PRNG using xorshift* algorithm.
 *
 * Lock-free (no mutexes, avoids rand() deadlock risk) and fork-safe via pthread_atfork.
 * Similar to ddtrace/internal/_rand.pyx but optimized for C++.
 *
 * This RNG provides:
 * - Lock-free operation (no deadlock risk from inherited mutexes after fork)
 * - Fork-safety (automatically reinitializes in child processes)
 * - Zero syscall overhead on hot path (no getpid() calls per random number)
 * - Fast performance (comparable to rand() using bit operations only)
 */

/* Generate a uniform random number in [0, 1).
 * Thread-safe and fork-safe. */
double
memalloc_rng_uniform(void);

/* Reinitialize the RNG state, for example after fork */
void
memalloc_rng_reseed(void);

#endif /* _MEMALLOC_RNG_H */

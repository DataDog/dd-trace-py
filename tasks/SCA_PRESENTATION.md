## Prompt: Presentation Preparation Questions for `ddtrace/appsec/sca`

Based on the work you have already completed in the **“tasks”** phase, and considering **everything that has been designed and implemented under**:

```
ddtrace/appsec/sca/
```

we want to prepare a technical presentation explaining the design, trade-offs, and runtime behavior of the SCA runtime instrumentation system.

Using that context, please address the following questions.
These questions are intended to be answered at a **design and architectural level**, suitable for a technical audience (e.g., AppSec, Runtime, or Python engineers), and should help justify the decisions taken.

### Questions to Address

1. **Application Startup Impact**

   * Does the introduction of the SCA runtime instrumentation system have any impact on the application startup time?
   * Explain *why* it does or does not, considering aspects such as lazy initialization, feature gating, and when instrumentation is actually applied.

2. **Why Bytecode Instrumentation (Debugger-style)?**

   * Why was bytecode / code-object patching chosen as the primary instrumentation mechanism, similarly to what is done in the Debugger?
   * What properties of bytecode instrumentation make it suitable for this use case?

3. **Why Bytecode Instead of AST Patching (as used in IAST)?**

   * Why is bytecode instrumentation preferred over AST patching in the context of SCA?
   * What are the fundamental differences between these two approaches in terms of:

     * Timing (import-time vs runtime)
     * Dynamism
     * Interaction with Remote Configuration
     * Ability to modify already-running applications

4. **Support for Asynchronous Functions**

   * Does bytecode instrumentation work correctly for `async def` functions?
   * How does instrumentation interact with coroutine creation and asynchronous execution semantics?

5. **End-to-End Runtime Flow**

   * Can you explain the full runtime flow of the system from start to finish?
   * In particular:

     * How a list of functions to instrument arrives via Remote Configuration
     * How the Remote Config worker operates in its own thread
     * How and when symbol resolution occurs
     * How bytecode is modified in memory
     * How this interacts safely with application threads already executing code

   The explanation should make clear how concurrency is handled (e.g., background workers vs application threads) and why this approach is safe and non-disruptive.

---

### Additional Guidance (Annotations)

* Do **not** provide code unless strictly necessary to clarify behavior.
* Focus on **conceptual flow, trade-offs, and reasoning**, not implementation details.
* Assume the audience is familiar with Python, ddtrace, and AppSec concepts, but not with the internal details of this specific system.
* These answers will later be adapted into **slides**, so clarity and structure matter more than exhaustiveness.


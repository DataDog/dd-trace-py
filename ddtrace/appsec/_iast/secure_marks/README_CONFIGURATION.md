# IAST Security Controls Configuration

This document explains how to configure custom security controls for IAST using the `DD_IAST_SECURITY_CONTROLS_CONFIGURATION` environment variable.

## Overview

The `DD_IAST_SECURITY_CONTROLS_CONFIGURATION` environment variable allows you to specify custom sanitizers and validators that IAST should recognize when analyzing your application for security vulnerabilities.

## Format

The configuration uses the following format:

```
CONTROL_TYPE:VULNERABILITY_TYPES:MODULE:METHOD[:PARAMETER_POSITIONS]
```

Multiple security controls are separated by semicolons (`;`).

### Fields

1. **CONTROL_TYPE**: Either `INPUT_VALIDATOR` or `SANITIZER`
2. **VULNERABILITY_TYPES**: Comma-separated list of vulnerability types or `*` for all types
3. **MODULE**: Python module path (e.g., `shlex`, `django.utils.http`)
4. **METHOD**: Method name to instrument
5. **PARAMETER_POSITIONS** (Optional): Zero-based parameter positions to validate (INPUT_VALIDATOR only)

### Vulnerability Types

Supported vulnerability types:
- `COMMAND_INJECTION` / `CMDI`
- `CODE_INJECTION`
- `SQL_INJECTION` / `SQLI`
- `XSS`
- `HEADER_INJECTION`
- `PATH_TRAVERSAL`
- `SSRF`
- `UNVALIDATED_REDIRECT`
- `INSECURE_COOKIE`
- `NO_HTTPONLY_COOKIE`
- `NO_SAMESITE_COOKIE`
- `WEAK_CIPHER`
- `WEAK_HASH`
- `WEAK_RANDOMNESS`
- `STACKTRACE_LEAK`

Use `*` to apply to all vulnerability types.

## Examples

### Basic Examples

#### Input Validator for Command Injection
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote"
```

#### Sanitizer for XSS
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="SANITIZER:XSS:html:escape"
```

#### Multiple Vulnerability Types
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="INPUT_VALIDATOR:COMMAND_INJECTION,XSS:custom.validator:validate_input"
```

#### All Vulnerability Types
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="SANITIZER:*:custom.sanitizer:sanitize_all"
```

### Advanced Examples

#### Multiple Security Controls
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote;SANITIZER:XSS:html:escape;SANITIZER:SQLI:custom.db:escape_sql"
```

#### Validator with Specific Parameter Positions
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="INPUT_VALIDATOR:COMMAND_INJECTION:custom.validator:validate:0,2"
```
This validates only the 1st and 3rd parameters (0-based indexing).

#### Complex Configuration
```bash
export DD_IAST_SECURITY_CONTROLS_CONFIGURATION="INPUT_VALIDATOR:COMMAND_INJECTION,XSS:security.validators:validate_user_input:0,1;SANITIZER:SQLI:database.utils:escape_sql_string;SANITIZER:*:security.sanitizers:clean_all_inputs"
```

## How It Works

### Input Validators
- **Purpose**: Mark input parameters as safe after validation
- **When to use**: When your function validates input and returns a boolean or throws an exception
- **Effect**: Parameters are marked as secure for the specified vulnerability types
- **Parameter positions**: Optionally specify which parameters to mark (0-based index)

### Sanitizers
- **Purpose**: Mark return values as safe after sanitization
- **When to use**: When your function cleans/escapes input and returns the sanitized value
- **Effect**: Return value is marked as secure for the specified vulnerability types

## Integration with Existing Controls

Your custom security controls work alongside the built-in IAST security controls:

- `shlex.quote` (Command injection sanitizer)
- `html.escape` (XSS sanitizer)
- Database escape functions (SQL injection sanitizers)
- Django validators (Various validators)
- And more...

## Error Handling

If there are errors in the configuration:
- Invalid configurations are logged and skipped
- The application continues to run with built-in security controls
- Check application logs for configuration warnings/errors
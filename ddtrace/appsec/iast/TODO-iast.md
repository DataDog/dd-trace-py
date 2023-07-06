# IAST TODO

## Functions from old __init__.py

- `api_add_taint_pyobject`
- `api_taint_pyobject`
- `api_is_pyobject_tainted`
- `api_set_tainted_ranges`
- `api_get_tainted_ranges` -> can be implemented already.
- `api_taint_ranges_as_evidence_info`

## Classes

- `SecureMarks`?
-  Some DTOs?
- `Hdiv/Python/StackTraceElement` & `StackTraceHolder`

## Utils

- `MiscUtils` (only has `parse_params` used only by `AspectSplit`).
- `PyIntHasher` (so simple probably not worth making an util of 
- `StringUtils` (most are variants of `copy_string_new_id` of which we already
  have a variant, but must be checked). 

## Aspects
- `AspectJoin` (finish)
- `AspectAdd` (finish)
- `AspectAppend`
- `AspectExtend`
- `AspectIndex`
- `AspectJsonLoads`
- `AspectOperatorAdd`
- `AspectOperatorMultiply`
- `AspectPartition`
- `AspectSlice`
- `AspectSplit`
- `AspectStringIO`
...etc...
(also migrate existing Hdiv Python-implemented aspects)


## Other
Data structure and mechanism that we had in Hdiv to define what functions would be wrapped
and what would be the wrapping function? Existing data structure in
`core/ast/aspects_spec/spec_fullvar/aspects_spec.py`, used in
`core/ast/ast_visitor/ast_visitor.py`.


## C++ unittests

- `TestStringUtils.cpp`
- `TestAspectOperatorAdd.cpp`
...


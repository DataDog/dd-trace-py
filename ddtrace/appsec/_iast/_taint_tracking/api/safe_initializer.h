#pragma once

#include "initializer/initializer.h"
#include <Python.h>

/**
 * @brief Safely allocate a taint range.
 * @param start The start position of the taint range
 * @param length The length of the taint range
 * @param source The source of the taint
 * @param secure_marks Optional secure marks
 * @return TaintRangePtr or nullptr if not initialized
 */
TaintRangePtr
safe_allocate_taint_range(RANGE_START start,
                          RANGE_LENGTH length,
                          const Source& source,
                          const SecureMarks secure_marks = 0);

/**
 * @brief Safely allocate a copy of a tainted object.
 * @param from The tainted object to copy
 * @return TaintedObjectPtr or nullptr if not initialized
 */
TaintedObjectPtr
safe_allocate_tainted_object_copy(const TaintedObjectPtr& from);

/**
 * @brief Safely allocate a tainted object.
 * @return TaintedObjectPtr or nullptr if not initialized
 */
TaintedObjectPtr
safe_allocate_tainted_object();

/**
 * @brief Safely allocate ranges into a tainted object.
 * @param ranges The ranges to allocate
 * @return TaintedObjectPtr or nullptr if not initialized
 */
TaintedObjectPtr
safe_allocate_ranges_into_taint_object(TaintRangeRefs ranges);

/**
 * @brief Safely allocate a copy of ranges into a tainted object.
 * @param ranges The ranges to copy and allocate
 * @return TaintedObjectPtr or nullptr if not initialized
 */
TaintedObjectPtr
safe_allocate_ranges_into_taint_object_copy(const TaintRangeRefs& ranges);

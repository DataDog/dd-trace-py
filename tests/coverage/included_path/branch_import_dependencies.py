def choose_dependency(flag):
    if flag:
        from tests.coverage.included_path import branch_dep_a

        return branch_dep_a.VALUE

    from tests.coverage.included_path import branch_dep_b

    return branch_dep_b.VALUE

from tests.appsec.iast.aspects.conftest import _iast_patched_module

mod = _iast_patched_module(
    'tests.appsec.iast.fixtures.aspects.str_methods'
)


class TestOperatorAddReplacement(object):
    def test_nostring_operator_add(self):
        # type: () -> None
        assert mod.do_operator_add_params(2, 3) == 5

    # def test_regression_operator_add_re_compile(self, context):  # type: () -> None
    #     try:
    #         mod.do_add_re_compile()
    #     except Exception as e:
    #         pytest.fail(e)
    #
    # def test_string_operator_add_none_tainted(self, context):  # type: () -> None
    #     string_input = 'foo'
    #     bar = 'bar'
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert not get_ranges(result)
    #
    # def test_string_operator_add_one_tainted(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(':+-foo-+:')
    #     bar = 'bar'
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert as_formatted_evidence(result) == ':+-foo-+:bar'
    #
    # def test_string_operator_add_two(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(':+-foo-+:')
    #     bar = create_taint_range_with_format(':+-bar-+:')
    #
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert as_formatted_evidence(result) == ':+-foo-+::+-bar-+:'
    #
    # def test_decoration_when_function_and_decorator_modify_texts_then_tainted(
    #     self,
    #     context
    # ):  # type: () -> None
    #
    #     prefix = self._to_tainted_string_with_origin(':+-<input1>a<input1>-+:')
    #     suffix = self._to_tainted_string_with_origin(':+-<input2>b<input2>-+:')
    #
    #     result = mod.do_add_and_uppercase(prefix, suffix)
    #
    #     assert result == 'AB'
    #
    #     expected_formatted_evidence = ':+-<input1>A<input1>-+::+-<input2>B<input2>-+:'
    #     assert (
    #         as_formatted_evidence(result, tag_mapping_function=None) == expected_formatted_evidence
    #     )
    #
    # @pytest.mark.skipif(sys.version_info >= (3, 0, 0), reason='Python 2 only')
    # def test_string_operator_add_one_tainted_mixed_unicode_bytes(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(u':+-foo-+:')
    #     bar = b'bar'
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert as_formatted_evidence(result) == u':+-foo-+:bar'
    #
    # @pytest.mark.skipif(sys.version_info >= (3, 0, 0), reason='Python 2 only')
    # def test_string_operator_add_two_mixed_unicode_bytes(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(b':+-foo-+:')
    #     bar = create_taint_range_with_format(u':+-bar-+:')
    #
    #     result = mod.<g>(string_input, bar)
    #     assert as_formatted_evidence(result) == u':+-foo-+::+-bar-+:'
    #
    # def test_string_operator_add_one_tainted_mixed_bytearray_bytes(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(b':+-foo-+:')
    #     bar = bytearray('bar', encoding='utf-8')
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert as_formatted_evidence(result) == bytearray(':+-foo-+:bar', encoding='utf-8')
    #
    # def test_string_operator_add_two_mixed_bytearray_bytes(self, context):  # type: () -> None
    #     string_input = create_taint_range_with_format(bytearray(':+-foo-+:', encoding='utf-8'))
    #     bar = create_taint_range_with_format(b':+-bar-+:')
    #
    #     result = mod.do_operator_add_params(string_input, bar)
    #     assert as_formatted_evidence(result) == bytearray(':+-foo-+::+-bar-+:', encoding='utf-8')
